"""誤答率降順の並べ替え(C-010 / R-10 / A-1 / A-2)。"""

from datetime import timedelta
from itertools import permutations

from quiz_sift.domain.attempt import ClusterStat
from quiz_sift.domain.ids import ClusterId
from quiz_sift.schedule.rank import NoTargets, cluster_stats, rank_clusters

from .conftest import DECK, T0, attempt, cluster


def test_descending_error_rate_with_deterministic_tiebreak() -> None:
    clusters = [
        cluster("c1", "q1a", "q1b"),
        cluster("c2", "q2a", "q2b", "q2c"),
        cluster("c3", "q3a", "q3b", "q3c"),
        cluster("c5", "q5a"),
        cluster("c7", "q7a"),
        cluster("c8", "q8a"),
        cluster("c9", "q9a"),
    ]
    attempts = [
        # c9: 2/2 → 誤答率 1
        attempt("q9a", False, 50),
        attempt("q9a", False, 51),
        # c5: 1/2(最終 10 分)と c1: 2/4(最終 30 分)は同率 → 古い c5 が先
        attempt("q5a", False, 5),
        attempt("q5a", True, 10),
        attempt("q1a", False, 20),
        attempt("q1b", True, 21),
        attempt("q1a", False, 29),
        attempt("q1b", True, 30),
        # c3: 1/3 と c2: 2/6 は同率・同じ最終日時 → ID 昇順で c2 が先。浮動小数で比べると同率にならない。
        attempt("q3a", False, 38),
        attempt("q3b", True, 39),
        attempt("q3c", True, 40),
        *(attempt(q, i >= 2, 35 + i) for i, q in enumerate(["q2a", "q2b", "q2c", "q2a", "q2b"])),
        attempt("q2c", True, 40),
        # c7: 全問正解 → 対象外。c8: 未出題 → 対象外。どのクラスタにも属さない問題は数えない。
        attempt("q7a", True, 1),
        attempt("orphan", False, 2),
    ]
    stats = cluster_stats(clusters, attempts)
    by_id = {s.cluster_id.local_id: s for s in stats}
    assert (by_id["c2"].presented, by_id["c2"].wrong) == (6, 2)
    assert by_id["c1"].last_studied_at == T0 + timedelta(minutes=30)
    assert by_id["c8"] == ClusterStat(ClusterId(DECK, "c8"), 0, 0, None)

    expected = ["c9", "c5", "c1", "c2", "c3"]
    for order in permutations(stats):
        ranks = rank_clusters(order)
        assert not isinstance(ranks, NoTargets)
        assert [r.cluster_id.local_id for r in ranks] == expected
    ranks = rank_clusters(stats)
    assert not isinstance(ranks, NoTargets)
    assert [str(r.error_rate) for r in ranks] == ["1", "1/2", "1/2", "1/3", "1/3"]


def test_unknown_last_studied_is_treated_as_oldest() -> None:
    dated = ClusterStat(ClusterId(DECK, "a"), 2, 1, T0)
    undated = ClusterStat(ClusterId(DECK, "b"), 2, 1, None)
    for order in ([dated, undated], [undated, dated]):
        ranks = rank_clusters(order)
        assert not isinstance(ranks, NoTargets)
        assert [r.cluster_id.local_id for r in ranks] == ["b", "a"]


def test_no_targets_is_a_distinct_type() -> None:
    clusters = [cluster("c1", "q1"), cluster("c2", "q2")]
    all_correct = [attempt("q1", True, 0), attempt("q2", True, 1), attempt("q1", True, 2)]

    result = rank_clusters(cluster_stats(clusters, all_correct))
    assert isinstance(result, NoTargets)
    assert not isinstance(result, tuple)

    # 1問でも誤答すれば並びが返る。
    one_wrong = rank_clusters(cluster_stats(clusters, [*all_correct, attempt("q2", False, 3)]))
    assert isinstance(one_wrong, tuple)
    assert [r.cluster_id.local_id for r in one_wrong] == ["c2"]

    # 1問も解いていないときも、誤答のあるクラスタは無い。
    assert isinstance(rank_clusters(cluster_stats(clusters, [])), NoTargets)
