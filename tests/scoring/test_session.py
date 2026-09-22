"""A-3: シャッフル seed は (セッション ID × 問題 ID) から導出され、同一問題の2セッションで並びが異なる。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import SessionId
from quiz_sift.scoring.session import Quizmaster, shuffle_seed

from .conftest import OTHER_DECK, POST, PRE, FakeClock, wrong_option


def test_same_question_differs_across_sessions(quizmaster: Quizmaster, deck: Deck) -> None:
    question_id = deck.questions[0].question_id
    first = quizmaster.present(PRE, question_id)
    second = quizmaster.present(POST, question_id)
    assert first.presentation != second.presentation
    assert shuffle_seed(PRE, question_id) != shuffle_seed(POST, question_id)
    # 同じ (セッション, 問題) なら何度出しても同じ並び。
    assert quizmaster.present(PRE, question_id) == first


def test_seed_is_stable_across_processes(deck: Deck) -> None:
    # 組み込みの hash はプロセスごとに変わる。seed がそれに依存していないことを別プロセスで見る。
    question_id = deck.questions[0].question_id
    code = (
        "from quiz_sift.domain.ids import DeckId, QuestionId, SessionId;"
        "from quiz_sift.scoring.session import shuffle_seed;"
        f"print(shuffle_seed(SessionId(DeckId({PRE.deck_id!r}), {PRE.local_id!r}),"
        f" QuestionId(DeckId({question_id.deck_id!r}), {question_id.local_id!r})))"
    )
    src = Path(__file__).resolve().parents[2] / "src"
    env = {**os.environ, "PYTHONPATH": str(src), "PYTHONHASHSEED": "random"}
    out = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True, env=env)
    assert int(out.stdout) == shuffle_seed(PRE, question_id)


def test_elapsed_time_is_measured_by_the_clock(quizmaster: Quizmaster, deck: Deck, clock: FakeClock) -> None:
    question = deck.questions[2]
    quizmaster.present(PRE, question.question_id)
    clock.seconds += 7.5
    attempt = quizmaster.submit(PRE, question.question_id, wrong_option(question))
    assert attempt.elapsed_seconds == 7.5
    assert attempt.correct is False
    assert attempt.answered_at == clock.now()


def test_rejects_session_of_another_deck(quizmaster: Quizmaster, deck: Deck) -> None:
    with pytest.raises(ValueError):
        quizmaster.present(SessionId(OTHER_DECK, PRE.local_id), deck.questions[0].question_id)
