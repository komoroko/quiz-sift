"""呼び出し経路自身の固定オーバーヘッド(NFR-7 / AR-41 / AR-51 / AR-76)。

定数はモデルごとに1ターンあたりのトークン数として持つ。値は既知長の合成プロンプトを
k = 1, 2, 3 回反復した較正表から回帰で求め、切片を実測の `num_turns` で割って出す(T-046)。
較正表は `docs/test/measurements/overhead-<model>.md`、採取は `tests/measurements/measure_overhead.py`。
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


_SKILLS_2026_09_26 = "ed36efc60ca4981b830e199aeebe39322fbc8a3e750e0b39b9daeae6afcd2774"

OVERHEAD: Mapping[str, OverheadConstant] = MappingProxyType(
    {
        # 切片 921.0 / num_turns 2。
        "claude-opus-5-5": OverheadConstant(
            model="claude-opus-5-5",
            per_turn_tokens=460.5,
            bucket="cache_creation_input_tokens",
            measured_at=date(2026, 9, 26),
            cli_version="2.1.283",
            argv_digest="caf33f6f31c8a4b02c627ca95209cc48dc1b4e616984c79549e4bcaa0d773954",
            skills_digest=_SKILLS_2026_09_26,
        ),
        # 切片 1115.0 / num_turns 2。
        "claude-haiku-4-5": OverheadConstant(
            model="claude-haiku-4-5",
            per_turn_tokens=557.5,
            bucket="input_tokens",
            measured_at=date(2026, 9, 26),
            cli_version="2.1.283",
            argv_digest="c5cbab94561ebe3d3edcf126768ead3229bbc5e135501e9f669f2c728f96b11b",
            skills_digest=_SKILLS_2026_09_26,
        ),
    }
)
"""モデル ID → 定数。値は較正表の回帰から転記する(T-046)。"""
