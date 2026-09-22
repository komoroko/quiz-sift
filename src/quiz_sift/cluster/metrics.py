"""クラスタの退化メトリクスと退化ガード(R-3 / AR-11)。

実データの M/N・その他比率・最大クラスタ比率には合格ラインを置かず、記録するだけにする(D-004)。
`degeneracy_violations` は、既知5グループの合成フィクスチャに対して「圧縮を一切していない出力」を
弾くための下限であり、実データの実行を止めるためには使わない。
"""

import logging
from dataclasses import dataclass

from quiz_sift.cluster.assign import Partition

logger = logging.getLogger(__name__)

MAX_CLUSTER_RATIO = 0.5
"""退化ガード: 最大クラスタが全体に占める比率の上限。"""
MAX_OTHER_RATIO = 0.2
"""退化ガード: 「その他」クラスタが全体に占める比率の上限。"""


@dataclass(frozen=True, slots=True)
class ClusterMetrics:
    """1回のクラスタリングの実行サマリ。"""

    question_count: int
    """N。"""
    cluster_count: int
    """M。空でなければ「その他」も1クラスタと数える。"""
    other_count: int
    max_cluster_size: int
    """「その他」を含めた最大クラスタの問題数。"""
    failure_count: int
    """失敗した外部 API 呼び出しの回数。"""

    @property
    def m_over_n(self) -> float:
        return self.cluster_count / self.question_count

    @property
    def other_ratio(self) -> float:
        return self.other_count / self.question_count

    @property
    def max_cluster_ratio(self) -> float:
        return self.max_cluster_size / self.question_count


def measure(partition: Partition, failure_count: int) -> ClusterMetrics:
    """`partition` のメトリクスを計算する。問題が0問ではどの比率も定義できない。"""
    sizes = [len(group.question_ids) for group in partition.groups]
    if partition.other:
        sizes.append(len(partition.other))
    if not sizes:
        raise ValueError("問題が0問ではメトリクスを定義できない")
    return ClusterMetrics(
        question_count=sum(sizes),
        cluster_count=len(sizes),
        other_count=len(partition.other),
        max_cluster_size=max(sizes),
        failure_count=failure_count,
    )


def log_summary(metrics: ClusterMetrics) -> None:
    """実行サマリをログに出す。数値だけを載せ、教材本文は載せない(NFR-2)。"""
    logger.info(
        "clustering summary: n=%d m=%d m_over_n=%.4f other_ratio=%.4f max_cluster_ratio=%.4f failure_count=%d",
        metrics.question_count,
        metrics.cluster_count,
        metrics.m_over_n,
        metrics.other_ratio,
        metrics.max_cluster_ratio,
        metrics.failure_count,
    )


def degeneracy_violations(metrics: ClusterMetrics) -> tuple[str, ...]:
    """退化ガードに反した項目を返す。空なら通過。"""
    violations = []
    if metrics.cluster_count <= 1:
        violations.append(f"クラスタが {metrics.cluster_count} 個しかない")
    if metrics.max_cluster_ratio > MAX_CLUSTER_RATIO:
        violations.append(f"最大クラスタ比率 {metrics.max_cluster_ratio:.3f} が {MAX_CLUSTER_RATIO} を超える")
    if metrics.other_ratio > MAX_OTHER_RATIO:
        violations.append(f"「その他」比率 {metrics.other_ratio:.3f} が {MAX_OTHER_RATIO} を超える")
    return tuple(violations)
