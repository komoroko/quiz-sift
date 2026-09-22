"""学習記録(R-8 / R-10)。

`SessionId` も `QuestionId` も `deck_id` を持つので、記録はデッキ単位で分離される。
ただし**それだけでは足りない**(AR-18): 集計クエリの `WHERE deck_id = ?` の書き忘れは
型では捕まらないので、ストアへのアクセスは `DeckScopedStore` 経由に限る(T-005)。
"""

from dataclasses import dataclass
from datetime import datetime

from quiz_sift.domain.ids import ClusterId, OptionId, QuestionId, SessionId


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """1問への回答1件。"""

    session_id: SessionId
    question_id: QuestionId
    chosen: OptionId
    correct: bool
    elapsed_seconds: float
    answered_at: datetime

    def __post_init__(self) -> None:
        if self.chosen.question_id != self.question_id:
            raise ValueError("別の問題の選択肢を選んだことになっている")
        if self.session_id.deck_id != self.question_id.deck_id:
            raise ValueError("別デッキのセッションに記録しようとしている")
        if self.elapsed_seconds < 0:
            raise ValueError("所要時間が負の値になっている")


@dataclass(frozen=True, slots=True)
class ClusterStat:
    """1クラスタについての集計。R-10 の誤答率降順の並べ替えの入力になる。"""

    cluster_id: ClusterId
    presented: int
    wrong: int
    last_studied_at: datetime | None
    """まだ1度も出題していないクラスタは `None`。"""

    def __post_init__(self) -> None:
        if self.presented < 0 or self.wrong < 0:
            raise ValueError("回数に負の値がある")
        if self.wrong > self.presented:
            raise ValueError("誤答回数が出題回数を超えている")
        if self.presented == 0 and self.last_studied_at is not None:
            raise ValueError("出題していないのに最終学習日時がある")
