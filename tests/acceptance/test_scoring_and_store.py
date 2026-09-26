"""出題・採点と、UI 経由の学習記録の受入テスト(C-008 / C-012)。claim ごとに1本。

どちらもローカル API(`quiz_sift.server`)を空きポートで別スレッドに立て、実ソケット越しに叩く。
C-012 はその後で `cli.main` を conftest の実行ヘルパ越しに走らせ、API が書いた学習記録を review 段が
読むことを見る。ブラウザの実動作は旧 T-021 の実測ログ(`tests/measurements/`)が担う。
本文はすべて合成で、実教材は含まない(NFR-2)。
"""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from quiz_sift.domain.ids import DeckId, SessionId
from quiz_sift.domain.recall import RecallKey
from quiz_sift.ports.llm import RecallDraft
from quiz_sift.server.handler import make_server
from quiz_sift.store.deck_scope import DeckScopedStore
from quiz_sift.store.location import store_path

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

# (問題, 正解, 既習フラグ)。正解は A〜D に散らし、既習フラグは3値すべてを2問ずつ置く。
# `unknown` は行に書けない値なので、`studied` キーを省いて表す。
SPEC = (
    ("q1", "A", "studied"),
    ("q2", "B", "studied"),
    ("q3", "C", "unseen"),
    ("q4", "D", "unseen"),
    ("q5", "B", None),
    ("q6", "C", None),
)
ANSWER = {qid: answer for qid, answer, _ in SPEC}
ORIGINAL_ORDER = "ABCD"


def _scoring_row(qid: str, answer: str, studied: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": qid,
        "stem": f"合成問題 {qid}: 正しいものはどれか。",
        "options": [{"id": label, "text": f"{qid} の選択肢 {label}"} for label in ORIGINAL_ORDER],
        "answer": answer,
        "explanation": f"{qid} の解説",
    }
    if studied is not None:
        row["studied"] = studied
    return row


def _correct_text(qid: str) -> str:
    return f"{qid} の選択肢 {ANSWER[qid]}"


def _take(api: Api, session: str, *, correct: bool, by_content: bool) -> dict[str, tuple[str, ...]]:
    """全問に答え、問題ごとの表示順(選択肢 ID の並び)を返す。

    `by_content` なら表示された本文で正解を同定し、そうでなければ ID で同定する。表示位置は使わない。
    """
    orders: dict[str, tuple[str, ...]] = {}
    for qid, _, _ in SPEC:
        presented = api.ok("GET", f"/sessions/{session}/questions/{qid}")
        options = presented["options"]
        orders[qid] = tuple(option["option_id"] for option in options)
        assert sorted(orders[qid]) == list(ORIGINAL_ORDER)
        if by_content:
            is_correct = [option["text"] == _correct_text(qid) for option in options]
        else:
            is_correct = [option["option_id"] == ANSWER[qid] for option in options]
        assert is_correct.count(True) == 1
        chosen = next(o["option_id"] for o, hit in zip(options, is_correct, strict=True) if hit is correct)
        graded = api.ok("POST", f"/sessions/{session}/answers", {"question_id": qid, "chosen": chosen}, expect=201)
        assert graded["correct"] is correct
        assert graded["correct_option_id"] == ANSWER[qid]
    return orders


def _positional_grade(sessions: list[dict[str, tuple[str, ...]]]) -> float:
    """表示位置で採点する偽の採点器に全問正解の入力を与えたときの正答率。

    選んだ選択肢の表示位置が、シャッフル前の正解の位置と一致すれば正解とする採点器である。
    """
    hits = [orders[q].index(ANSWER[q]) == ORIGINAL_ORDER.index(ANSWER[q]) for orders in sessions for q in orders]
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

        # 表示位置(整数)では回答できない。
        extra = api.new_session()
        api.ok("GET", f"/sessions/{extra}/questions/q1")
        status, _ = api.call("POST", f"/sessions/{extra}/answers", {"question_id": "q1", "chosen": 0})
        assert status == 400

        reports = [_report(api, wrong_by_id, right_by_content), _report(api, wrong_by_content, right_by_id)]

    # シャッフルは問題ごとに異なり、同じ問題でもセッションごとに異なる。
    for orders in (wrong_by_id_orders, right_by_content_orders, right_by_id_orders):
        assert len(set(orders.values())) > 1
        assert any(order != tuple(ORIGINAL_ORDER) for order in orders.values())
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

    # 選択・正誤・所要時間が学習記録に残り、サーバを止めて開き直しても読める。
    store = DeckScopedStore(deck_id, workspace)
    for session, correct in ((wrong_by_id, False), (right_by_content, True), (right_by_id, True)):
        attempts = store.attempts(SessionId(deck_id, session))
        assert [a.question_id.local_id for a in attempts] == list(ANSWER)
        assert all(a.correct is correct for a in attempts)
        assert all((a.chosen.local_id == ANSWER[a.question_id.local_id]) is correct for a in attempts)
        assert all(a.elapsed_seconds == STEP for a in attempts)
        assert all(a.answered_at == T0 for a in attempts)
    with serving(workspace, deck_id) as restarted:
        sessions = restarted.ok("GET", "/sessions")
        assert sessions == [
            {"session_id": s, "answered": 6} for s in (wrong_by_id, right_by_content, wrong_by_content, right_by_id)
        ]


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
        "answer": "A",
        "explanation": f"{word}には値{n:02d}Aが対応する。",
    }


DRAFTS = {
    _qid(n): RecallDraft(
        RecallKey((f"語{n:02d}番",)),
        f"値{n:02d}A",
        f"この語には先頭の値が結びつき、「値{n:02d}B」は別の語に結びつく",
    )
    for n in range(1, QUESTIONS + 1)
}


def _m3u_entries(m3u: Path) -> list[str]:
    lines = m3u.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line and not line.startswith("#")]


def test_c012_answers_persist_server_side_and_cli_reads_them(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deck_id = DeckId("store")
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
                chosen = "B" if qid in missed else "A"
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
