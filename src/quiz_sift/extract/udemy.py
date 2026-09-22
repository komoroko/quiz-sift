"""Udemy の問題レビューページの HTML から問題と正解根拠を取り出す(R-2 / ADR-007)。

`extract` は取得済みのページ(または取得失敗)の列だけを入力にとる純粋関数で、ブラウザにも
ネットワークにも触らない。

抽出規則は下の定数に集めてある。正解は2段で同定し、マッチした段を `SelectorKind` として
全問の根拠に残す(AR-4):

1. 正解マーク(`CORRECT_CLASS`)の付いた選択肢がちょうど1つ → `CORRECT_MARK`
2. 正解マークが1つも無く、受験者の選択(`USER_SELECTED_CLASS`)がちょうど1つ → `USER_SELECTED`

2段目は受験者が誤答していれば誤るので、「根拠の要素種別が全問で同一」の判定で見つかるように
別の値を返す。それ以外(マークが複数、どちらも無い)は `ExtractionFailure` にする。

問題文・選択肢・解説の本文は `NonEmptyText` で受ける。空文字で穴を埋めた問題は構築できない。
本文を持つ dataclass は `repr=False` にする(AR-17)。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from quiz_sift.domain.question import OPTION_COUNT
from quiz_sift.extract.evidence import AnswerEvidence, SelectorKind
from quiz_sift.extract.failures import ExtractField, ExtractionFailure, FetchFailure, FetchFailureReason
from quiz_sift.extract.html_tree import Element, parse_html

QUESTION_BLOCK = ("data-purpose", "question-result")
"""問題1問分のブロック。"""
QUESTION_BLOCK_SELECTOR = '[data-purpose="question-result"]'
"""`QUESTION_BLOCK` の CSS セレクタ表記。ブラウザ側でページの準備完了を待つのに使う。"""
STEM = ("data-purpose", "question-prompt")
OPTION = ("data-purpose", "answer")
OPTION_BODY = ("data-purpose", "answer-body")
EXPLANATION = ("data-purpose", "overall-explanation")
CORRECT_CLASS = "answer--correct"
USER_SELECTED_CLASS = "answer--user-selected"

OPTION_LABELS = "ABCD"
"""選択肢 ID。ページ上の並び順に振る。"""


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
    answer_label: str
    explanation: NonEmptyText | None
    evidence: AnswerEvidence

    def __post_init__(self) -> None:
        texts = [self.stem, *(option.text for option in self.options)]
        if self.explanation is not None:
            texts.append(self.explanation)
        if not all(isinstance(text, NonEmptyText) for text in texts):
            raise TypeError("本文は NonEmptyText でなければならない")
        if len(self.options) != OPTION_COUNT:
            raise ValueError(f"選択肢は {OPTION_COUNT} 件でなければならない: {len(self.options)} 件")
        if self.answer_label not in {option.label for option in self.options}:
            raise ValueError("answer_label がこの問題の選択肢に無い")


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
        blocks = parse_html(page.html).find_all(*QUESTION_BLOCK)
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

    stems = block.find_all(*STEM)
    stem = _text_of(stems[0]) if stems else None
    if stem is None:
        return fail(ExtractField.STEM, stems[0] if stems else block)

    option_elements = block.find_all(*OPTION)
    if len(option_elements) != OPTION_COUNT:
        return fail(ExtractField.OPTIONS, block)
    options = []
    for label, element in zip(OPTION_LABELS, option_elements, strict=True):
        bodies = element.find_all(*OPTION_BODY)
        text = _text_of(bodies[0]) if bodies else None
        if text is None:
            return fail(ExtractField.OPTION_TEXT, element)
        options.append(ExtractedOption(label, text))

    answer = _identify_answer(option_elements)
    if answer is None:
        return fail(ExtractField.ANSWER, block)
    position, evidence = answer

    explanations = block.find_all(*EXPLANATION)
    return ExtractedQuestion(
        local_id=local_id,
        stem=stem,
        options=tuple(options),
        answer_label=OPTION_LABELS[position],
        explanation=_text_of(explanations[0]) if explanations else None,
        evidence=evidence,
    )


def _identify_answer(option_elements: Sequence[Element]) -> tuple[int, AnswerEvidence] | None:
    """正解の選択肢の位置と根拠。どの規則でも一意に決まらなければ `None`。"""
    for css_class, kind in (
        (CORRECT_CLASS, SelectorKind.CORRECT_MARK),
        (USER_SELECTED_CLASS, SelectorKind.USER_SELECTED),
    ):
        marked = [position for position, element in enumerate(option_elements) if css_class in element.classes]
        if len(marked) == 1:
            return marked[0], AnswerEvidence(kind, option_elements[marked[0]].path)
        if marked:
            return None
    return None


def _text_of(element: Element) -> NonEmptyText | None:
    text = element.text()
    return NonEmptyText(text) if text else None
