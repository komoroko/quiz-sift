"""抽出テストの共通部品。Udemy の問題レビューページを模した合成 HTML を組み立てる。

構造は `extract/udemy.py` の抽出規則に合わせてある。実ページとの一致は T-019 の実測が受け持つ。
"""

from collections.abc import Sequence

from quiz_sift.extract.udemy import CORRECT_CLASS, USER_SELECTED_CLASS


def question_html(
    number: int,
    *,
    correct: int | None = None,
    selected: int | None = None,
    explanation: str | None = None,
    stem: str | None = None,
    option_texts: Sequence[str] | None = None,
) -> str:
    """問題1問分のブロック。`correct` / `selected` は正解マーク / 受験者の選択を付ける選択肢の位置。"""
    stem = f"問題 {number} の問題文" if stem is None else stem
    texts = option_texts if option_texts is not None else [f"問題 {number} の選択肢 {i}" for i in range(4)]
    options = []
    for position, text in enumerate(texts):
        classes = ["answer"]
        if position == correct:
            classes.append(CORRECT_CLASS)
        if position == selected:
            classes.append(USER_SELECTED_CLASS)
        options.append(
            f'<li class="{" ".join(classes)}" data-purpose="answer">'
            f'<span class="icon"></span><div data-purpose="answer-body"><p>{text}</p></div></li>'
        )
    explanation_html = (
        "" if explanation is None else f'<div data-purpose="overall-explanation"><p>{explanation}</p></div>'
    )
    return (
        f'<div class="result-pane" data-purpose="question-result">'
        f'<div id="question-prompt" data-purpose="question-prompt"><p>{stem}</p></div>'
        f'<ul class="answers">{"".join(options)}</ul>{explanation_html}</div>'
    )


def page_html(*blocks: str) -> str:
    """問題ブロックを並べたページ。ヘッダや `<br>` など抽出に無関係な要素も混ぜる。"""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Practice Test</title></head><body>"
        "<header><nav>Udemy</nav></header><main><h1>Results</h1><br>"
        f"{''.join(blocks)}"
        "</main></body></html>"
    )
