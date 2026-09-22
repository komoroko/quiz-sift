"""A-1: 回答の受け口は submit(session_id, question_id, chosen: OptionId) だけで、表示位置を受け取る API が無い。"""

import inspect
import typing
from types import ModuleType

import pytest

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import OptionId, QuestionId, SessionId
from quiz_sift.scoring import grade, report, session
from quiz_sift.scoring.session import Quizmaster

from .conftest import PRE

MODULES = (grade, session, report)


def _public_callables(module: ModuleType) -> list[tuple[str, typing.Any]]:
    found = []
    for name, obj in vars(module).items():
        if name.startswith("_") or getattr(obj, "__module__", None) != module.__name__:
            continue
        if inspect.isclass(obj):
            found += [
                (f"{name}.{attr}", member)
                for attr, member in vars(obj).items()
                if not attr.startswith("_") and inspect.isfunction(member)
            ]
        elif inspect.isfunction(obj):
            found.append((name, obj))
    return found


def test_no_positional_index_api_exists(quizmaster: Quizmaster, deck: Deck) -> None:
    # 受け口は submit ただ1つで、引数は (session_id, question_id, chosen: OptionId)。
    submits = [name for module in MODULES for name, _ in _public_callables(module) if "submit" in name]
    assert submits == ["Quizmaster.submit"]
    hints = typing.get_type_hints(Quizmaster.submit)
    params = list(inspect.signature(Quizmaster.submit).parameters)
    assert params == ["self", "session_id", "question_id", "chosen"]
    assert (hints["session_id"], hints["question_id"], hints["chosen"]) == (SessionId, QuestionId, OptionId)

    # scoring のどの公開 API も int を引数に取らず、index / position という名の引数も持たない。
    for module in MODULES:
        for name, func in _public_callables(module):
            for param, hint in typing.get_type_hints(func).items():
                if param == "return":
                    continue
                assert hint is not int, f"{name}({param}: int)"
                assert "index" not in param and "position" not in param, f"{name}({param})"

    # 実行時にも、表示位置の整数は回答として受け付けない。
    question = deck.questions[0]
    quizmaster.present(PRE, question.question_id)
    with pytest.raises(TypeError):
        quizmaster.submit(PRE, question.question_id, 0)  # type: ignore[arg-type]
