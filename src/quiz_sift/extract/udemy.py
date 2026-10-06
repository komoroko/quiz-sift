"""Udemy の問題レビューページの HTML から問題と正解根拠を取り出す(R-2 / ADR-007)。

`extract` は取得済みのページ(または取得失敗)の列だけを入力にとる純粋関数で、ブラウザにも
ネットワークにも触らない。

抽出規則は下の定数に集めてある。値は実物の結果ページ(`/learn/quiz/<id>/result/<id>`)の DOM を
実測して決めた。Udemy のクラス名は CSS Modules の `<ファイル>--<ローカル名>--<ハッシュ>` 形式で、
ハッシュはデプロイごとに変わりうるので、ハッシュを除いた接頭辞で照合する。

選択肢の件数 M は `OPTION_COUNTS`(4・5・6)を受け、ID は `OPTION_LABELS` からページ順に振る。
それ以外の件数は `OPTIONS` の失敗として報告する。

正解は2段で同定し、マッチした段を `SelectorKind` として全問の根拠に残す(AR-4):

1. 正解マーク(`CORRECT_CLASS_PREFIX`)の付いた選択肢が1つ以上 → その全件を正解にする `CORRECT_MARK`
2. 正解マークが1つも無く、選択アイコンの印で単一選択と判別でき(`_answer_form`)、受験者の選択
   (選択アイコンが `SELECTED_ICONS` のどれか)がちょうど1つ → `USER_SELECTED`

2段目は受験者が誤答していれば誤るので、「根拠の要素種別が全問で同一」の判定で見つかるように
別の値を返す。複数選択の問題では受験者の選択の件数が正解の件数 N と一致する保証が無いので、2段目を
使わない(D-189)。複数選択と判別した問題・判別できない問題・正解マークが全選択肢に付いた問題
(N < M を満たさない)は `ANSWER` の失敗にする。

問題文・選択肢・解説の本文は `NonEmptyText` で受ける。空文字で穴を埋めた問題は構築できない。
本文を持つ dataclass は `repr=False` にする(AR-17)。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from quiz_sift.domain.question import OPTION_COUNTS
from quiz_sift.extract.evidence import AnswerEvidence, SelectorKind
from quiz_sift.extract.failures import ExtractField, ExtractionFailure, FetchFailure, FetchFailureReason
from quiz_sift.extract.html_tree import Element, parse_html

QUESTION_BLOCK_CLASS_PREFIX = "result-pane--question-result-pane--"
"""問題1問分のブロック。外側の `...-pane-wrapper--` や内側の `...-pane-expanded-content--` とは
`--` の位置で区別される。"""
QUESTION_BLOCK_SELECTOR = f'[class*="{QUESTION_BLOCK_CLASS_PREFIX}"]'
"""問題ブロックの CSS セレクタ表記。ブラウザ側でページの準備完了を待つのに使う。"""
STEM_ID = "question-prompt"
OPTION = ("data-purpose", "answer")
OPTION_BODY = ("data-purpose", "answer-body")
EXPLANATION_ID = "overall-explanation"
"""「全体的な説明」。選択肢ごとの `question-explanation` は公式解説として扱わない。"""
CORRECT_CLASS_PREFIX = "answer-result-pane--answer-correct--"
"""正解の選択肢に付くクラス。受験者が選んだかどうかに依らず付く。"""
SELECTION_ICON = ("data-purpose", "answer-result-body-selection-icon")
SELECTED_ICONS = frozenset({"#icon-success", "#icon-cancel", "#icon-checkbox-filled", "#icon-wrong-selection"})
"""受験者が選んだ選択肢の選択アイコン(`<use xlink:href>`)。選ばなかった選択肢は `#icon-radio-empty` /
`#icon-checkbox-empty`。"""
SINGLE_SELECT_ICON_PREFIX = "#icon-radio-"
"""単一選択の問題の選択アイコン(`#icon-radio-empty` など)。"""
MULTI_SELECT_ICON_PREFIX = "#icon-checkbox-"
"""複数選択の問題の選択アイコン(`#icon-checkbox-empty` / `#icon-checkbox-filled`)。"""

OPTION_LABELS = "ABCDEF"
"""選択肢 ID。ページ上の並び順に先頭から振る。"""
assert len(OPTION_LABELS) == max(OPTION_COUNTS)


class AnswerForm(Enum):
    """選択アイコンの印から判別した問題の形。"""

    SINGLE = "single"
    MULTI = "multi"
    UNKNOWN = "unknown"
    """印が無いか、単一選択と複数選択の印が混ざっている。"""


class NonEmptyText(str):
    """前後の空白を落として空でない文字列。空なら構築できない。"""

    def __new__(cls, value: str) -> "NonEmptyText":
        stripped = value.strip()
        if not stripped:
            raise ValueError("本文が空")
        return super().__new__(cls, stripped)


@dataclass(frozen=True, slots=True, repr=False)
class FetchedPage:
    """ブラウザから取得したページ1つ。"""

    url: str
    html: str


@dataclass(frozen=True, slots=True, repr=False)
class ExtractedOption:
    label: str
    text: NonEmptyText


@dataclass(frozen=True, slots=True, repr=False)
class ExtractedQuestion:
    """抽出できた1問。書き出しは ingest の入力スキーマ(ADR-011)に従う。"""

    local_id: str
    stem: NonEmptyText
    options: tuple[ExtractedOption, ...]
    answer_labels: tuple[str, ...]
    """正解の選択肢 ID。選択肢の並び順で、重複なし。"""
    explanation: NonEmptyText | None
    evidence: AnswerEvidence

    def __post_init__(self) -> None:
        texts = [self.stem, *(option.text for option in self.options)]
        if self.explanation is not None:
            texts.append(self.explanation)
        if not all(isinstance(text, NonEmptyText) for text in texts):
            raise TypeError("本文は NonEmptyText でなければならない")
        if len(self.options) not in OPTION_COUNTS:
            raise ValueError(f"選択肢は {sorted(OPTION_COUNTS)} 件のいずれかでなければならない: {len(self.options)} 件")
        labels = [option.label for option in self.options]
        if not 1 <= len(self.answer_labels) < len(labels):
            raise ValueError(f"正解は 1 件以上 {len(labels)} 件未満でなければならない: {len(self.answer_labels)} 件")
        if self.answer_labels != tuple(label for label in labels if label in self.answer_labels):
            raise ValueError("answer_labels はこの問題の選択肢 ID を選択肢順に重複なく並べたものでなければならない")


@dataclass(frozen=True, slots=True)
class ExtractReport:
    """抽出結果の件数。

    `extracted + len(extraction_failures) == question_blocks` を不変条件に置く。取得失敗は問題ブロック
    を1つも持たないので別に数え、`failed` に両方を算入する(AR-5)。公式解説の有無の件数も持つ(AR-10)。
    """

    question_blocks: int
    extracted: int
    extraction_failures: tuple[ExtractionFailure, ...]
    fetch_failures: tuple[FetchFailure, ...]
    with_explanation: int
    without_explanation: int

    def __post_init__(self) -> None:
        if self.extracted + len(self.extraction_failures) != self.question_blocks:
            raise ValueError(
                f"保存則が破れている: extracted={self.extracted} + failures={len(self.extraction_failures)} "
                f"!= question_blocks={self.question_blocks}"
            )
        if self.with_explanation + self.without_explanation != self.extracted:
            raise ValueError("解説の内訳が extracted と合わない")
        counts = (self.question_blocks, self.extracted, self.with_explanation, self.without_explanation)
        if any(n < 0 for n in counts):
            raise ValueError("件数に負の値がある")

    @property
    def failed(self) -> int:
        """抽出できなかった件数。要素レベルの失敗と取得できなかったページの両方を数える。"""
        return len(self.extraction_failures) + len(self.fetch_failures)


@dataclass(frozen=True, slots=True, repr=False)
class ExtractResult:
    questions: tuple[ExtractedQuestion, ...]
    report: ExtractReport

    @property
    def evidences(self) -> tuple[AnswerEvidence, ...]:
        return tuple(question.evidence for question in self.questions)


def extract(pages: Sequence[FetchedPage | FetchFailure]) -> ExtractResult:
    """ページの列から問題を取り出す。問題 ID は `p<ページ位置>-q<ページ内の位置>`(どちらも1始まり)。"""
    questions: list[ExtractedQuestion] = []
    extraction_failures: list[ExtractionFailure] = []
    fetch_failures: list[FetchFailure] = []
    question_index = 0
    for page_number, page in enumerate(pages, start=1):
        if isinstance(page, FetchFailure):
            fetch_failures.append(page)
            continue
        blocks = parse_html(page.html).find_by_class_prefix(QUESTION_BLOCK_CLASS_PREFIX)
        if not blocks:
            fetch_failures.append(FetchFailure(page.url, FetchFailureReason.NO_QUESTIONS))
            continue
        for block_number, block in enumerate(blocks, start=1):
            outcome = _extract_question(block, f"p{page_number:03d}-q{block_number:03d}", question_index)
            if isinstance(outcome, ExtractionFailure):
                extraction_failures.append(outcome)
            else:
                questions.append(outcome)
            question_index += 1

    with_explanation = sum(1 for question in questions if question.explanation is not None)
    report = ExtractReport(
        question_blocks=question_index,
        extracted=len(questions),
        extraction_failures=tuple(extraction_failures),
        fetch_failures=tuple(fetch_failures),
        with_explanation=with_explanation,
        without_explanation=len(questions) - with_explanation,
    )
    return ExtractResult(questions=tuple(questions), report=report)


def _extract_question(block: Element, local_id: str, question_index: int) -> ExtractedQuestion | ExtractionFailure:
    def fail(field: ExtractField, element: Element) -> ExtractionFailure:
        return ExtractionFailure(question_index, field, element.path)

    stems = block.find_all("id", STEM_ID)
    stem = _text_of(stems[0]) if stems else None
    if stem is None:
        return fail(ExtractField.STEM, stems[0] if stems else block)

    option_elements = block.find_all(*OPTION)
    if len(option_elements) not in OPTION_COUNTS:
        return fail(ExtractField.OPTIONS, block)
    options = []
    for label, element in zip(OPTION_LABELS, option_elements, strict=False):
        bodies = element.find_all(*OPTION_BODY)
        text = _text_of(bodies[0]) if bodies else None
        if text is None:
            return fail(ExtractField.OPTION_TEXT, element)
        options.append(ExtractedOption(label, text))

    answer = _identify_answer(option_elements)
    if answer is None:
        return fail(ExtractField.ANSWER, block)
    positions, evidence = answer

    explanations = block.find_all("id", EXPLANATION_ID)
    return ExtractedQuestion(
        local_id=local_id,
        stem=stem,
        options=tuple(options),
        answer_labels=tuple(OPTION_LABELS[position] for position in positions),
        explanation=_text_of(explanations[0]) if explanations else None,
        evidence=evidence,
    )


def _identify_answer(option_elements: Sequence[Element]) -> tuple[tuple[int, ...], AnswerEvidence] | None:
    """正解の選択肢の位置(ページ順)と根拠。どの規則でも決まらなければ `None`。

    根拠の要素パスは、マッチした選択肢のうち先頭のもの。
    """
    marked = [position for position, element in enumerate(option_elements) if _is_correct_mark(element)]
    if marked:
        if len(marked) == len(option_elements):
            return None
        return tuple(marked), AnswerEvidence(SelectorKind.CORRECT_MARK, option_elements[marked[0]].path)
    if answer_form(option_elements) is not AnswerForm.SINGLE:
        return None
    selected = [position for position, element in enumerate(option_elements) if is_user_selected(element)]
    if len(selected) != 1:
        return None
    return (selected[0],), AnswerEvidence(SelectorKind.USER_SELECTED, option_elements[selected[0]].path)


def answer_form(option_elements: Sequence[Element]) -> AnswerForm:
    """選択アイコンの印から、問題が単一選択か複数選択かを判別する。

    単一選択の問題でも受験者が選んだ選択肢は `#icon-success` などに変わるので、印は全選択肢から集める。
    """
    icons = {href for option in option_elements for href in _selection_icons(option)}
    single = any(icon.startswith(SINGLE_SELECT_ICON_PREFIX) for icon in icons)
    multi = any(icon.startswith(MULTI_SELECT_ICON_PREFIX) for icon in icons)
    if single and not multi:
        return AnswerForm.SINGLE
    if multi and not single:
        return AnswerForm.MULTI
    return AnswerForm.UNKNOWN


def _is_correct_mark(option: Element) -> bool:
    return option.has_class_prefix(CORRECT_CLASS_PREFIX)


def is_user_selected(option: Element) -> bool:
    """受験者がこの選択肢を選んだか。"""
    return any(href in SELECTED_ICONS for href in _selection_icons(option))


def _selection_icons(option: Element) -> list[str]:
    return [
        href
        for icon in option.find_all(*SELECTION_ICON)
        for use in icon.iter()
        if use.tag == "use" and (href := use.attrs.get("xlink:href")) is not None
    ]


def _text_of(element: Element) -> NonEmptyText | None:
    text = element.text()
    return NonEmptyText(text) if text else None
