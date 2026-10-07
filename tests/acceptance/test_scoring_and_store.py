"""出題・採点と、UI 経由の学習記録の受入テスト(C-008 / C-012)。claim ごとに1本。

どちらもローカル API(`quiz_sift.server`)を空きポートで別スレッドに立て、実ソケット越しに叩く。
C-012 はその後で `cli.main` を conftest の実行ヘルパ越しに走らせ、API が書いた学習記録を review 段が
読むことを見る。ブラウザの実動作は旧 T-021 の実測ログ(`tests/measurements/`)が担う。
本文はすべて合成で、実教材は含まない(NFR-2)。
"""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from quiz_sift.domain.attempt import AttemptRecord
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId, SessionId
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.ports.llm import RecallDraft
from quiz_sift.server.handler import make_server
from quiz_sift.store.deck_scope import DeckScopedStore
from quiz_sift.store.location import LegacyStoreError, store_path

from .conftest import CliRunner, StubLlm, StubTts, write_deck

T0 = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
STEP = 2.5
"""固定 clock の `monotonic` が1回の読み出しごとに進む秒数。出題→回答で1回ずつ読むので所要時間はこの値になる。"""


class SteppingClock:
    """`monotonic` を読むたびに `STEP` 秒進める固定 clock。"""

    def __init__(self) -> None:
        self._seconds = 100.0
        self._lock = threading.Lock()

    def now(self) -> datetime:
        return T0

    def monotonic(self) -> float:
        with self._lock:
            self._seconds += STEP
            return self._seconds


class Api:
    """実ソケット越しに API を叩く最小のクライアント。1リクエスト1接続。"""

    def __init__(self, port: int, deck_id: str) -> None:
        self.port = port
        self.base = f"/api/decks/{deck_id}"

    def call(self, method: str, path: str, body: object = None) -> tuple[int, Any]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            data = None if body is None else json.dumps(body).encode("utf-8")
            headers = {} if data is None else {"Content-Type": "application/json"}
            conn.request(method, self.base + path, body=data, headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def ok(self, method: str, path: str, body: object = None, expect: int = 200) -> Any:
        status, payload = self.call(method, path, body)
        assert status == expect, (status, payload)
        return payload

    def new_session(self) -> str:
        session: str = self.ok("POST", "/sessions", expect=201)["session_id"]
        return session


@contextmanager
def serving(root: Path, deck_id: str) -> Iterator[Api]:
    server = make_server(root, port=0, clock=SteppingClock())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Api(server.server_port, deck_id)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---- C-008 ----

# (問題, 選択肢, 正解, 既習フラグ)。M は 4〜6、N は 1〜3 で、N > M−N の組(q6: 3-of-5)も置く(D-202)。
# 既習フラグは3値すべてを2問ずつ置き、`unknown` は行に書けない値なので `studied` キーを省いて表す。
SPEC = (
    ("q1", "ABCD", "A", "studied"),
    ("q2", "ABCDE", "BD", "studied"),
    ("q3", "ABCD", "C", "unseen"),
    ("q4", "ABCDEF", "ACF", "unseen"),
    ("q5", "ABCD", "B", None),
    ("q6", "ABCDE", "ACE", None),
)
OPTIONS = {qid: labels for qid, labels, _, _ in SPEC}
ANSWER = {qid: answers for qid, _, answers, _ in SPEC}


def _scoring_row(qid: str, labels: str, answers: str, studied: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": qid,
        "stem": f"合成問題 {qid}: 正しいものをすべて選べ。",
        "options": [{"id": label, "text": f"{qid} の選択肢 {label}"} for label in labels],
        "answers": list(answers),
        "explanation": f"{qid} の解説",
    }
    if studied is not None:
        row["studied"] = studied
    return row


def _correct_texts(qid: str) -> set[str]:
    return {f"{qid} の選択肢 {label}" for label in ANSWER[qid]}


def _take(api: Api, session: str, *, correct: bool, by_content: bool) -> dict[str, tuple[str, ...]]:
    """全問に答え、問題ごとの表示順(選択肢 ID の並び)を返す。

    `by_content` なら表示された本文で正解を同定し、そうでなければ ID で同定する。表示位置は使わない。
    誤答は正解の集合のうち1件を誤答選択肢1件に入れ替えた N 件の集合(D-202)。
    """
    orders: dict[str, tuple[str, ...]] = {}
    for qid in ANSWER:
        presented = api.ok("GET", f"/sessions/{session}/questions/{qid}")
        assert presented["select_count"] == len(ANSWER[qid])
        options = presented["options"]
        orders[qid] = tuple(option["option_id"] for option in options)
        assert sorted(orders[qid]) == list(OPTIONS[qid])
        if by_content:
            is_correct = [option["text"] in _correct_texts(qid) for option in options]
        else:
            is_correct = [option["option_id"] in ANSWER[qid] for option in options]
        assert is_correct.count(True) == len(ANSWER[qid])
        right = [o["option_id"] for o, hit in zip(options, is_correct, strict=True) if hit]
        first_wrong = next(o["option_id"] for o, hit in zip(options, is_correct, strict=True) if not hit)
        chosen = right if correct else [*right[1:], first_wrong]
        graded = api.ok("POST", f"/sessions/{session}/answers", {"question_id": qid, "chosen": chosen}, expect=201)
        assert graded["correct"] is correct
        assert graded["correct_option_ids"] == list(ANSWER[qid])
    return orders


def _positional_grade(sessions: list[dict[str, tuple[str, ...]]]) -> float:
    """表示位置で採点する偽の採点器に全問正解の入力を与えたときの正答率。

    選んだ選択肢の表示位置がすべて、シャッフル前の正解の位置と一致すれば正解とする採点器である。
    """
    hits = [
        all(orders[q].index(label) == OPTIONS[q].index(label) for label in ANSWER[q])
        for orders in sessions
        for q in orders
    ]
    return sum(hits) / len(hits)


def _report(api: Api, pre: str, post: str) -> dict[str, Any]:
    report: dict[str, Any] = api.ok("GET", f"/report?pre={pre}&post={post}")
    return report


def test_c008_grading_under_shuffle_and_report(workspace: Path) -> None:
    deck_id = DeckId("scoring")
    write_deck(workspace, deck_id, [_scoring_row(*spec) for spec in SPEC])

    with serving(workspace, deck_id) as api:
        # 全問誤答(ID で同定)→ 全問正解(内容で同定)、全問誤答(内容)→ 全問正解(ID)の2組。
        wrong_by_id = api.new_session()
        wrong_by_id_orders = _take(api, wrong_by_id, correct=False, by_content=False)
        right_by_content = api.new_session()
        right_by_content_orders = _take(api, right_by_content, correct=True, by_content=True)
        wrong_by_content = api.new_session()
        _take(api, wrong_by_content, correct=False, by_content=True)
        right_by_id = api.new_session()
        right_by_id_orders = _take(api, right_by_id, correct=True, by_content=False)

        reports = [_report(api, wrong_by_id, right_by_content), _report(api, wrong_by_content, right_by_id)]

    # シャッフルは問題ごとに異なり、同じ問題でもセッションごとに異なる。
    for orders in (wrong_by_id_orders, right_by_content_orders, right_by_id_orders):
        assert any(order != tuple(OPTIONS[q]) for q, order in orders.items())
    assert any(wrong_by_id_orders[q] != right_by_content_orders[q] for q in ANSWER)
    # 表示位置で採点する採点器なら、全問正解の入力が 100% にならない(このシャッフルが採点器を見分ける)。
    assert _positional_grade([wrong_by_id_orders, right_by_content_orders, right_by_id_orders]) < 1.0

    # 全問正解は 100%、全問誤答は 0%。既習フラグの3値それぞれに分けて出る。
    for report in reports:
        assert report["overall"]["pre"] == {"correct": 0, "answered": 6, "ratio": 0.0}
        assert report["overall"]["post"] == {"correct": 6, "answered": 6, "ratio": 1.0}
        assert report["overall"]["delta"] == 1.0
        assert set(report["by_studied"]) == {"studied", "unseen", "unknown"}
        for pair in report["by_studied"].values():
            assert pair["pre"] == {"correct": 0, "answered": 2, "ratio": 0.0}
            assert pair["post"] == {"correct": 2, "answered": 2, "ratio": 1.0}
        # 限界開示は hold-out 不在とテスト効果の両方に触れ、出力本文に必ず入る。
        assert "hold-out" in report["limits"]
        assert "テスト効果" in report["limits"]
        assert report["limits"] in report["markdown"]
        for flag in ("studied", "unseen", "unknown"):
            assert f"| {flag} | 0.0% (0/2) | 100.0% (2/2) | +100.0pt |" in report["markdown"]

    # 選んだ集合・正誤・所要時間が学習記録に残り、サーバを止めて開き直しても読める。
    store = DeckScopedStore(deck_id, workspace)
    for session, correct in ((wrong_by_id, False), (right_by_content, True), (right_by_id, True)):
        attempts = store.attempts(SessionId(deck_id, session))
        assert [a.question_id.local_id for a in attempts] == list(ANSWER)
        assert all(a.correct is correct for a in attempts)
        for a in attempts:
            chosen = {option.local_id for option in a.chosen}
            assert len(chosen) == len(ANSWER[a.question_id.local_id])
            assert (chosen == set(ANSWER[a.question_id.local_id])) is correct
        assert all(a.elapsed_seconds == STEP for a in attempts)
        assert all(a.answered_at == T0 for a in attempts)
    with serving(workspace, deck_id) as restarted:
        sessions = restarted.ok("GET", "/sessions")
        assert sessions == [
            {"session_id": s, "answered": 6} for s in (wrong_by_id, right_by_content, wrong_by_content, right_by_id)
        ]


def test_c008_api_refuses_anything_but_n_choices(workspace: Path) -> None:
    deck_id = DeckId("choices")
    write_deck(workspace, deck_id, [_scoring_row(*spec) for spec in SPEC])

    with serving(workspace, deck_id) as api:
        session = api.new_session()
        # present は選ぶ数 N を返す。
        for qid in ANSWER:
            assert api.ok("GET", f"/sessions/{session}/questions/{qid}")["select_count"] == len(ANSWER[qid])

        # q2 は 2-of-5(正解 B, D)。型違い・重複・N 件以外・未知 ID はどれも 400 で、記録されない。
        refused: tuple[object, ...] = (
            0,  # 表示位置(整数)
            "B",  # 配列でない文字列
            None,  # chosen が無い
            {"B": True},
            ["B", 3],  # 要素の型違い
            ["B", "B"],  # 重複
            [],
            ["B"],  # N − 1 件
            ["B", "D", "A"],  # N + 1 件
            ["B", "Z"],  # 未知 ID
        )
        for chosen in refused:
            status, payload = api.call("POST", f"/sessions/{session}/answers", {"question_id": "q2", "chosen": chosen})
            assert status == 400, (chosen, status, payload)
        assert store_path(workspace).exists()
        assert DeckScopedStore(deck_id, workspace).attempts() == ()

        # 拒んだ回答は出題を消費しない。ちょうど N 件なら、並びによらず集合として採点する。
        graded = api.ok("POST", f"/sessions/{session}/answers", {"question_id": "q2", "chosen": ["D", "B"]}, expect=201)
        assert graded == {"correct": True, "correct_option_ids": ["B", "D"], "explanation": "q2 の解説"}
        graded = api.ok("POST", f"/sessions/{session}/answers", {"question_id": "q1", "chosen": ["B"]}, expect=201)
        assert graded == {"correct": False, "correct_option_ids": ["A"], "explanation": "q1 の解説"}

    recorded = DeckScopedStore(deck_id, workspace).attempts()
    assert [(a.question_id.local_id, sorted(o.local_id for o in a.chosen), a.correct) for a in recorded] == [
        ("q2", ["B", "D"], True),
        ("q1", ["B"], False),
    ]


LEGACY_SCHEMA = """
CREATE TABLE attempts (
    deck_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    chosen_option_id TEXT NOT NULL,
    correct INTEGER NOT NULL,
    elapsed_seconds REAL NOT NULL,
    answered_at TEXT NOT NULL
);
INSERT INTO attempts VALUES ('legacy', 's1', 'q1', 'A', 1, 2.0, '2026-09-20T09:00:00+00:00');
"""
"""v1(ADR-005)の形の学習記録。`user_version` は書かれていない(0)。"""


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_c008_legacy_store_stops_without_rewriting(workspace: Path) -> None:
    deck_id = DeckId("legacy")

    # 新しく作るストアは v2: `user_version = 2` で、選んだ集合を `chosen_option_ids` に持つ。
    fresh = workspace / "fresh"
    write_deck(fresh, deck_id, [_scoring_row(*spec) for spec in SPEC])
    with serving(fresh, deck_id) as api:
        session = api.new_session()
        api.ok("GET", f"/sessions/{session}/questions/q4")
        api.ok("POST", f"/sessions/{session}/answers", {"question_id": "q4", "chosen": ["F", "A", "C"]}, expect=201)
    conn = sqlite3.connect(store_path(fresh))
    try:
        assert conn.execute("PRAGMA user_version").fetchone() == (2,)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
        assert "chosen_option_ids" in columns and "chosen_option_id" not in columns
        assert conn.execute("SELECT chosen_option_ids, correct FROM attempts").fetchall() == [('["A", "C", "F"]', 1)]
    finally:
        conn.close()

    # 同じ所在に旧形式(テーブルあり・版が 2 でない)があれば、読み替えも作り直しもせずに止まる。
    for version in (0, 1):
        root = workspace / f"v{version}"
        path = store_path(root)
        path.parent.mkdir(parents=True)
        conn = sqlite3.connect(path)
        try:
            conn.executescript(LEGACY_SCHEMA + f"PRAGMA user_version = {version};")
        finally:
            conn.close()
        before = _files(root)

        store = DeckScopedStore(deck_id, root)
        with pytest.raises(LegacyStoreError) as raised:
            store.attempts()
        message = str(raised.value)
        assert str(path.resolve()) in message
        assert f"user_version = {version}" in message
        assert "退避" in message
        with pytest.raises(LegacyStoreError):
            store.record_attempt(
                AttemptRecord(
                    SessionId(deck_id, "s2"),
                    QuestionId(deck_id, "q1"),
                    frozenset({OptionId(QuestionId(deck_id, "q1"), "A")}),
                    True,
                    1.0,
                    T0,
                )
            )

        # ストアは消えず、1バイトも書き換わらず、別のファイルも作られない。
        assert _files(root) == before
        conn = sqlite3.connect(path)
        try:
            assert conn.execute("SELECT deck_id, chosen_option_id FROM attempts").fetchall() == [("legacy", "A")]
        finally:
            conn.close()


# ---- C-012 ----

QUESTIONS = 4


def _qid(n: int) -> str:
    return f"q{n:02d}"


def _store_row(n: int) -> dict[str, Any]:
    word = f"語{n:02d}番"
    return {
        "id": _qid(n),
        "stem": f"合成問題: {word}に対応する値はどれか。",
        "options": [{"id": label, "text": f"値{n:02d}{label}"} for label in "ABCD"],
        "answers": ["A"],
        "explanation": f"{word}には値{n:02d}Aが対応する。",
    }


STORE_DECK = DeckId("store")


def _store_draft(n: int) -> RecallDraft:
    question_id = QuestionId(STORE_DECK, _qid(n))
    return RecallDraft(
        RecallKey((f"語{n:02d}番",)),
        (AnswerElement(OptionId(question_id, "A"), f"値{n:02d}A"),),
        DiscriminationPoint(
            f"この語には先頭の値が結びつき、「値{n:02d}B」は別の語に結びつく", (OptionId(question_id, "B"),)
        ),
    )


DRAFTS = {_qid(n): _store_draft(n) for n in range(1, QUESTIONS + 1)}


def _m3u_entries(m3u: Path) -> list[str]:
    lines = m3u.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line and not line.startswith("#")]


def test_c012_answers_persist_server_side_and_cli_reads_them(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deck_id = STORE_DECK
    write_deck(workspace, deck_id, [_store_row(n) for n in range(1, QUESTIONS + 1)])

    # 誤答率: q02 = 2/2、q03 = 1/2、q01 と q04 = 0/2。
    wrong = {"first": {"q02", "q03"}, "second": {"q02"}}
    with serving(workspace, deck_id) as api:
        sessions = {}
        for name, missed in wrong.items():
            session = api.new_session()
            sessions[name] = session
            for n in range(1, QUESTIONS + 1):
                qid = _qid(n)
                api.ok("GET", f"/sessions/{session}/questions/{qid}")
                chosen = ["B"] if qid in missed else ["A"]
                api.ok("POST", f"/sessions/{session}/answers", {"question_id": qid, "chosen": chosen}, expect=201)

    # 回答はサーバ側の学習記録ストアに書かれ、所在は store/location.py の定義する1ファイルだけ。
    path = store_path(workspace)
    assert path.is_file()
    assert sorted(workspace.rglob("*.sqlite3")) == [path]
    recorded = DeckScopedStore(deck_id, workspace).attempts()
    assert [(a.session_id.local_id, a.question_id.local_id, a.correct) for a in recorded] == [
        (sessions[name], _qid(n), _qid(n) not in missed)
        for name, missed in wrong.items()
        for n in range(1, QUESTIONS + 1)
    ]

    # 同じストアを CLI の review 段が読み、API で書いた誤答から誤答率降順の復習系列を作る。
    run = CliRunner(workspace, capsys, StubLlm(drafts=DRAFTS), StubTts())
    clusters = json.loads(Path(run("cluster", deck_id)).read_text(encoding="utf-8"))["clusters"]
    owner = {q: c["id"] for c in clusters for q in c["questions"]}
    run("audio", deck_id)
    m3u = Path(run("review", deck_id))
    assert m3u == workspace / "out" / deck_id / "review" / "review.m3u"
    assert _m3u_entries(m3u) == [f"{seq:03d}_{deck_id}_{owner[q]}.mp3" for seq, q in enumerate(("q02", "q03"), 1)]

    # review 段が書いたクラスタ集計も同じストアにあり、サーバを立て直した API からも同じ回答が見える。
    stats = {s.cluster_id.local_id: (s.wrong, s.presented) for s in DeckScopedStore(deck_id, workspace).cluster_stats()}
    assert stats == {owner["q01"]: (0, 2), owner["q02"]: (2, 2), owner["q03"]: (1, 2), owner["q04"]: (0, 2)}
    with serving(workspace, deck_id) as restarted:
        assert restarted.ok("GET", "/sessions") == [
            {"session_id": sessions[name], "answered": QUESTIONS} for name in wrong
        ]
    assert sorted(workspace.rglob("*.sqlite3")) == [path]
