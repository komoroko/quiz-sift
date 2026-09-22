"""クラスタ — 1デッキ内の問題のグループ(R-3)。"""

from dataclasses import dataclass

from quiz_sift.domain.ids import ClusterId, QuestionId


@dataclass(frozen=True, slots=True, repr=False)
class Cluster:
    """排他分割の1グループ。

    `ClusterId` が `deck_id` を持ち、構成員はその `deck_id` の問題に限られる。
    別デッキの問題を含むクラスタは構築できない(R-11 の第1基準)。
    """

    cluster_id: ClusterId
    summary: str
    question_ids: tuple[QuestionId, ...]

    def __post_init__(self) -> None:
        if not self.question_ids:
            raise ValueError("空のクラスタは作れない")
        foreign = [qid for qid in self.question_ids if qid.deck_id != self.cluster_id.deck_id]
        if foreign:
            raise ValueError(f"別デッキの問題が混ざっている: {[qid.deck_id for qid in foreign]}")
        if len(set(self.question_ids)) != len(self.question_ids):
            raise ValueError("問題 ID が重複している")
