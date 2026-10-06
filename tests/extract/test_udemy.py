"""抽出器が HTML 文字列だけで動く純粋関数であること(C-002 / R-2 / ADR-007)。"""

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from quiz_sift.extract.evidence import SelectorKind
from quiz_sift.extract.failures import ExtractField
from quiz_sift.extract.udemy import FetchedPage, extract

from .conftest import page_html, question_html

SRC = Path(__file__).resolve().parents[2] / "src"


def test_extractor_is_a_pure_function_of_html(monkeypatch: pytest.MonkeyPatch) -> None:
    # 受験者が誤答した問題(選択 B / 正解 D)を含む。正解は受験者の選択ではなく正解マークから取る。
    html = page_html(
        question_html(1, correct=2, selected=2),
        question_html(2, correct=3, selected=1),
        question_html(3, correct=0, selected=3),
    )

    def no_network(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("抽出器がネットワークに触った")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    first = extract([FetchedPage("https://example.invalid/quiz/1", html)])
    second = extract([FetchedPage("https://example.invalid/quiz/1", html)])

    assert [q.answer_labels for q in first.questions] == [("C",), ("D",), ("A",)]
    assert [q.local_id for q in first.questions] == ["p001-q001", "p001-q002", "p001-q003"]
    assert first.questions[1].stem == "問題 2 の問題文"
    assert [o.text for o in first.questions[1].options] == [f"問題 2 の選択肢 {i}" for i in range(4)]
    assert {q.evidence.selector_kind for q in first.questions} == {SelectorKind.CORRECT_MARK}
    # 同じ入力には同じ結果。
    assert [(q.local_id, q.answer_labels, q.evidence) for q in first.questions] == [
        (q.local_id, q.answer_labels, q.evidence) for q in second.questions
    ]
    assert first.report == second.report


def test_extractor_does_not_import_the_browser_adapter() -> None:
    code = (
        "import sys; import quiz_sift.extract.udemy, quiz_sift.extract.writer; "
        "bad = sorted(m for m in ('quiz_sift.adapters.browser_cdp', 'socket', 'urllib.request') if m in sys.modules); "
        "sys.exit(', '.join(bad) if bad else 0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env={"PYTHONPATH": str(SRC)}
    )
    assert completed.returncode == 0, completed.stderr


def test_explanation_fetch_counts_are_reported() -> None:
    html = page_html(
        question_html(1, correct=0, explanation="公式解説 1"),
        question_html(2, correct=1),
        question_html(3, correct=2, explanation="   "),
        question_html(4, correct=3, explanation="公式解説 4"),
    )

    result = extract([FetchedPage("https://example.invalid/quiz/1", html)])

    assert result.report.with_explanation == 2
    assert result.report.without_explanation == 2
    assert [q.explanation for q in result.questions] == ["公式解説 1", None, None, "公式解説 4"]


def test_ids_are_numbered_per_page() -> None:
    pages = [
        FetchedPage("https://example.invalid/quiz/1", page_html(question_html(1, correct=0))),
        FetchedPage(
            "https://example.invalid/quiz/2", page_html(question_html(1, correct=1), question_html(2, correct=2))
        ),
    ]

    result = extract(pages)

    assert [q.local_id for q in result.questions] == ["p001-q001", "p002-q001", "p002-q002"]
    assert result.report.question_blocks == 3
    assert result.report.extracted == 3


def test_rules_ignore_css_module_hashes() -> None:
    # CSS Modules のハッシュはデプロイごとに変わりうる。接頭辞で照合していれば同じ結果になる。
    html = page_html(question_html(1, correct=1, selected=2), question_html(2, correct=3, selected=3))
    rehashed = html.replace("--sIcOh", "--Xy_9z").replace("--PLOEU", "--a1B2c")

    result = extract([FetchedPage("https://example.invalid/quiz/1", rehashed)])

    assert result.report.question_blocks == 2
    assert [q.answer_labels for q in result.questions] == [("B",), ("D",)]
    assert {q.evidence.selector_kind for q in result.questions} == {SelectorKind.CORRECT_MARK}


def test_wrapper_and_expanded_content_are_not_question_blocks() -> None:
    # `...question-result-pane-wrapper--` と `...question-result-pane-expanded-content--` は同じ語で始まる。
    html = page_html(question_html(1, correct=0))
    assert "result-pane--question-result-pane-wrapper--" in html
    assert "result-pane--question-result-pane-expanded-content--" in html

    result = extract([FetchedPage("https://example.invalid/quiz/1", html)])

    assert result.report.question_blocks == 1
    assert result.report.extracted == 1


def test_five_and_six_option_blocks_are_extracted() -> None:
    """A-1: 選択肢5件・6件のブロックは抽出され、3件以下・7件以上は抽出の失敗として列挙される。"""
    html = page_html(
        question_html(1, correct=4, selected=4, option_texts=list("abcde")),
        question_html(2, correct=(1, 5), option_texts=list("abcdef"), form="checkbox"),
        question_html(3, correct=0, option_texts=list("abc")),
        question_html(4, correct=0, option_texts=list("abcdefg")),
        question_html(5, correct=0, option_texts=list("ab")),
        question_html(6, correct=3),
    )

    result = extract([FetchedPage("https://example.invalid/quiz/1", html)])

    assert [(q.local_id, [o.label for o in q.options], q.answer_labels) for q in result.questions] == [
        ("p001-q001", list("ABCDE"), ("E",)),
        ("p001-q002", list("ABCDEF"), ("B", "F")),
        ("p001-q006", list("ABCD"), ("D",)),
    ]
    assert [o.text for o in result.questions[1].options] == list("abcdef")
    assert [(f.question_index, f.missing_field) for f in result.report.extraction_failures] == [
        (2, ExtractField.OPTIONS),
        (3, ExtractField.OPTIONS),
        (4, ExtractField.OPTIONS),
    ]
    assert (result.report.question_blocks, result.report.extracted) == (6, 3)
