"""呼び出し経路自身の固定オーバーヘッド(NFR-7 / AR-41 / AR-51 / AR-76)。

定数はモデルごとに1ターンあたりのトークン数として持つ。値は既知長の合成プロンプトを
k = 1, 2, 3 回反復した較正表から回帰で求め、切片を実測の `num_turns` で割って出す(T-030)。
`argv_digest` が実行時の argv と違えば、その定数は使わず `unknown` と報告する。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class OverheadConstant:
    model: str
    per_turn_tokens: float
    bucket: str
    """計上先のトークンの区分(`TokenUsage` のフィールド名)。"""
    measured_at: date
    cli_version: str
    argv_digest: str
    """測定に使った argv のダイジェスト(プロンプト・スキーマ本文を除いた形)。"""


OVERHEAD: Mapping[str, OverheadConstant] = MappingProxyType({})
"""モデル ID → 定数。実測前なので空(T-030 が埋める)。"""
