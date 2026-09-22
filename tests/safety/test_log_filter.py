"""教材本文のログ遮断(C-014 / NFR-2 / AR-17)。

本文はすべて合成。ログ出力(ハンドラが書いた文字列)と、`logging` を通らない素の例外トレースの
両方に本文が現れないことを確かめる。
"""

import io
import logging
import traceback
from collections.abc import Iterator

import pytest

from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.safety.entailment import Claim
from quiz_sift.safety.log_filter import MATERIAL_MASK, MaterialTextFilter

Q1 = QuestionId(DeckId("deckA"), "q1")
STEM = "合成問題文: 可用性ゾーンをまたぐ構成はどれか"
OPTION_TEXTS = ("合成選択肢アルファ", "合成選択肢ブラボー", "合成選択肢チャーリー", "合成選択肢デルタ")
EXPLANATION = "合成公式解説: 複数のゾーンに配置する"
QUESTION = Question(
    question_id=Q1,
    stem=STEM,
    options=tuple(Option(OptionId(Q1, label), text) for label, text in zip("ABCD", OPTION_TEXTS, strict=True)),
    correct_option_id=OptionId(Q1, "A"),
    explanation=EXPLANATION,
    studied=StudiedFlag.UNKNOWN,
)
MATERIAL = (STEM, *OPTION_TEXTS, EXPLANATION)


@pytest.fixture
def captured() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    material_filter = MaterialTextFilter()
    material_filter.register_question(QUESTION)
    handler.addFilter(material_filter)
    parent = logging.getLogger("quiz_sift_test_material")
    parent.addHandler(handler)
    parent.setLevel(logging.DEBUG)
    # 子ロガーから伝播したレコードにも効くことを確かめるため、子ロガーで書く。
    yield logging.getLogger("quiz_sift_test_material.child"), stream
    parent.removeHandler(handler)


def test_material_text_never_reaches_logs_or_repr(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    logger.info("問題文 %s", STEM)
    logger.warning("選択肢: %r / 解説: %s", OPTION_TEXTS, EXPLANATION)
    logger.info(f"f-string で埋めた本文 {STEM}")
    try:
        raise ValueError(f"本文を含む例外 {STEM}")
    except ValueError:
        logger.exception("抽出に失敗した")
    logger.info("スタック付き %s", EXPLANATION, stack_info=True)

    output = stream.getvalue()
    for text in MATERIAL:
        assert text not in output, text
    assert output.count(MATERIAL_MASK) >= 6
    assert "抽出に失敗した" in output
    assert "ValueError" in output

    # 素の例外トレース(logging を通らない)には filter が効かない。repr=False で塞いでいる。
    for material_holder in (QUESTION, *QUESTION.options, Claim(reference=EXPLANATION, statement=STEM)):
        assert not any(text in repr(material_holder) for text in MATERIAL), type(material_holder)
    try:
        raise RuntimeError(QUESTION, QUESTION.options[0])
    except RuntimeError as error:
        trace = "".join(traceback.format_exception(error))
    assert not any(text in trace for text in MATERIAL)


def test_record_is_kept_and_unregistered_text_passes(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured
    logger.info("処理した問題数 %d", 3)
    assert "処理した問題数 3" in stream.getvalue()


def test_longer_material_is_masked_before_its_substring() -> None:
    material_filter = MaterialTextFilter()
    material_filter.register_question(QUESTION)
    stem_extended = Question(
        question_id=Q1,
        stem=STEM + "(続き)",
        options=QUESTION.options,
        correct_option_id=QUESTION.correct_option_id,
        explanation=None,
        studied=StudiedFlag.UNKNOWN,
    )
    material_filter.register_question(stem_extended)
    assert material_filter.mask(f"x {STEM}(続き) y") == f"x {MATERIAL_MASK} y"
