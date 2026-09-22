"""既知5グループの合成フィクスチャ(R-3 / AR-11)。

`fixtures/known5.jsonl` は5グループ・各3問以上の合成問題で、各行の `group` がそのグループ構造の
「既知の正解」になる。合成なので NFR-2 の対象外でコミットできる。
"""

import json
from pathlib import Path

import pytest

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag

DECK = DeckId("aws-saa")
FIXTURE = Path(__file__).parent / "fixtures" / "known5.jsonl"

type Groups = dict[str, tuple[QuestionId, ...]]


def _question(row: dict[str, object]) -> Question:
    qid = QuestionId(DECK, str(row["id"]))
    texts = row["options"]
    assert isinstance(texts, list)
    options = tuple(Option(OptionId(qid, label), str(text)) for label, text in zip("ABCD", texts, strict=True))
    correct = row["correct"]
    assert isinstance(correct, int)
    return Question(
        question_id=qid,
        stem=str(row["stem"]),
        options=options,
        correct_option_id=options[correct].option_id,
        explanation=str(row["explanation"]),
        studied=StudiedFlag.UNKNOWN,
    )


@pytest.fixture
def known5() -> tuple[Deck, Groups]:
    """フィクスチャのデッキと、既知のグループ(ラベル → 問題 ID)。"""
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    deck = Deck(DECK, Language.JA, None, tuple(_question(row) for row in rows))
    groups: Groups = {}
    for row in rows:
        groups[row["group"]] = (*groups.get(row["group"], ()), QuestionId(DECK, row["id"]))
    return deck, groups
