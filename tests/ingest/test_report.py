"""IngestReport の保存則と解説の有無の件数(受入基準 A-1 / A-5)。"""

import pytest

from quiz_sift.domain.report import IngestRejected, IngestReport, RejectionReason
from quiz_sift.ingest.deck import ingest_deck

from .conftest import DECK, WriteDeck, question_row


def test_conservation_is_a_construction_invariant(write_deck: WriteDeck) -> None:
    """受入基準 A-1: accepted + rejected == input_total を壊す値では IngestReport を構築できない。"""
    one_rejected = (IngestRejected("q9", RejectionReason.DUPLICATE_ID),)
    IngestReport(input_total=3, accepted=2, rejected=one_rejected, with_explanation=1, without_explanation=1)

    # accepted と rejected を別々に数えて後から足し合わせる実装は、ずれた瞬間に構築で落ちる。
    with pytest.raises(ValueError, match="保存則"):
        IngestReport(input_total=3, accepted=3, rejected=one_rejected, with_explanation=2, without_explanation=1)
    with pytest.raises(ValueError, match="保存則"):
        IngestReport(input_total=4, accepted=2, rejected=one_rejected, with_explanation=1, without_explanation=1)

    # 取り込み経路が作るレポートも同じ不変条件の上にある。除外の出る入力で、入力件数と一致する。
    data_root = write_deck(
        {
            "a.jsonl": [question_row("q1"), question_row("q2", answers=["Z"]), question_row("q3")],
            "b.jsonl": [question_row("q3"), question_row("q4", options=[{"id": "A", "text": "唯一"}])],
        }
    )
    report = ingest_deck(data_root, DECK).report
    assert report.input_total == 5
    assert report.accepted + len(report.rejected) == report.input_total
    assert report.accepted == 1


def test_explanation_presence_is_counted(write_deck: WriteDeck) -> None:
    """受入基準 A-5: 公式解説を持つ問題数と持たない問題数を報告する。"""
    data_root = write_deck(
        {
            "q.jsonl": [
                question_row("q1", explanation="S3 はオブジェクトストレージである。"),
                question_row("q2", explanation="Lambda はサーバーレスである。"),
                question_row("q3", explanation=None),
                question_row("q4"),
                # 空白だけの解説は「解説あり」に数えない。水増しすると NFR-1 の照合先を過大に見積もる。
                question_row("q5", explanation="   "),
                # 除外された問題は、解説を持っていても数えない(内訳は accepted の内訳)。
                question_row("q6", explanation="除外される問題の解説", answers=[]),
            ]
        }
    )
    ingested = ingest_deck(data_root, DECK)
    assert ingested.report.accepted == 5
    assert ingested.report.with_explanation == 2
    assert ingested.report.without_explanation == 3
    explained = {q.question_id.local_id for q in ingested.deck.questions if q.explanation is not None}
    assert explained == {"q1", "q2"}
