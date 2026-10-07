"""出題セッション(R-8)。

出題は `PresentedQuestion` を返し、その `presentation` は選択肢 ID の並びである。回答の受け口は
`Quizmaster.submit(session_id, question_id, chosen: frozenset[OptionId])` だけで、表示位置を受け取る API は置かない。

シャッフルの seed は「セッション ID × 問題 ID」から導出する(AR-7)。問題 ID だけから導出すると、
同じ問題はどのセッションでも同じ順列になり、事前テストで覚えた位置が事後テストでもそのまま当たる。
"""

import hashlib
import random
from dataclasses import dataclass

from quiz_sift.domain.attempt import AttemptRecord
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import OptionId, QuestionId, SessionId
from quiz_sift.domain.question import Question
from quiz_sift.ports.clock import Clock
from quiz_sift.scoring.grade import grade
from quiz_sift.store.deck_scope import DeckScopedStore


@dataclass(frozen=True, slots=True)
class PresentedQuestion:
    """出題1件。`presentation` は表示順に並べた選択肢 ID。"""

    question_id: QuestionId
    presentation: tuple[OptionId, ...]


def shuffle_seed(session_id: SessionId, question_id: QuestionId) -> int:
    """(セッション ID × 問題 ID) から決まる seed。

    組み込みの `hash` はプロセスごとにランダム化されるので使わない。
    """
    key = "\0".join((session_id.deck_id, session_id.local_id, question_id.deck_id, question_id.local_id))
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")


class Quizmaster:
    """1デッキ分の出題と回答の記録。

    所要時間は `present` から `submit` までを `clock.monotonic()` で測る。出題していない問題への回答と、
    同じ出題への2度目の回答は拒む。
    """

    def __init__(self, deck: Deck, store: DeckScopedStore, clock: Clock) -> None:
        if store.deck_id != deck.deck_id:
            raise ValueError(f"別デッキのストアを渡された: {store.deck_id}")
        self._questions = {question.question_id: question for question in deck.questions}
        self._deck_id = deck.deck_id
        self._store = store
        self._clock = clock
        self._pending: dict[tuple[SessionId, QuestionId], float] = {}

    def present(self, session_id: SessionId, question_id: QuestionId) -> PresentedQuestion:
        """問題を出題し、所要時間の計測を始める。"""
        question = self._question(session_id, question_id)
        order = [option.option_id for option in question.options]
        random.Random(shuffle_seed(session_id, question_id)).shuffle(order)
        self._pending[(session_id, question_id)] = self._clock.monotonic()
        return PresentedQuestion(question_id, tuple(order))

    def submit(self, session_id: SessionId, question_id: QuestionId, chosen: frozenset[OptionId]) -> AttemptRecord:
        """選んだ選択肢の集合を採点して学習記録へ追記し、その記録を返す。"""
        question = self._question(session_id, question_id)
        correct = grade(question, chosen)
        started = self._pending.pop((session_id, question_id), None)
        if started is None:
            raise ValueError("出題していない、または回答済みの問題への回答")
        attempt = AttemptRecord(
            session_id=session_id,
            question_id=question_id,
            chosen=chosen,
            correct=correct,
            elapsed_seconds=self._clock.monotonic() - started,
            answered_at=self._clock.now(),
        )
        self._store.record_attempt(attempt)
        return attempt

    def _question(self, session_id: SessionId, question_id: QuestionId) -> Question:
        if session_id.deck_id != self._deck_id:
            raise ValueError(f"別デッキのセッション: {session_id.deck_id}")
        question = self._questions.get(question_id)
        if question is None:
            raise ValueError(f"このデッキに無い問題: {question_id.local_id}")
        return question
