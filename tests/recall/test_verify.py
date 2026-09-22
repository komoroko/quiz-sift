"""想起キーの重複・汎用語・識別ポイントの丸写し(C-004 / R-4 / A-2 / A-3 / A-4)。"""

from dataclasses import replace
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId
from quiz_sift.domain.recall import RecallKey
from quiz_sift.recall.generate import RecallRejected, generate_recall_set
from quiz_sift.recall.verify import (
    MIN_ORIGINAL_RATIO,
    distractors,
    duplicate_count,
    is_generic,
    is_verbatim,
    original_ratio,
    other_cluster_rates,
    question_texts,
)

from .conftest import DECK, GOOD, StubLlm, Writer, question


def _reject(llm: StubLlm, deck: Deck, clusters: tuple[Cluster, ...], out: Path) -> RecallRejected:
    with pytest.raises(RecallRejected) as exc:
        generate_recall_set(llm, deck, clusters, Writer(out))
    assert exc.value.code not in (None, 0)
    assert not out.exists()
    assert llm.checked == []
    return exc.value


def test_reordered_key_counts_as_duplicate(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    a = RecallKey(("AWS", "料金", "設定"))
    b = RecallKey(("料金", "設定", "AWS"))
    # tuple の等価比較では別物になる並べ替えを、重複として数える。
    assert a.terms != b.terms
    assert duplicate_count([a, b]) == 1
    assert duplicate_count([a, RecallKey(("AWS", "料金"))]) == 0

    # q01 と q02 に、どちらの問題にも実在する同じ語の組を順序だけ変えて与える。
    llm = StubLlm(
        {
            **GOOD,
            "q01": replace(GOOD["q01"], key=RecallKey(("S3", "ストレージクラス"))),
            "q02": replace(GOOD["q02"], key=RecallKey(("ストレージクラス", "S3"))),
        }
    )
    report = _reject(llm, deck, clusters, tmp_path / "recall.tsv").report
    assert report.duplicate_keys == 1
    assert (report.ungrounded_keys, report.generic_keys, report.missing_options, report.verbatim) == (0, 0, 0, 0)


def test_generic_only_keys_are_rejected(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    texts = {
        c.cluster_id: [t for qid in c.question_ids for t in question_texts(question(deck, qid.local_id))]
        for c in clusters
    }
    s3 = ClusterId(DECK, "c001")
    # 「S3」「データ」は iam クラスタにも出現する(3クラスタ中1つ)。「アクセス頻度」は s3 クラスタにしか無い。
    rates = other_cluster_rates(RecallKey(("S3", "データ", "アクセス頻度")), s3, texts)
    assert rates == pytest.approx({"S3": 1 / 3, "データ": 1 / 3, "アクセス頻度": 0.0})
    assert not is_generic(rates)
    assert is_generic({"S3": 1 / 3, "データ": 1 / 3})

    # 全 term が他クラスタにも出現するキーは止める。出現率は止めた実行でも残る。
    llm = StubLlm({**GOOD, "q01": replace(GOOD["q01"], key=RecallKey(("S3", "データ")))})
    report = _reject(llm, deck, clusters, tmp_path / "generic.tsv").report
    assert report.generic_keys == 1
    assert (report.ungrounded_keys, report.duplicate_keys, report.missing_options, report.verbatim) == (0, 0, 0, 0)
    assert {(r.question_id.local_id, r.term): r.rate for r in report.term_rates}[("q01", "S3")] == pytest.approx(1 / 3)

    # 通った実行では汎用語だけのキーは 0 件で、全キーの全 term の出現率が成果物に記録される。
    writer = Writer(tmp_path / "recall.tsv")
    generate_recall_set(StubLlm(GOOD), deck, clusters, writer)
    assert writer.result is not None
    assert writer.result.report.generic_keys == 0
    recorded = {(r.question_id.local_id, r.term): r.rate for r in writer.result.report.term_rates}
    assert set(recorded) == {(qid, t) for qid, d in GOOD.items() for t in d.key.terms}
    assert recorded[("q01", "アクセス頻度")] == 0.0
    # 「許可」は iam クラスタの外に出現しない。
    assert recorded[("q03", "許可")] == 0.0


def test_two_distractors_stitched_together_goes_red(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    q01 = question(deck, "q01")
    stitched = "「S3 Glacier Deep Archive」のに対し「S3 One Zone-IA」"
    # 1件ずつ比べると、どちらとも半分ほどしか一致せず、残りが閾値を超えて通ってしまう。
    for wrong in ("S3 Glacier Deep Archive", "S3 One Zone-IA"):
        assert original_ratio(stitched, [wrong]) >= MIN_ORIGINAL_RATIO
    # 全誤答選択肢との一致区間の和で見ると、残りは閾値に届かない。
    assert original_ratio(stitched, distractors(q01)) < MIN_ORIGINAL_RATIO
    assert is_verbatim(stitched, q01)
    assert not is_verbatim(GOOD["q01"].discrimination, q01)

    llm = StubLlm({**GOOD, "q01": replace(GOOD["q01"], discrimination=stitched)})
    report = _reject(llm, deck, clusters, tmp_path / "recall.tsv").report
    assert report.verbatim == 1
    assert (report.ungrounded_keys, report.duplicate_keys, report.generic_keys, report.missing_options) == (0, 0, 0, 0)
