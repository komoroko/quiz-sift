"""想起キーの重複・汎用語・識別ポイントの丸写し(C-004 / R-4)。"""

from dataclasses import replace

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, QuestionId
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.ports.llm import Problem, RecallDraft
from quiz_sift.recall.verify import (
    MIN_ORIGINAL_RATIO,
    QuestionCheck,
    RecallMaterial,
    check_question,
    cluster_texts,
    distractors,
    is_duplicate,
    is_generic,
    is_verbatim,
    original_ratio,
    other_cluster_rates,
)

from .conftest import DECK, GOOD, question


def _texts(deck: Deck, clusters: tuple[Cluster, ...]) -> dict[ClusterId, list[str]]:
    questions = {q.question_id: q for q in deck.questions}
    return cluster_texts({c.cluster_id: c.question_ids for c in clusters}, questions)


def _material(cluster: str, qid: str, key: RecallKey) -> RecallMaterial:
    return RecallMaterial(ClusterId(DECK, cluster), QuestionId(DECK, qid), replace(GOOD[qid], key=key))


def test_duplicates_count_only_across_clusters(deck: Deck, clusters: tuple[Cluster, ...]) -> None:
    """A-4: 重複キーは別クラスタの素材の間だけで数え、クラスタ順で後の問題を落ちとする(D-200)。"""
    a = RecallKey(("S3", "データ"))
    b = RecallKey(("データ", "S3"))
    # tuple の等価比較では別物になる並べ替えを、同じ組として扱う。
    assert a.terms != b.terms
    q01 = _material("c001", "q01", a)
    # 同じクラスタ(c001)の中の同じ組は重複に数えない。
    assert not is_duplicate(_material("c001", "q02", b), [q01])
    # 別クラスタ(c002)の同じ組は重複で、後に来た方が落ちる。前の問題は後の問題と比べない。
    q04 = _material("c002", "q04", b)
    assert is_duplicate(q04, [q01])
    assert not is_duplicate(q01, [])
    # 別の組は重複ではない。
    assert not is_duplicate(_material("c002", "q04", RecallKey(("S3",))), [q01])

    texts = _texts(deck, clusters)
    q04_question = question(deck, "q04")
    # 1問単位の検査では、後の問題にだけ duplicate_key が付く。
    assert "duplicate_key" in check_question(q04, q04_question, texts, [q01]).failures
    assert "duplicate_key" not in check_question(q01, question(deck, "q01"), texts, []).failures
    same_cluster = _material("c001", "q02", RecallKey(("90 日後", "Glacier")))
    assert (
        check_question(
            same_cluster, question(deck, "q02"), texts, [_material("c001", "q01", same_cluster.draft.key)]
        ).failures
        == ()
    )


def test_generic_only_keys_are_rejected(deck: Deck, clusters: tuple[Cluster, ...]) -> None:
    texts = _texts(deck, clusters)
    s3 = ClusterId(DECK, "c001")
    # 「S3」「データ」は iam クラスタ(q04 の問題文)にも出現する(3クラスタ中1つ)。
    # 「アクセス頻度」は s3 クラスタにしか無い。
    rates = other_cluster_rates(RecallKey(("S3", "データ", "アクセス頻度")), s3, texts)
    assert rates == pytest.approx({"S3": 1 / 3, "データ": 1 / 3, "アクセス頻度": 0.0})
    assert not is_generic(rates)
    assert is_generic({"S3": 1 / 3, "データ": 1 / 3})
    # 他クラスタ出現率は選択肢・解説も見る(D-198):「ロール」は iam クラスタの選択肢と解説にしか無い。
    assert other_cluster_rates(RecallKey(("ロール",)), s3, texts) == pytest.approx({"ロール": 1 / 3})

    # 全 term が他クラスタにも出現するキーは落ち、出現率は落ちた素材でも記録される。
    material = _material("c001", "q01", RecallKey(("S3", "データ")))
    check = check_question(material, question(deck, "q01"), texts, [])
    assert check.failures == ("generic",)
    assert {(r.question_id.local_id, r.term): r.rate for r in check.rates}[("q01", "S3")] == pytest.approx(1 / 3)
    assert check_question(_material("c001", "q01", GOOD["q01"].key), question(deck, "q01"), texts, []).failures == ()


def test_two_distractors_stitched_together_goes_red(deck: Deck, clusters: tuple[Cluster, ...]) -> None:
    q01 = question(deck, "q01")
    stitched = "「S3 Glacier Deep Archive」のに対し「S3 One Zone-IA」"
    # 1件ずつ比べると、どちらとも半分ほどしか一致せず、残りが閾値を超えて通ってしまう。
    for wrong in ("S3 Glacier Deep Archive", "S3 One Zone-IA"):
        assert original_ratio(stitched, [wrong]) >= MIN_ORIGINAL_RATIO
    # 全誤答選択肢との一致区間の和で見ると、残りは閾値に届かない。
    assert original_ratio(stitched, distractors(q01)) < MIN_ORIGINAL_RATIO
    assert is_verbatim(stitched, q01)
    assert not is_verbatim(GOOD["q01"].discrimination.text, q01)

    point = DiscriminationPoint(stitched, GOOD["q01"].discrimination.distractor_ids)
    material = RecallMaterial(ClusterId(DECK, "c001"), q01.question_id, replace(GOOD["q01"], discrimination=point))
    assert check_question(material, q01, _texts(deck, clusters), []).failures == ("verbatim",)


def _problem(check: QuestionCheck, name: str) -> Problem:
    """落ちた検査 `name` の対象。落ちた検査と対象は同じ順に1件ずつ並ぶ。"""
    assert [p.check for p in check.problems] == list(check.failures)
    [problem] = [p for p in check.problems if p.check == name]
    return problem


def test_failed_checks_carry_their_targets(deck: Deck, clusters: tuple[Cluster, ...]) -> None:
    """T-086 A-2: 作り直しに渡す対象は検査ごとに決まる(empty は要素の位置、generic・duplicate_key はキーの語句、
    verbatim は名前だけ)。"""
    texts = _texts(deck, clusters)
    q01 = question(deck, "q01")
    good = GOOD["q01"]

    # empty: 空白だけの term と答えの要素の位置。空でない要素は入らない。
    blank = replace(
        good,
        key=RecallKey(("アクセス頻度", " ", "即座")),
        answers=(AnswerElement(good.answers[0].option_id, "  "),),
    )
    empty = _problem(
        check_question(RecallMaterial(ClusterId(DECK, "c001"), q01.question_id, blank), q01, texts, []), "empty"
    )
    assert (empty.elements, empty.terms, empty.options, empty.statements) == (("terms[1]", "answers[0]"), (), (), ())

    # generic: すべての term が他の論点にも現れるキーの語句。
    key = RecallKey(("S3", "データ"))
    generic = _problem(check_question(_material("c001", "q01", key), q01, texts, []), "generic")
    assert (generic.terms, generic.options, generic.elements) == (key.terms, (), ())

    # duplicate_key: 別の論点の前の問題と同じ組になったキーの語句。
    first = _material("c001", "q01", RecallKey(("S3", "アクセス頻度")))
    later = RecallKey(("アクセス頻度", "S3"))
    duplicate = _problem(
        check_question(_material("c002", "q04", later), question(deck, "q04"), texts, [first]), "duplicate_key"
    )
    assert (duplicate.terms, duplicate.options, duplicate.elements) == (later.terms, (), ())

    # verbatim: 対象を持たず名前だけ。
    stitched = DiscriminationPoint(
        "「S3 Glacier Deep Archive」のに対し「S3 One Zone-IA」", good.discrimination.distractor_ids
    )
    material = RecallMaterial(ClusterId(DECK, "c001"), q01.question_id, replace(good, discrimination=stitched))
    verbatim = _problem(check_question(material, q01, texts, []), "verbatim")
    assert (verbatim.terms, verbatim.options, verbatim.elements, verbatim.statements) == ((), (), (), ())


LEAKED_KEY_FORMS = ("（o1）", "。o1は", "、o2の", "解o2は", "・o3・", "前 o1 の", "O1", "ｏ１")
"""試行5で素材の本文に漏れた選択肢のキーの形(D-247)。本文は合成。"""


def test_option_key_in_material_text_fails_unless_the_question_has_it(
    deck: Deck, clusters: tuple[Cluster, ...]
) -> None:
    """T-087 A-1: 素材の本文が選択肢のキーを含み、問題の本文に無ければ option_key で落ちる。"""
    texts = _texts(deck, clusters)
    q01 = question(deck, "q01")
    good = GOOD["q01"]
    cluster = ClusterId(DECK, "c001")

    def failures(question_: Question, draft: RecallDraft) -> tuple[str, ...]:
        return check_question(RecallMaterial(cluster, q01.question_id, draft), question_, texts, []).failures

    assert "option_key" not in failures(q01, good)
    for form in LEAKED_KEY_FORMS:
        in_discrimination = replace(
            good,
            discrimination=DiscriminationPoint(good.discrimination.text + form, good.discrimination.distractor_ids),
        )
        in_answer = replace(good, answers=(AnswerElement(good.answers[0].option_id, form + "が正解"),))
        in_term = replace(good, key=RecallKey((*good.key.terms, form)))
        for draft in (in_discrimination, in_answer, in_term):
            assert "option_key" in failures(q01, draft), (form, draft)

    # ASCII の英数字に挟まれたもの・o7 以降・o10 はキーではない。
    for text in ("go1", "o1x", "So1", "o7", "o10", "Co2e"):
        draft = replace(
            good,
            discrimination=DiscriminationPoint(good.discrimination.text + text, good.discrimination.distractor_ids),
        )
        assert "option_key" not in failures(q01, draft), text

    # 問題の本文(問題文・選択肢・解説、同じ正規化)に同じ字面のキーがあれば落とさない。別のキーは落とす。
    leaked = replace(
        good,
        discrimination=DiscriminationPoint(good.discrimination.text + "（o1）", good.discrimination.distractor_ids),
    )
    for named in (
        replace(q01, stem=q01.stem + " モデル O1 を使う。"),
        replace(q01, options=(replace(q01.options[0], text="ｏ１ モデル"), *q01.options[1:])),
        replace(q01, explanation="o1 は推論モデル。"),
    ):
        assert "option_key" not in failures(named, leaked)
        other = replace(
            good,
            discrimination=DiscriminationPoint(good.discrimination.text + "、o2の", good.discrimination.distractor_ids),
        )
        assert "option_key" in failures(named, other)

    # 作り直しに渡すのは検査の名前だけで、本文もキーも渡さない。
    check = check_question(RecallMaterial(cluster, q01.question_id, leaked), q01, texts, [])
    problem = _problem(check, "option_key")
    assert (problem.terms, problem.options, problem.elements, problem.statements) == ((), (), (), ())
