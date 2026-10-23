"""誤答率降順の並べ替え(R-10 / D-054)。

誤答率は `Fraction` で比べる。浮動小数だと 1/3 と 2/6 が同率にならないことがあり、そこで同率の
タイブレークが効かずに順序が入力順に依存する。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from quiz_sift.domain.attempt import AttemptRecord, ClusterStat
from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.ids import ClusterId


@dataclass(frozen=True, slots=True)
class ClusterRank:
    """復習系列の1クラスタ。`stat` が順序の根拠。"""

    stat: ClusterStat

    @property
    def cluster_id(self) -> ClusterId:
        return self.stat.cluster_id

    @property
    def error_rate(self) -> Fraction:
        return Fraction(self.stat.wrong, self.stat.presented)


@dataclass(frozen=True, slots=True)
class NoTargets:
    """誤答のあるクラスタが1つも無い(全問正解)。空の並びとは型で区別する。"""


def cluster_stats(clusters: Sequence[Cluster], attempts: Sequence[AttemptRecord]) -> tuple[ClusterStat, ...]:
    """クラスタごとの出題回数・誤答回数・最終学習日時。どのクラスタにも属さない問題の回答は数えない。"""
    owner = {qid: c.cluster_id for c in clusters for qid in c.question_ids}
    presented: dict[ClusterId, int] = {}
    wrong: dict[ClusterId, int] = {}
    last: dict[ClusterId, datetime] = {}
    for attempt in attempts:
        cid = owner.get(attempt.question_id)
        if cid is None:
            continue
        presented[cid] = presented.get(cid, 0) + 1
        wrong[cid] = wrong.get(cid, 0) + (not attempt.correct)
        if cid not in last or attempt.answered_at > last[cid]:
            last[cid] = attempt.answered_at
    return tuple(
        ClusterStat(c.cluster_id, presented.get(c.cluster_id, 0), wrong.get(c.cluster_id, 0), last.get(c.cluster_id))
        for c in clusters
    )


def _key(rank: ClusterRank) -> tuple[Fraction, bool, datetime | None, ClusterId]:
    # 誤答率の降順 → 最終学習日時が古い順(日時の無いものを最古とみなす)→ クラスタ ID 昇順。
    last = rank.stat.last_studied_at
    return (-rank.error_rate, last is not None, last, rank.cluster_id)


def rank_clusters(stats: Sequence[ClusterStat]) -> tuple[ClusterRank, ...] | NoTargets:
    """誤答のあるクラスタを誤答率の降順に並べる。1つも無ければ `NoTargets`。"""
    ranks = [ClusterRank(s) for s in stats if s.wrong > 0]
    if not ranks:
        return NoTargets()
    return tuple(sorted(ranks, key=_key))
