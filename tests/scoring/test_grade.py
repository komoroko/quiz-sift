"""A-2: 問題ごとに異なる非恒等シャッフルの下で、ID で同定した全問正解が 100%、全問誤答が 0%。"""

import pytest

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import OptionId, SessionId
from quiz_sift.domain.question import Question
from quiz_sift.scoring.grade import grade
from quiz_sift.scoring.session import PresentedQuestion, Quizmaster
from quiz_sift.store.deck_scope import DeckScopedStore

from .conftest import POST, PRE, wrong_option


def _present_all(quizmaster: Quizmaster, deck: Deck, session: SessionId) -> list[PresentedQuestion]:
    return [quizmaster.present(session, q.question_id) for q in deck.questions]


def _ratio(store: DeckScopedStore, session: SessionId) -> float:
    marks = [a.correct for a in store.attempts(session)]
    return sum(marks) / len(marks)


def test_grading_under_nonidentity_shuffle(quizmaster: Quizmaster, deck: Deck, store: DeckScopedStore) -> None:
    by_id = {q.question_id: q for q in deck.questions}
    for session, answer, expected in ((PRE, lambda q: q.correct_option_ids[0], 1.0), (POST, wrong_option, 0.0)):
        presented = _present_all(quizmaster, deck, session)

        # フィクスチャ側の前提: 並びが問題ごとに異なり、正解が位置 0 に来ない問題がある。
        patterns = {tuple(o.local_id for o in p.presentation) for p in presented}
        assert len(patterns) > 1, "全問が同じ並びになっている"
        assert any(p.presentation[0] != by_id[p.question_id].correct_option_ids[0] for p in presented)
        for p in presented:
            original = tuple(o.option_id for o in by_id[p.question_id].options)
            assert sorted(p.presentation) == sorted(original)

        # 入力は表示位置ではなく選択肢 ID で組み立てる。
        for p in presented:
            quizmaster.submit(session, p.question_id, answer(by_id[p.question_id]))
        assert _ratio(store, session) == expected


def test_grade_rejects_option_of_another_question(deck: Deck) -> None:
    first, second = deck.questions[:2]
    with pytest.raises(ValueError):
        grade(first, second.correct_option_ids[0])


def test_grade_compares_ids(deck: Deck) -> None:
    question: Question = deck.questions[0]
    assert grade(question, OptionId(question.question_id, "A")) is True
    assert grade(question, OptionId(question.question_id, "B")) is False


def test_submit_requires_a_presentation(quizmaster: Quizmaster, deck: Deck) -> None:
    question = deck.questions[0]
    with pytest.raises(ValueError):
        quizmaster.submit(PRE, question.question_id, question.correct_option_ids[0])
    quizmaster.present(PRE, question.question_id)
    quizmaster.submit(PRE, question.question_id, question.correct_option_ids[0])
    with pytest.raises(ValueError):
        quizmaster.submit(PRE, question.question_id, question.correct_option_ids[0])
