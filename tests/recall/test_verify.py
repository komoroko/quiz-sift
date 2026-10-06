"""想起キーの重複・汎用語・識別ポイントの丸写し(C-004 / R-4)。"""

from dataclasses import replace

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, QuestionId
from quiz_sift.domain.recall import DiscriminationPoint, RecallKey
from quiz_sift.recall.verify import (
    MIN_ORIGINAL_RATIO,
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
