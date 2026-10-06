"""要素レベルの失敗と取得レベルの失敗を別々に数えること(C-002 / AR-5)。"""

import pytest

from quiz_sift.extract.evidence import AnswerEvidence, SelectorKind
from quiz_sift.extract.failures import ExtractField, FetchFailure, FetchFailureReason
from quiz_sift.extract.udemy import (
    ExtractedOption,
    ExtractedQuestion,
    FetchedPage,
    NonEmptyText,
    extract,
)

from .conftest import page_html, question_html


def test_element_and_fetch_failures_are_separate() -> None:
    pages: list[FetchedPage | FetchFailure] = [
        FetchedPage(
            "https://example.invalid/quiz/1",
            page_html(question_html(1, correct=0), question_html(2, correct=1, stem="  ")),
        ),
        FetchFailure("https://example.invalid/quiz/2", FetchFailureReason.DISCONNECTED),
        FetchedPage("https://example.invalid/join/login", "<html><body><form>Log in</form></body></html>"),
        FetchedPage(
            "https://example.invalid/quiz/4",
            page_html(question_html(1, correct=2, option_texts=["a", "", "c", "d"])),
        ),
    ]

    result = extract(pages)
    report = result.report

    assert report.extracted == 1
    assert [(f.question_index, f.missing_field) for f in report.extraction_failures] == [
        (1, ExtractField.STEM),
        (2, ExtractField.OPTION_TEXT),
    ]
    assert report.fetch_failures == (
        FetchFailure("https://example.invalid/quiz/2", FetchFailureReason.DISCONNECTED),
        FetchFailure("https://example.invalid/join/login", FetchFailureReason.NO_QUESTIONS),
    )
    assert report.failed == 4
    # 空の問題文・空の選択肢は空文字で埋めた問題にならず、失敗として出る。
    assert all(q.stem and all(o.text for o in q.options) for q in result.questions)


@pytest.mark.parametrize(
    ("block", "field"),
    [
        (question_html(1), ExtractField.ANSWER),
        (question_html(1, correct=(0, 1, 2, 3)), ExtractField.ANSWER),
        (question_html(1, correct=0, option_texts=["a", "b", "c"]), ExtractField.OPTIONS),
        (question_html(1, correct=0, option_texts=list("abcdefg")), ExtractField.OPTIONS),
    ],
    ids=["no-mark", "every-option-marked", "three-options", "seven-options"],
)
def test_element_failures_name_the_field(block: str, field: ExtractField) -> None:
    result = extract([FetchedPage("https://example.invalid/quiz/1", page_html(block))])

    assert result.questions == ()
    assert [f.missing_field for f in result.report.extraction_failures] == [field]
    assert result.report.extraction_failures[0].element_path.startswith("/html[1]/body[1]/main[1]/div[2]/div[1]")


def test_empty_text_cannot_be_constructed() -> None:
    with pytest.raises(ValueError):
        NonEmptyText(" \n ")
    evidence = AnswerEvidence(SelectorKind.CORRECT_MARK, "/x")
    options = tuple(ExtractedOption(label, NonEmptyText(label)) for label in "ABCD")
    with pytest.raises(TypeError):
        ExtractedQuestion("q1", "", options, ("A",), None, evidence)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ExtractedQuestion("q1", NonEmptyText("s"), options, ("A",), "", evidence)  # type: ignore[arg-type]
