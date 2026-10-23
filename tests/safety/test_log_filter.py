"""教材本文のログ遮断(C-014 / NFR-2 / AR-17)。

本文はすべて合成。ログ出力(ハンドラが書いた文字列)と、`logging` を通らない素の例外トレースの
両方に本文が現れないことを確かめる。
"""

import io
import json
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
    correct_option_ids=(OptionId(Q1, "A"),),
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
        correct_option_ids=QUESTION.correct_option_ids,
        explanation=None,
        studied=StudiedFlag.UNKNOWN,
    )
    material_filter.register_question(stem_extended)
    assert material_filter.mask(f"x {STEM}(続き) y") == f"x {MATERIAL_MASK} y"


def test_empty_material_is_not_registered() -> None:
    material_filter = MaterialTextFilter()
    material_filter.register_question(
        Question(
            question_id=Q1,
            stem=STEM,
            options=QUESTION.options,
            correct_option_ids=QUESTION.correct_option_ids,
            explanation="",
            studied=StudiedFlag.UNKNOWN,
        )
    )
    assert material_filter.mask("処理した問題数 3") == "処理した問題数 3"


def test_escaped_material_is_masked_in_repr_and_json(captured: tuple[logging.Logger, io.StringIO]) -> None:
    # 実教材の解説は複数行で、引用符やバックスラッシュも含む。repr と JSON はそれらをエスケープする。
    explanation = "合成解説1行目: 'ゾーン' を\n2行目: \"冗長化\" と C:\\path"
    stem = "合成問題文: it's 複数行\n続き"
    question = Question(
        question_id=Q1,
        stem=stem,
        options=QUESTION.options,
        correct_option_ids=QUESTION.correct_option_ids,
        explanation=explanation,
        studied=StudiedFlag.UNKNOWN,
    )
    logger, stream = captured
    material_filter = MaterialTextFilter()
    material_filter.register_question(question)
    # 子ロガーにはハンドラがないので、親のハンドラへフィルタを足す。
    logging.getLogger("quiz_sift_test_material").handlers[0].addFilter(material_filter)

    logger.warning("解説: %r", explanation)
    logger.warning("組: %r", (stem, explanation))
    try:
        raise ValueError(f"抽出に失敗 {stem!r}")
    except ValueError:
        logger.exception("失敗")
    logger.info("json: %s / %s", json.dumps(explanation), json.dumps(stem, ensure_ascii=False))

    output = stream.getvalue()
    for text in (stem, explanation):
        for form in (text, repr(text)[1:-1], repr((text,))[2:-3], json.dumps(text)[1:-1]):
            assert form not in output, form
    for fragment in ("1行目", "2行目", "複数行", "冗長化"):
        assert fragment not in output, fragment
    assert output.count(MATERIAL_MASK) >= 6
