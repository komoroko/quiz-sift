"""2デッキ分の学習記録フィクスチャ。

2つのデッキはセッション・問題・選択肢・クラスタの local_id をわざと同じにしてある。
`deck_id` を落としたクエリはここで必ず相手のデッキの行を拾う。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from quiz_sift.domain.attempt import AttemptRecord, ClusterStat
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId, SessionId

DECK_A = DeckId("aws-saa")
DECK_B = DeckId("gcp-ace")


@dataclass(frozen=True)
class DeckLog:
    deck_id: DeckId
    attempts: tuple[AttemptRecord, ...]
    cluster_stats: tuple[ClusterStat, ...]


def _log(deck_id: DeckId, correct_in_s1: bool, tz: timezone) -> DeckLog:
    start = datetime(2026, 9, 20, 9, 0, tzinfo=tz)
    attempts = []
    for session_n, session in enumerate(("s1", "s2")):
        for question_n, (question, chosen) in enumerate((("q1", "A"), ("q2", "C"))):
            question_id = QuestionId(deck_id, question)
            attempts.append(
                AttemptRecord(
                    session_id=SessionId(deck_id, session),
                    question_id=question_id,
                    chosen=OptionId(question_id, chosen),
                    correct=correct_in_s1 if session == "s1" else not correct_in_s1,
                    elapsed_seconds=3.25 + question_n,
                    answered_at=start + timedelta(days=session_n, seconds=30 * question_n),
                )
            )
    stats = (
        ClusterStat(ClusterId(deck_id, "c1"), presented=4, wrong=1 if correct_in_s1 else 3, last_studied_at=start),
        ClusterStat(ClusterId(deck_id, "c2"), presented=0, wrong=0, last_studied_at=None),
    )
    return DeckLog(deck_id, tuple(attempts), stats)


@pytest.fixture
def two_decks() -> tuple[DeckLog, DeckLog]:
    # タイムゾーンもデッキで変え、読み戻しがオフセットを保つことを見る。
    return _log(DECK_A, True, UTC), _log(DECK_B, False, timezone(timedelta(hours=9)))
