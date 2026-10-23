"""抽出テストの共通部品。Udemy の問題レビューページを模した合成 HTML を組み立てる。

要素の入れ子・クラス名・`data-purpose`・`id` は実物の結果ページ(`/learn/quiz/<id>/result/<id>`)の DOM を
実測して写した。本文だけが合成で、実教材は含まない(NFR-2)。クラス名末尾のハッシュ(`--sIcOh` など)
も実物から写してあるが、抽出器はハッシュを見ない。
"""

from collections.abc import Collection, Sequence
from typing import Literal

_ICON_SELECTED_CORRECT = "#icon-success"
_ICON_SELECTED_WRONG = "#icon-cancel"
_ICON_UNSELECTED = {"radio": "#icon-radio-empty", "checkbox": "#icon-checkbox-empty", "none": "#icon-blank"}
_ICON_SELECTED_MULTI = "#icon-checkbox-filled"

type Form = Literal["radio", "checkbox", "none"]
"""選択アイコンの形。`none` は単一選択とも複数選択とも判別できない印。"""


def question_html(
    number: int,
    *,
    correct: int | Collection[int] | None = None,
    selected: int | Collection[int] | None = None,
    form: Form = "radio",
    explanation: str | None = None,
    stem: str | None = None,
    option_texts: Sequence[str] | None = None,
) -> str:
    """問題1問分のブロック。`correct` / `selected` は正解マーク / 受験者の選択を付ける選択肢の位置。

    正解マークは実物どおり選択肢の `answer-correct` クラスで表し、受験者の選択は選択アイコンで表す
    (選んで誤答した選択肢は `answer-incorrect` になる)。`form` は選択アイコンの形(単一選択は radio、
    複数選択は checkbox)。
    """
    stem = f"問題 {number} の問題文" if stem is None else stem
    texts = option_texts if option_texts is not None else [f"問題 {number} の選択肢 {i}" for i in range(4)]
    correct_positions = {correct} if isinstance(correct, int) else set(correct or ())
    selected_positions = {selected} if isinstance(selected, int) else set(selected or ())
    options = "".join(
        _option_html(
            text, is_correct=position in correct_positions, is_selected=position in selected_positions, form=form
        )
        for position, text in enumerate(texts)
    )
    explanation_html = (
        ""
        if explanation is None
        else '<div class="result-pane--question-related-fields--c3m--">'
        '<div class="overall-explanation-pane--overall-explanation--G-hLQ ud-form-group">'
        '<label class="ud-heading-md ud-form-label ud-heading-sm">全体的な説明</label>'
        f'<div data-purpose="safely-set-inner-html:rich-text-viewer:html" class="ud-text-md rt-scaffolding" '
        f'id="overall-explanation"><p>{explanation}</p></div></div></div>'
    )
    return (
        '<div class="result-pane--question-result-pane-wrapper--2bGiz">'
        '<div class="result-pane--question-result-pane--sIcOh result-pane--accordion-panel--TEJg7">'
        '<div class="result-pane--question-header-wrapper--3DCpC"><div class="result-pane--question-header--pTUOx">'
        '<span class="result-pane--question-header-collapsed-text--WVdEr">'
        f'<span class="result-pane--pane-title--f-2kd ud-heading-md"><span>問題{number}</span></span></span>'
        '<div data-purpose="safely-set-inner-html:rich-text-viewer:html" '
        'class="result-pane--question-format--PBvdY ud-text-md rt-scaffolding" '
        f'id="question-prompt"><p>{stem}</p></div>'
        "</div></div>"
        f'<div class="result-pane--question-result-pane-expanded-content--Og5Vc">{options}{explanation_html}</div>'
        "</div></div>"
    )


def _option_html(text: str, *, is_correct: bool, is_selected: bool, form: Form) -> str:
    if is_correct:
        state, label = "answer-correct--PLOEU", "回答は正解です" if is_selected else "正解"
    elif is_selected:
        state, label = "answer-incorrect--vFyOv", "回答は不正解です"
    else:
        state, label = "answer-skipped--1NDPn", None
    if not is_selected:
        icon = _ICON_UNSELECTED[form]
    elif form == "checkbox":
        icon = _ICON_SELECTED_MULTI
    else:
        icon = _ICON_SELECTED_CORRECT if is_correct else _ICON_SELECTED_WRONG
    label_html = (
        ""
        if label is None
        else '<div><span data-purpose="answer-result-header-user-label" '
        f'class="result-pane--answer-by-user-label--PSH86 ud-heading-xs">{label}</span></div>'
    )
    return (
        '<div class="result-pane--answer-result-pane--Niazi">'
        f'<div class="answer-result-pane--{state}" data-purpose="answer">{label_html}'
        '<div class="answer-result-pane--answer-body--cDGY6" data-purpose="answer-body">'
        '<span data-purpose="answer-result-body-selection-icon" '
        'class="answer-result-pane--answer-selection-icon--O6Xkn">'
        '<svg aria-hidden="true" focusable="false" class="ud-icon ud-icon-small">'
        f'<use xlink:href="{icon}"></use></svg>'
        "</span>"
        '<div data-purpose="safely-set-inner-html:rich-text-viewer:html" class="ud-heading-md rt-scaffolding" '
        f'id="answer-text"><p>{text}</p></div></div></div>'
        '<div class="answer-result-pane--answer-feedback--bHmbH"><h4 class="ud-heading-sm">説明</h4>'
        '<div data-purpose="safely-set-inner-html:rich-text-viewer:html" id="question-explanation" '
        'class="ud-text-md rt-scaffolding"><p>選択肢ごとの説明</p></div></div>'
        "</div>"
    )


def page_html(*blocks: str) -> str:
    """問題ブロックを並べたページ。ヘッダや `<br>` など抽出に無関係な要素も混ぜる。"""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Practice Test</title></head><body>"
        "<header><nav>Udemy</nav></header><main><h1>Results</h1><br>"
        '<div class="result-pane--collapse-all-button--6dQHt"><button type="button">'
        '<span class="ud-btn-label">すべての質問を折りたたむ</span></button></div>'
        f"{''.join(blocks)}"
        "</main></body></html>"
    )
