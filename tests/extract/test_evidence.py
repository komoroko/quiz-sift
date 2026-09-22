"""正解根拠の要素種別の判定が入力に依存すること(C-002 / AR-4)。

`selector_kind` を定数にすると「全問で同一」の判定は恒真式になる。問題ごとに DOM 構造が異なる
negative control で、判定が実際に赤くなることを確かめる。
"""

from quiz_sift.extract.evidence import AnswerEvidence, SelectorKind, selector_kind_is_uniform
from quiz_sift.extract.udemy import FetchedPage, extract

from .conftest import page_html, question_html

URL = "https://example.invalid/quiz/1"


def test_selector_kind_negative_control_goes_red() -> None:
    # 1問目は正解マークを持ち、2問目は受験者の選択マークしか持たない(正解マークが描画されなかった形)。
    negative_control = page_html(
        question_html(1, correct=2, selected=1),
        question_html(2, correct=None, selected=3),
    )

    result = extract([FetchedPage(URL, negative_control)])

    kinds = {evidence.selector_kind for evidence in result.evidences}
    assert kinds == {SelectorKind.CORRECT_MARK, SelectorKind.USER_SELECTED}
    assert len(kinds) == 2
    assert selector_kind_is_uniform(result.evidences) is False
    # フォールバックの枝は受験者の選択を正解として取り込んでいる。だから判定が赤くなる必要がある。
    assert [q.answer_label for q in result.questions] == ["C", "D"]

    # 対照: 同じ形の問題を正解マークだけで揃えれば緑になる。赤は入力から来ている。
    uniform = page_html(question_html(1, correct=2, selected=1), question_html(2, correct=3, selected=3))
    uniform_result = extract([FetchedPage(URL, uniform)])
    assert {e.selector_kind for e in uniform_result.evidences} == {SelectorKind.CORRECT_MARK}
    assert selector_kind_is_uniform(uniform_result.evidences) is True


def test_evidence_points_at_the_matched_option_element() -> None:
    html = page_html(question_html(1, correct=1), question_html(2, selected=2))

    first, second = extract([FetchedPage(URL, html)]).evidences

    assert first.element_path.endswith("/div[1]/ul[1]/li[2]")
    assert second.element_path.endswith("/div[2]/ul[1]/li[3]")
    assert first.element_path.startswith("/html[1]/body[1]/main[1]/")


def test_no_evidence_is_not_uniform() -> None:
    assert selector_kind_is_uniform([]) is False
    assert selector_kind_is_uniform([AnswerEvidence(SelectorKind.USER_SELECTED, "/x")]) is True
