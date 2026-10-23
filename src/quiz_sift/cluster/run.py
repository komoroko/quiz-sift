"""クラスタリングステージの結線(R-3 / AR-5 / S-1)。

分類 → 排他分割 → メトリクス(実行サマリをログへ)→ 失敗があれば止める → 要約と検査 → 書き出し。

**外部呼び出しが1回でも失敗した実行は下流へ進ませない**。失敗した分割呼び出しの問題は
「その他」に落ちるので、排他分割も M/N も成立したまま、その他比率だけが膨らんだ出力が先へ流れて
しまう。サマリは止める前に出すので、失敗件数とその他比率は止まった実行でも読める。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from quiz_sift.cluster.assign import partition
from quiz_sift.cluster.metrics import ClusterMetrics, log_summary, measure
from quiz_sift.cluster.summary import SummaryLlm, emit_summarized
from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.ports.llm import ClassificationResult


class ClusterLlm(SummaryLlm, Protocol):
    """`ports.llm.Llm` のうちクラスタリングステージが使う部分。`Llm` の実装はそのまま渡せる。"""

    def classify_clusters(self, deck: Deck) -> ClassificationResult: ...


@dataclass(frozen=True, slots=True, repr=False)
class Clustering:
    """クラスタリングステージの成果物。"""

    clusters: tuple[Cluster, ...]
    metrics: ClusterMetrics


class ClassificationFailed(SystemExit):
    """分類の外部呼び出しに失敗があった。捕まえずに上げれば非0で終了する。"""

    def __init__(self, metrics: ClusterMetrics) -> None:
        super().__init__(f"分類の外部 API 呼び出しが {metrics.failure_count} 回失敗したため下流へ進まない")
        self.metrics = metrics


def cluster_deck[T](llm: ClusterLlm, deck: Deck, emit: Callable[[Clustering], T]) -> T:
    """`deck` をクラスタリングし、すべての検査を通ったときだけ `emit` に成果物を書かせる。"""
    result = llm.classify_clusters(deck)
    split = partition(deck, result.assignments)
    metrics = measure(split, failure_count=len(result.failures))
    log_summary(metrics)
    if result.failures:
        raise ClassificationFailed(metrics)
    return emit_summarized(llm, deck, split, lambda clusters: emit(Clustering(clusters, metrics)))
