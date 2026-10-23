"""復習スケジューリングのフィクスチャ(R-10)。学習ログは合成で、実受験のログではない。"""

from datetime import UTC, datetime, timedelta

from quiz_sift.domain.attempt import AttemptRecord
from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId, SessionId

DECK = DeckId("aws-saa")
T0 = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


def cluster(local_id: str, *questions: str, deck: DeckId = DECK) -> Cluster:
    return Cluster(ClusterId(deck, local_id), f"{local_id} の要約", tuple(QuestionId(deck, q) for q in questions))


def attempt(question: str, correct: bool, minutes: int, deck: DeckId = DECK) -> AttemptRecord:
    qid = QuestionId(deck, question)
    return AttemptRecord(
        session_id=SessionId(deck, "s1"),
        question_id=qid,
        chosen=frozenset({OptionId(qid, "A" if correct else "B")}),
        correct=correct,
        elapsed_seconds=4.0,
        answered_at=T0 + timedelta(minutes=minutes),
    )
