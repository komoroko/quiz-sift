"""取り込み・抽出・複数セットの受入テスト(C-001 / C-002 / C-011)。claim ごとに1本。

本文はすべて合成で、実教材は含まない(NFR-2)。取り込みの正常系は `tests/ingest/fixtures/healthy.jsonl`
(合成 120 問)と、範囲内の (N, M) 12 組を1問ずつ持つ `tests/fixtures/ingest/n_of_m.jsonl` を使う。
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from quiz_sift.domain.attempt import AttemptRecord
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId, SessionId
from quiz_sift.domain.recall import RecallKey
from quiz_sift.domain.report import IngestRejected, RejectionReason
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.extract.evidence import SelectorKind, selector_kind_is_uniform
from quiz_sift.extract.failures import ExtractField, FetchFailure, FetchFailureReason
from quiz_sift.extract.udemy import FetchedPage, extract
from quiz_sift.extract.writer import write_questions
from quiz_sift.ingest.deck import ingest_deck
from quiz_sift.ingest.reader import IngestFormatError
from quiz_sift.ports.llm import ClassificationResult, GroupProposal, RecallDraft
from quiz_sift.scoring.report import PrePost, Rate, build_report
from quiz_sift.store.deck_scope import DeckScopedStore

from .conftest import CliRunner, StubLlm, StubTts, write_deck

HEALTHY = Path(__file__).resolve().parents[1] / "ingest" / "fixtures" / "healthy.jsonl"
N_OF_M = Path(__file__).resolve().parents[1] / "fixtures" / "ingest" / "n_of_m.jsonl"


# ---- C-001 ----


def _studied(value: str | None) -> StudiedFlag | None:
    return None if value is None else StudiedFlag(value)


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _shape(row: dict[str, Any]) -> tuple[int, int]:
    return len(row["answers"]), len(row["options"])


def test_c001_ingest_keeps_every_field_and_lists_rejections(workspace: Path) -> None:
    source = _rows(HEALTHY) + _rows(N_OF_M)
    assert len(source) >= 100
    # 範囲内の (N, M) 12 組すべてを1問以上含む。
    assert {_shape(row) for row in source} == {(n, m) for m in (4, 5, 6) for n in range(1, m)}
    per_question = {_studied(row.get("studied")) for row in source}
    assert per_question == {StudiedFlag.STUDIED, StudiedFlag.UNSEEN, None}  # 3段の解決を1本で確かめられる入力
    data_root = workspace / "data"

    # 正常系: デッキ既定あり(studied)/ なし の2デッキに同じ行を取り込む。
    for deck_id, default, fallback in (
        (DeckId("with-default"), "studied", StudiedFlag.STUDIED),
        (DeckId("no-default"), None, StudiedFlag.UNKNOWN),
    ):
        write_deck(workspace, deck_id, source, default_studied=default)
        ingested = ingest_deck(data_root, deck_id)
        report = ingested.report

        assert report.rejected == ()
        assert report.accepted == report.input_total == len(source)
        questions = {q.question_id.local_id: q for q in ingested.deck.questions}
        assert list(questions) == [row["id"] for row in source]
        for row in source:
            question = questions[row["id"]]
            assert question.stem == row["stem"].strip()
            assert len(question.options) == len(row["options"])
            assert [(o.option_id.local_id, o.text) for o in question.options] == [
                (o["id"], o["text"].strip()) for o in row["options"]
            ]
            # 正解 N 件が欠落なく、選択肢の並び順で保持される。
            assert [o.local_id for o in question.correct_option_ids] == [
                o["id"] for o in row["options"] if o["id"] in row["answers"]
            ]
            assert question.answer_count == len(row["answers"])
            # 個別 → セット → unknown。個別の unseen はセット既定 studied に上書きされない。
            assert question.studied is (_studied(row.get("studied")) or fallback)

        with_explanation = sum(1 for row in source if (row.get("explanation") or "").strip())
        assert 0 < with_explanation < len(source)
        assert (report.with_explanation, report.without_explanation) == (
            with_explanation,
            len(source) - with_explanation,
        )

    # 不正な問題は1件ずつ、入力上の ID と理由で列挙される。
    broken = [dict(row) for row in source]
    broken[5]["options"] = broken[5]["options"][:3]
    broken[10]["answers"] = ["Z"]
    broken.append(dict(source[20]))
    deck_id = DeckId("broken")
    write_deck(workspace, deck_id, broken)
    report = ingest_deck(data_root, deck_id).report

    assert report.rejected == (
        IngestRejected(source[5]["id"], RejectionReason.OPTION_COUNT_UNSUPPORTED),
        IngestRejected(source[10]["id"], RejectionReason.ANSWER_UNRESOLVED),
        IngestRejected(source[20]["id"], RejectionReason.DUPLICATE_ID),
        IngestRejected(source[20]["id"], RejectionReason.DUPLICATE_ID),
    )
    assert report.input_total == len(broken)
    assert report.accepted == len(source) - 3


def _nm_row(qid: str, option_count: int, answers: list[str]) -> dict[str, Any]:
    return {
        "id": qid,
        "stem": f"合成問題 {qid} の問題文",
        "options": [{"id": label, "text": f"合成問題 {qid} の選択肢 {label}"} for label in "ABCDEFG"[:option_count]],
        "answers": answers,
    }


def test_c001_out_of_range_and_unresolved_answers_are_listed(workspace: Path) -> None:
    rows = [
        _nm_row("ok-4-1", 4, ["B"]),
        _nm_row("m3", 3, ["A"]),
        _nm_row("m7", 7, ["A"]),
        _nm_row("n0", 5, []),
        _nm_row("n-eq-m", 4, ["A", "B", "C", "D"]),
        _nm_row("dup-answer", 5, ["A", "A"]),
        _nm_row("unknown-answer", 6, ["A", "G"]),
        _nm_row("dup-id", 5, ["A", "B"]),
        _nm_row("ok-6-5", 6, ["F", "E", "D", "C", "B"]),
        _nm_row("dup-id", 4, ["C"]),
    ]
    deck_id = DeckId("out-of-range")
    write_deck(workspace, deck_id, rows)
    ingested = ingest_deck(workspace / "data", deck_id)
    report = ingested.report

    assert report.rejected == (
        IngestRejected("m3", RejectionReason.OPTION_COUNT_UNSUPPORTED),
        IngestRejected("m7", RejectionReason.OPTION_COUNT_UNSUPPORTED),
        IngestRejected("n0", RejectionReason.ANSWER_UNRESOLVED),
        IngestRejected("n-eq-m", RejectionReason.ANSWER_UNRESOLVED),
        IngestRejected("dup-answer", RejectionReason.ANSWER_UNRESOLVED),
        IngestRejected("unknown-answer", RejectionReason.ANSWER_UNRESOLVED),
        IngestRejected("dup-id", RejectionReason.DUPLICATE_ID),
        IngestRejected("dup-id", RejectionReason.DUPLICATE_ID),
    )
    assert (report.input_total, report.accepted, len(report.rejected)) == (10, 2, 8)
    assert [(q.question_id.local_id, q.answer_count) for q in ingested.deck.questions] == [("ok-4-1", 1), ("ok-6-5", 5)]


def test_c001_single_answer_input_is_refused(workspace: Path) -> None:
    ok = _nm_row("ok", 4, ["A"])
    legacy = {key: value for key, value in _nm_row("legacy", 4, ["A"]).items() if key != "answers"}
    legacy["answ" + "er"] = "A"
    deck_id = DeckId("legacy")
    write_deck(workspace, deck_id, [ok, legacy])

    with pytest.raises(IngestFormatError, match="未知のキー") as excinfo:
        ingest_deck(workspace / "data", deck_id)
    assert "questions.jsonl:2:" in str(excinfo.value)
    # 新キーと旧キーを両方持つ行も、旧キーを読み替えずに止める。
    write_deck(workspace, deck_id, [ok, {**ok, "id": "both", "answ" + "er": "A"}])
    with pytest.raises(IngestFormatError, match="未知のキー"):
        ingest_deck(workspace / "data", deck_id)
    # 正解を配列でなく単一の値で書いたものも止める。
    write_deck(workspace, deck_id, [ok, {**ok, "id": "scalar", "answers": "A"}])
    with pytest.raises(IngestFormatError, match="answers は文字列の配列"):
        ingest_deck(workspace / "data", deck_id)


# ---- C-002 ----


def _option(text: str, *, correct: bool, selected: bool, multi: bool) -> str:
    state = "answer-correct--PLOEU" if correct else "answer-incorrect--vFyOv" if selected else "answer-skipped--1NDPn"
    if multi:
        icon = "#icon-checkbox-filled" if selected else "#icon-checkbox-empty"
    else:
        icon = ("#icon-success" if correct else "#icon-cancel") if selected else "#icon-radio-empty"
    return (
        f'<div class="answer-result-pane--{state}" data-purpose="answer">'
        '<div class="answer-result-pane--answer-body--cDGY6" data-purpose="answer-body">'
        '<span data-purpose="answer-result-body-selection-icon"><svg>'
        f'<use xlink:href="{icon}"></use></svg></span>'
        f"<div><p>{text}</p></div></div></div>"
    )


def _block(
    number: int,
    *,
    correct: int | tuple[int, ...] | None,
    selected: int | tuple[int, ...] | None = None,
    options: int = 4,
    stem: str | None = None,
    explanation: str | None = None,
    multi: bool = False,
) -> str:
    """`multi` は選択アイコンを複数選択の形(`#icon-checkbox-*`)にする。既定は単一選択の形(`#icon-radio-*`)。"""
    stem = f"合成問題 {number} の問題文" if stem is None else stem
    corrects = {correct} if isinstance(correct, int) else set(correct or ())
    selecteds = {selected} if isinstance(selected, int) else set(selected or ())
    body = "".join(
        _option(f"合成問題 {number} の選択肢 {i}", correct=i in corrects, selected=i in selecteds, multi=multi)
        for i in range(options)
    )
    explained = "" if explanation is None else f'<div id="overall-explanation"><p>{explanation}</p></div>'
    return (
        '<div class="result-pane--question-result-pane--sIcOh">'
        f'<div id="question-prompt"><p>{stem}</p></div>{body}{explained}</div>'
    )


def _page(url: str, *blocks: str) -> FetchedPage:
    return FetchedPage(url, f"<html><body><main><h1>Results</h1>{''.join(blocks)}</main></body></html>")


def test_c002_extraction_reports_counts_and_uniform_evidence() -> None:
    pages: list[FetchedPage | FetchFailure] = [
        _page(
            "https://example.test/result/1",
            _block(1, correct=1, selected=1, explanation="合成の公式解説"),
            _block(2, correct=2, selected=0),  # 受験者は誤答。正解はマークから取る
            _block(3, correct=3),
        ),
        _page(
            "https://example.test/result/2",
            _block(4, correct=0, options=3),
            _block(5, correct=0, stem=""),
            _block(6, correct=0, selected=3, explanation="合成の公式解説 6"),
        ),
        FetchFailure("https://example.test/result/3", FetchFailureReason.AUTH_EXPIRED),
    ]
    result = extract(pages)
    report = result.report

    # 成功件数と失敗箇所。要素の失敗は位置とフィールドで、取得の失敗は URL と理由で別に列挙される。
    assert [q.answer_labels for q in result.questions] == [("B",), ("C",), ("D",), ("A",)]
    assert (report.question_blocks, report.extracted) == (6, 4)
    assert [(f.question_index, f.missing_field) for f in report.extraction_failures] == [
        (3, ExtractField.OPTIONS),
        (4, ExtractField.STEM),
    ]
    assert all(f.element_path for f in report.extraction_failures)
    assert report.fetch_failures == (FetchFailure("https://example.test/result/3", FetchFailureReason.AUTH_EXPIRED),)
    assert report.failed == 3
    assert (report.with_explanation, report.without_explanation) == (2, 2)

    # 根拠の要素種別が全問で同一であることを機械判定する。
    assert {e.selector_kind for e in result.evidences} == {SelectorKind.CORRECT_MARK}
    assert selector_kind_is_uniform(result.evidences)

    # 判定が入力に依存すること: 正解マークの無い問題(受験者の選択にしか根拠が無い)が混ざれば偽になる。
    mixed = extract([*pages, _page("https://example.test/result/4", _block(7, correct=None, selected=2))])
    assert {e.selector_kind for e in mixed.evidences} == {SelectorKind.CORRECT_MARK, SelectorKind.USER_SELECTED}
    assert not selector_kind_is_uniform(mixed.evidences)


def test_c002_marked_answers_become_the_answer_set(workspace: Path) -> None:
    """A-2: 正解マークが N 件ある問題は N 件の正解として抽出され、writer は answers を書く。"""
    page = _page(
        "https://example.test/result/1",
        _block(1, correct=(1, 3), selected=(1,), options=5, multi=True),
        _block(2, correct=(0, 2, 5), selected=(0, 2, 5), options=6, multi=True),
        _block(3, correct=(0, 1), options=4, multi=True),
        _block(4, correct=2, selected=0),
        _block(5, correct=(0, 1, 2, 3, 4), options=6, multi=True),
    )
    result = extract([page])

    assert [(q.local_id, q.answer_labels) for q in result.questions] == [
        ("p001-q001", ("B", "D")),
        ("p001-q002", ("A", "C", "F")),
        ("p001-q003", ("A", "B")),
        ("p001-q004", ("C",)),
        ("p001-q005", ("A", "B", "C", "D", "E")),
    ]
    assert [len(q.options) for q in result.questions] == [5, 6, 4, 4, 6]
    assert {e.selector_kind for e in result.evidences} == {SelectorKind.CORRECT_MARK}

    target = write_deck(workspace, DeckId("extracted"), []) / "questions.jsonl"
    write_questions(target, result.questions, workspace)
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert [row["answers"] for row in rows] == [["B", "D"], ["A", "C", "F"], ["A", "B"], ["C"], ["A", "B", "C", "D", "E"]]
    ingested = ingest_deck(workspace / "data", DeckId("extracted"))
    assert ingested.report.rejected == ()
    assert [q.answer_count for q in ingested.deck.questions] == [2, 3, 2, 1, 5]


def test_c002_user_selected_only_for_single_select() -> None:
    """A-3: マークが無いときの受験者の選択は、単一選択の形の問題でだけ正解にする。"""
    page = _page(
        "https://example.test/result/1",
        _block(1, correct=None, selected=2),  # 単一選択の形: 受験者の選択を正解とする
        _block(2, correct=None, selected=1, multi=True),  # 4択2正解・マーク無し・受験者は1件だけ選んだ
        _block(3, correct=None, selected=(0, 3), options=5, multi=True),
        _block(4, correct=None, selected=1).replace("#icon-radio-empty", "#icon-unknown"),  # 判別できない形
        _block(5, correct=None, selected=1).replace("#icon-radio-empty", "#icon-checkbox-empty", 1),  # 印が混在
    )
    result = extract([page])

    assert [(q.local_id, q.answer_labels) for q in result.questions] == [("p001-q001", ("C",))]
    assert [e.selector_kind for e in result.evidences] == [SelectorKind.USER_SELECTED]
    assert [(f.question_index, f.missing_field) for f in result.report.extraction_failures] == [
        (1, ExtractField.ANSWER),
        (2, ExtractField.ANSWER),
        (3, ExtractField.ANSWER),
        (4, ExtractField.ANSWER),
    ]


# ---- C-011 ----

DECK_A = DeckId("deck-a")
DECK_B = DeckId("deck-b")


def _row(qid: str, stem: str, options: list[str], explanation: str | None, studied: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": qid,
        "stem": stem,
        "options": [{"id": label, "text": text} for label, text in zip("ABCD", options, strict=True)],
        "answers": ["A"],
        "explanation": explanation,
    }
    if studied is not None:
        row["studied"] = studied
    return row


# デッキ A は日本語本文に英語の用語が混ざる。デッキ既定は studied で、a02 だけ個別に unseen。
ROWS_A = [
    _row(
        "a01",
        "Amazon S3 の Lifecycle ルールで、90 日経過したオブジェクトを低コストのアーカイブへ移すにはどうするか。",
        [
            "S3 Glacier Flexible Retrieval へ移行する",
            "EBS スナップショットを取る",
            "CloudFront でキャッシュする",
            "Route 53 で振り分ける",
        ],
        "Lifecycle ルールで S3 Glacier Flexible Retrieval へ移行できる。",
        None,
    ),
    _row(
        "a02",
        "IAM ポリシーで明示的な Deny と Allow が両方ある場合、アクセスはどうなるか。",
        [
            "Deny が優先され拒否される",
            "Allow が優先され許可される",
            "先に書かれた方が優先される",
            "管理者だけ許可される",
        ],
        None,
        "unseen",
    ),
]
# デッキ B は英語。デッキ既定なしで、b01 だけ個別に studied(b02 は unknown)。
ROWS_B = [
    _row(
        "b01",
        "Which service delivers static content from edge locations to reduce latency for global users?",
        ["Amazon CloudFront", "Amazon Redshift", "AWS Glue", "Amazon Athena"],
        "CloudFront caches content at edge locations close to users.",
        "studied",
    ),
    _row(
        "b02",
        "Which feature lets an RDS database fail over automatically to a standby in another Availability Zone?",
        ["Multi-AZ deployment", "Read replica", "Manual snapshot", "Parameter group"],
        None,
        None,
    ),
]
DRAFTS = {
    "a01": RecallDraft(
        RecallKey(("Lifecycle ルール", "アーカイブ")),
        "S3 Glacier Flexible Retrieval へ移行する",
        "Lifecycle ルールの移行先はアーカイブ向けのストレージクラスで、「EBS スナップショット」はボリュームの控え",
    ),
    "a02": RecallDraft(
        RecallKey(("明示的な Deny", "Allow")),
        "Deny が優先され拒否される",
        "明示的な Deny は常に優先され、「先に書かれた方が優先される」のように書いた順では決まらない",
    ),
    "b01": RecallDraft(
        RecallKey(("edge locations", "latency")),
        "Amazon CloudFront",
        'CloudFront serves cached content near users, while "Amazon Redshift" is a data warehouse',
    ),
    "b02": RecallDraft(
        RecallKey(("fail over", "standby")),
        "Multi-AZ deployment",
        'Only Multi-AZ keeps a synchronous standby for automatic failover; a "Read replica" serves reads',
    ),
}
MIXED_ANSWER = "S3 Glacier Flexible Retrieval へ移行する"


@dataclass
class MixingLlm(StubLlm):
    """分類の応答で、別デッキの問題を自デッキの先頭クラスタへ混ぜて返す(偽の割当)。"""

    foreign: dict[DeckId, tuple[QuestionId, ...]] = field(default_factory=dict)

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        result = super().classify_clusters(deck)
        intruders = tuple(qid for other, ids in self.foreign.items() if other != deck.deck_id for qid in ids)
        first, *rest = result.assignments
        return ClassificationResult((GroupProposal(first.label, (*first.question_ids, *intruders)), *rest), ())


def _attempt(deck_id: DeckId, session: str, qid: str, correct: bool) -> AttemptRecord:
    question_id = QuestionId(deck_id, qid)
    chosen = OptionId(question_id, "A" if correct else "B")
    return AttemptRecord(SessionId(deck_id, session), question_id, chosen, correct, 2.0, datetime.now(UTC))


def _rates(pair: PrePost) -> tuple[tuple[int, int], tuple[int, int]]:
    return (pair.pre.correct, pair.pre.answered), (pair.post.correct, pair.post.answered)


def test_c011_two_decks_stay_separate(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_deck(workspace, DECK_A, ROWS_A, language="ja", default_studied="studied")
    write_deck(workspace, DECK_B, ROWS_B, language="en")
    local_ids = {DECK_A: ["a01", "a02"], DECK_B: ["b01", "b02"]}
    llm = MixingLlm(drafts=DRAFTS, foreign={d: tuple(QuestionId(d, q) for q in ids) for d, ids in local_ids.items()})
    tts = StubTts()
    run = CliRunner(workspace, capsys, llm, tts)

    # クラスタリング: 別デッキの問題を混ぜた割当が返っても、各デッキのクラスタは自デッキの問題だけを持つ。
    for deck_id, own in local_ids.items():
        clustered = json.loads(Path(run("cluster", deck_id)).read_text(encoding="utf-8"))
        members = [q for cluster in clustered["clusters"] for q in cluster["questions"]]
        assert sorted(members) == own

    # 音声: 日英混在の本文は分割されずにそのまま TTS へ渡る。デッキごとに言語の既定ボイス。
    run("audio", DECK_A)
    inputs_a = list(tts.inputs)
    run("audio", DECK_B)
    inputs_b = tts.inputs[len(inputs_a) :]
    assert (MIXED_ANSWER, "日语男") in inputs_a
    assert ("Lifecycle ルール、アーカイブ。この組み合わせで選ぶものは?", "日语男") in inputs_a
    assert any(re.search(r"[A-Za-z]", text) and re.search(r"[ぁ-んァ-ヶ一-龠]", text) for text, _ in inputs_a)
    assert {voice for _, voice in inputs_a} == {"日语男"}
    assert {voice for _, voice in inputs_b} == {"英文女"}
    assert ("Amazon CloudFront", "英文女") in inputs_b
    for deck_id in local_ids:
        tracks = sorted(p.name for p in (workspace / "out" / deck_id / "audio").glob("*.mp3"))
        assert tracks == [f"001_{deck_id}_c001.mp3", f"002_{deck_id}_c002.mp3"]

    # 学習記録はデッキごとのストアに入り、読み出しも集計もデッキで閉じる。
    stores = {deck_id: DeckScopedStore(deck_id, workspace) for deck_id in local_ids}
    for record in (
        _attempt(DECK_A, "pre", "a01", False),
        _attempt(DECK_A, "pre", "a02", True),
        _attempt(DECK_A, "post", "a01", True),
        _attempt(DECK_A, "post", "a02", True),
    ):
        stores[DECK_A].record_attempt(record)
    for record in (
        _attempt(DECK_B, "pre", "b01", True),
        _attempt(DECK_B, "pre", "b02", False),
        _attempt(DECK_B, "post", "b01", True),
        _attempt(DECK_B, "post", "b02", False),
    ):
        stores[DECK_B].record_attempt(record)
    with pytest.raises(ValueError):
        stores[DECK_A].record_attempt(_attempt(DECK_B, "pre", "b01", True))

    reviews = {deck_id: Path(run("review", deck_id)) for deck_id in local_ids}
    for deck_id, own in local_ids.items():
        attempts = stores[deck_id].attempts()
        assert len(attempts) == 4
        assert {a.question_id.deck_id for a in attempts} == {deck_id}
        assert sorted({a.question_id.local_id for a in attempts}) == own
        assert {s.cluster_id.deck_id for s in stores[deck_id].cluster_stats()} == {deck_id}
        assert reviews[deck_id] == workspace / "out" / deck_id / "review" / "review.m3u"
    assert [p.name for p in reviews[DECK_A].parent.glob("*.mp3")] == ["001_deck-a_c001.mp3"]
    assert [p.name for p in reviews[DECK_B].parent.glob("*.mp3")] == ["001_deck-b_c002.mp3"]

    # レポートはデッキ単位で、既習フラグは セット既定 → 個別上書き → unknown のとおりに分かれる。
    decks = {deck_id: ingest_deck(workspace / "data", deck_id).deck for deck_id in local_ids}
    assert {q.question_id.local_id: q.studied for q in decks[DECK_A].questions} == {
        "a01": StudiedFlag.STUDIED,
        "a02": StudiedFlag.UNSEEN,
    }
    assert {q.question_id.local_id: q.studied for q in decks[DECK_B].questions} == {
        "b01": StudiedFlag.STUDIED,
        "b02": StudiedFlag.UNKNOWN,
    }
    reports = {
        deck_id: build_report(
            decks[deck_id], stores[deck_id].attempts(), SessionId(deck_id, "pre"), SessionId(deck_id, "post")
        )
        for deck_id in local_ids
    }
    assert _rates(reports[DECK_A].overall) == ((1, 2), (2, 2))
    assert _rates(reports[DECK_A].by_studied[StudiedFlag.STUDIED]) == ((0, 1), (1, 1))
    assert _rates(reports[DECK_A].by_studied[StudiedFlag.UNSEEN]) == ((1, 1), (1, 1))
    assert reports[DECK_A].by_studied[StudiedFlag.UNKNOWN].pre == Rate(0, 0)
    assert _rates(reports[DECK_B].overall) == ((1, 2), (1, 2))
    assert _rates(reports[DECK_B].by_studied[StudiedFlag.STUDIED]) == ((1, 1), (1, 1))
    assert _rates(reports[DECK_B].by_studied[StudiedFlag.UNKNOWN]) == ((0, 1), (0, 1))
    assert reports[DECK_B].by_studied[StudiedFlag.UNSEEN].pre == Rate(0, 0)
    with pytest.raises(ValueError):
        build_report(decks[DECK_A], stores[DECK_B].attempts(), SessionId(DECK_A, "pre"), SessionId(DECK_A, "post"))
