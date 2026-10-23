"""不正な問題の個別列挙と、正常系で何も捨てないこと(受入基準 A-2 / A-3)。"""

import json
import shutil
from pathlib import Path

import pytest

from quiz_sift.domain.ids import OptionId, QuestionId
from quiz_sift.domain.report import IngestRejected, RejectionReason
from quiz_sift.ingest.deck import ingest_deck
from quiz_sift.ingest.reader import IngestFormatError, parse_jsonl

from .conftest import DECK, FIXTURES, WriteDeck, question_row


def test_healthy_fixture_excludes_nothing(write_deck: WriteDeck) -> None:
    """受入基準 A-2: 全問が妥当な正常系フィクスチャ(120問)に対して除外件数が 0。

    件数だけでなく、全問の問題文・選択肢4件・正解が入力どおりに保持されていることも見る。
    """
    data_root = write_deck({})
    shutil.copy(FIXTURES / "healthy.jsonl", data_root / DECK / "healthy.jsonl")
    source = [json.loads(line) for line in (FIXTURES / "healthy.jsonl").read_text(encoding="utf-8").splitlines()]
    assert 100 <= len(source) <= 200

    ingested = ingest_deck(data_root, DECK)

    assert ingested.report.rejected == ()
    assert ingested.report.accepted == ingested.report.input_total == len(source)
    assert ingested.duplicates.pairs == ()
    for row, question in zip(source, ingested.deck.questions, strict=True):
        question_id = QuestionId(DECK, row["id"])
        assert question.question_id == question_id
        assert question.stem == row["stem"]
        assert len(question.options) == 4
        assert [(o.option_id, o.text) for o in question.options] == [
            (OptionId(question_id, o["id"]), o["text"]) for o in row["options"]
        ]
        assert question.correct_option_ids == tuple(OptionId(question_id, oid) for oid in row["answers"])


def test_each_rejection_reason_is_listed(write_deck: WriteDeck) -> None:
    """受入基準 A-3: 選択肢数が範囲外 / 正解が特定できない / ID 重複 が理由つきで個別に列挙される。"""
    seven = [{"id": label, "text": f"選択肢 {label}"} for label in "ABCDEFG"]
    data_root = write_deck(
        {
            "a.jsonl": [
                question_row("ok1"),
                question_row("three", options=question_row("x")["options"][:3]),
                question_row("seven", options=seven),
                question_row("no-answer", answers=[]),
                question_row("blank-answer", answers=["  "]),
                question_row("foreign-answer", answers=["E"]),
                question_row("dup-answer", answers=["B", "B"]),
                question_row("all-answers", answers=["A", "B", "C", "D"]),
                question_row("reordered", answers=["D", "A"]),
                question_row("dup"),
                question_row("ok2"),
            ],
            # ID の重複はファイルをまたいでも見る。どちらが正しいかは決まらないので両方を除外する。
            "b.jsonl": [question_row("dup"), question_row("ok3")],
        }
    )
    ingested = ingest_deck(data_root, DECK)
    report = ingested.report

    assert report.rejected == (
        IngestRejected("three", RejectionReason.OPTION_COUNT_UNSUPPORTED),
        IngestRejected("seven", RejectionReason.OPTION_COUNT_UNSUPPORTED),
        IngestRejected("no-answer", RejectionReason.ANSWER_UNRESOLVED),
        IngestRejected("blank-answer", RejectionReason.ANSWER_UNRESOLVED),
        IngestRejected("foreign-answer", RejectionReason.ANSWER_UNRESOLVED),
        IngestRejected("dup-answer", RejectionReason.ANSWER_UNRESOLVED),
        IngestRejected("all-answers", RejectionReason.ANSWER_UNRESOLVED),
        IngestRejected("dup", RejectionReason.DUPLICATE_ID),
        IngestRejected("dup", RejectionReason.DUPLICATE_ID),
    )
    assert report.accepted == 4
    assert report.input_total == 13
    # 正解は入力の並びに依らず選択肢の並び順で持つ。
    reordered = next(q for q in ingested.deck.questions if q.question_id.local_id == "reordered")
    assert [o.local_id for o in reordered.correct_option_ids] == ["A", "D"]


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ('{"id": "q1", "stem": "秘密の問題文"', "JSON として読めない"),
        (json.dumps(question_row("q1", answr="B")), "未知のキー"),
        (json.dumps(question_row("q1", **{"answ" + "er": "B"})), "未知のキー"),
        (json.dumps(question_row("q1", answers=None)), "answers は文字列の配列"),
        (json.dumps(question_row("q1", answers=["B", 1])), "answers は文字列の配列"),
        (json.dumps(question_row("q1", stem=...)), "必須のキーが無い"),
        (json.dumps(question_row("q1", stem="  ")), "stem は空でない文字列"),
        (json.dumps(question_row("q1", studied="unknown")), "studied は"),
        (json.dumps(question_row("q1", options=[{"id": "A", "text": "x"}, {"id": "A", "text": "y"}])), "重複"),
    ],
)
def test_a_malformed_line_stops_the_ingest_with_its_location(line: str, message: str) -> None:
    """形の壊れた行は読み飛ばさない。読み飛ばすと input_total から消え、保存則が素通りする。"""
    text = "\n".join([json.dumps(question_row("q0")), "", line])
    with pytest.raises(IngestFormatError, match=message) as excinfo:
        parse_jsonl(text, "data/aws-saa/q.jsonl")
    assert "data/aws-saa/q.jsonl:3:" in str(excinfo.value)
    # 教材本文をエラーメッセージに載せない(NFR-2)。
    assert "秘密の問題文" not in str(excinfo.value)
    assert "問題 q1" not in str(excinfo.value)


def test_blank_lines_are_not_inputs(tmp_path: Path) -> None:
    """空行は入力件数に数えない。末尾改行のたびに input_total が増えると保存則の意味が変わる。"""
    text = "\n" + json.dumps(question_row("q1")) + "\n\n  \n"
    assert [record.raw_id for record in parse_jsonl(text, "q.jsonl")] == ["q1"]
