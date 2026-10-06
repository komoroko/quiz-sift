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


_SKILLS_2026_10_06 = "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14"

OVERHEAD: Mapping[str, OverheadConstant] = MappingProxyType(
    {
        # 切片 1060.3333333333335 / num_turns 2。
        "claude-opus-5-5": OverheadConstant(
            model="claude-opus-5-5",
            per_turn_tokens=530.1666666666667,
            bucket="cache_creation_input_tokens",
            measured_at=date(2026, 10, 6),
            cli_version="2.1.290",
            argv_digest="7be15268ad5b8c2990489105260475a37ed97b7305bfc1a6706663709d9fde86",
            skills_digest=_SKILLS_2026_10_06,
        ),
        # 切片 1224.3333333333335 / num_turns 2。
        "claude-haiku-4-5": OverheadConstant(
            model="claude-haiku-4-5",
            per_turn_tokens=612.1666666666667,
            bucket="input_tokens",
            measured_at=date(2026, 10, 6),
            cli_version="2.1.290",
            argv_digest="66c1b01769d3385b32422acb877cdeef860ff266d144d816e701b7a7bab0dcb2",
            skills_digest=_SKILLS_2026_10_06,
        ),
    }
)
"""モデル ID → 定数。値は較正表の回帰から転記する(T-046)。"""
