"""正解の同定根拠(R-2 / AR-4)。

全問に `AnswerEvidence(selector_kind, element_path)` を記録し、「根拠の要素種別が全問で同一」を
`len({e.selector_kind for e in evidences}) == 1` で判定する。

`selector_kind` は**実行時にどの抽出規則が実際にマッチしたか**から導出する。定数にすると集合サイズは
常に 1 で、この判定は入力に依存しない恒真式になる。正解マーク要素が無いときに受験者の選択へ
フォールバックする枝は、正解マークの枝とは別の値を返す — 同じ値を書けば、誤答を正解として
取り込んだまま判定が緑になる。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


class SelectorKind(Enum):
    """正解の同定にマッチした抽出規則。フォールバックの枝ごとに1つ。"""

    CORRECT_MARK = "correct-mark"
    """選択肢に付いた正解マーク。本来の根拠。"""
    USER_SELECTED = "user-selected"
    """正解マークが無く、受験者が選んだ選択肢を正解とみなした。受験者が誤答していれば誤る。"""


@dataclass(frozen=True, slots=True)
class AnswerEvidence:
    """1問の正解を何から同定したか。本文は持たない。"""

    selector_kind: SelectorKind
    element_path: str


def selector_kind_is_uniform(evidences: Iterable[AnswerEvidence]) -> bool:
    """根拠の要素種別が全問で同一か。根拠が1件も無ければ偽(判定できないものを緑にしない)。"""
    return len({evidence.selector_kind for evidence in evidences}) == 1
