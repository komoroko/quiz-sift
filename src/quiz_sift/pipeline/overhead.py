"""呼び出し経路自身の固定オーバーヘッド(NFR-7 / AR-41 / AR-51 / AR-76)。

定数はモデルごとに1ターンあたりのトークン数として持つ。値は既知長の合成プロンプトを
k = 1, 2, 3 回反復した較正表から回帰で求め、切片を実測の `num_turns` で割って出す(T-030)。
`argv_digest` か `skills_digest` が実行時の値と違えば、その定数は使わず `unknown` と報告する。
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
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
    skills_digest: str
    """測定時の `init` イベントの skills 一覧のダイジェスト(`skills_digest()`。D-126)。"""


def skills_digest(names: Sequence[str] | None) -> str:
    """skills の名前を並べ替えた JSON 配列の sha256。キーが無ければ固定値 `"absent"` のダイジェスト(D-141)。

    名前が同じまま説明文だけ変わる同期は検知できない。
    """
    payload = "absent" if names is None else json.dumps(sorted(names))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


OVERHEAD: Mapping[str, OverheadConstant] = MappingProxyType({})
"""モデル ID → 定数。実測前なので空(T-030 が埋める)。"""
