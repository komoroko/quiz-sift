"""取り込み結果のレポート(R-1)。

保存則 `accepted + rejected == input_total` を `__post_init__` の不変条件に置く。
テストで確かめる性質ではなく、壊すと構築できない構造にしておく。
"""

from dataclasses import dataclass
from enum import Enum

from quiz_sift.domain.question import Question


class RejectionReason(Enum):
    """問題1件を取り込まなかった理由。"""

    OPTION_COUNT_NOT_4 = "option_count_not_4"
    ANSWER_UNRESOLVED = "answer_unresolved"
    DUPLICATE_ID = "duplicate_id"


@dataclass(frozen=True, slots=True, repr=False)
class IngestOk:
    """取り込めた1件。"""

    question: Question


@dataclass(frozen=True, slots=True)
class IngestRejected:
    """取り込まなかった1件。本文は持たず、入力上の ID と理由だけを残す。"""

    raw_id: str
    reason: RejectionReason


IngestOutcome = IngestOk | IngestRejected
"""入力1件に対する結果。取り込み処理はこれを1件ずつ返す。"""


@dataclass(frozen=True, slots=True)
class IngestReport:
    """1デッキ分の取り込みレポート。

    公式解説の有無の件数も報告する(AR-10)。NFR-1 の照合先がどれだけあるかは
    承認前に知れなければならず、R-5 の `no_reference` の前提でもある。
    """

    input_total: int
    accepted: int
    rejected: tuple[IngestRejected, ...]
    with_explanation: int
    without_explanation: int

    def __post_init__(self) -> None:
        if self.accepted + len(self.rejected) != self.input_total:
            raise ValueError(
                f"保存則が破れている: accepted={self.accepted} + rejected={len(self.rejected)} "
                f"!= input_total={self.input_total}"
            )
        if self.with_explanation + self.without_explanation != self.accepted:
            raise ValueError(
                f"解説の内訳が accepted と合わない: {self.with_explanation} + {self.without_explanation} "
                f"!= {self.accepted}"
            )
        counts = (self.input_total, self.accepted, self.with_explanation, self.without_explanation)
        if any(n < 0 for n in counts):
            raise ValueError("件数に負の値がある")
