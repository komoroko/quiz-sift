"""合成デッキと固定 clock。

問題本文は架空のもの(NFR-2 の対象外)。正解の選択肢は問題ごとに A〜D へ散らしてあり、
既習フラグも3値すべてが現れる。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId, SessionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.scoring.session import Quizmaster
from quiz_sift.store.deck_scope import DeckScopedStore

DECK = DeckId("fixture-deck")
OTHER_DECK = DeckId("other-deck")
PRE = SessionId(DECK, "pre-20260920")
POST = SessionId(DECK, "post-20260927")

_SPEC = (
    ("q1", "A", StudiedFlag.STUDIED),
    ("q2", "B", StudiedFlag.STUDIED),
    ("q3", "C", StudiedFlag.UNSEEN),
    ("q4", "D", StudiedFlag.UNSEEN),
    ("q5", "B", StudiedFlag.UNKNOWN),
    ("q6", "C", StudiedFlag.UNKNOWN),
)


class FakeClock:
    """`monotonic` を手で進める時計。"""

    def __init__(self) -> None:
        self.seconds = 1000.0

    def now(self) -> datetime:
        return datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds


def make_deck(deck_id: DeckId = DECK) -> Deck:
    questions = []
    for local, correct, studied in _SPEC:
        qid = QuestionId(deck_id, local)
        questions.append(
            Question(
                question_id=qid,
                stem=f"架空の問題 {local}",
                options=tuple(Option(OptionId(qid, label), f"選択肢 {label}") for label in "ABCD"),
                correct_option_id=OptionId(qid, correct),
                explanation=None,
                studied=studied,
            )
        )
    return Deck(deck_id=deck_id, language=Language.JA, default_studied=None, questions=tuple(questions))


def wrong_option(question: Question) -> OptionId:
    """正解でない選択肢の ID。内容で同定するのではなく ID で選ぶ。"""
    return next(o.option_id for o in question.options if o.option_id != question.correct_option_id)


@pytest.fixture
def deck() -> Deck:
    return make_deck()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path) -> DeckScopedStore:
    return DeckScopedStore(DECK, tmp_path)


@pytest.fixture
def quizmaster(deck: Deck, store: DeckScopedStore, clock: FakeClock) -> Quizmaster:
    return Quizmaster(deck, store, clock)
