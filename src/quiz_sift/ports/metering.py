"""実行1回分の計測の口(NFR-7)。

`UsageRecorder` は「モデルと4値トークン」しか運ばないので、呼び出し回数の3語
(`llm_calls` / `provider_invocations` / `model_turns`)・申告コスト・レート枠・失敗の分類を
渡す上位 Protocol `RunMeter` をここに置く。`ports/llm.py` は変えない。

`FailureKind` もここに置く(D-104)。`record_failure` の引数なので、adapters に置くと ports が
adapters を import する逆向きの依存になる。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from quiz_sift.ports.llm import TokenUsage, UsageRecorder

MAX_CONCURRENCY = 1
"""同時に起動する子プロセスの最大数(D-100)。並列化しても枠は増えないので直列で凍結する。"""


class FailureKind(Enum):
    """呼び出し失敗の7種。判定順序は adapters 側の判定表が持つ。

    レート制限は特別扱いせず `PROVIDER_ERROR` に数える(D-091 撤回)。
    """

    SPAWN = "spawn"
    TIMEOUT = "timeout"
    PARSE = "parse"
    SCHEMA = "schema"
    TRUNCATED = "truncated"
    INCOMPLETE_IDS = "incomplete_ids"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True, slots=True)
class QuotaWindow:
    """レート枠の1ウィンドウ(`five_hour` / `seven_day`)。"""

    utilization: float
    resets_at: int
    """`resetsAt`(epoch 秒)。"""


@dataclass(frozen=True, slots=True)
class RateLimitInfo:
    """`rate_limit_event` の `rate_limit_info` を生のまま写したもの。"""

    status: str
    """観測できた値は `allowed_warning` だけ。枠切れ時の値は未観測(D-110)。"""
    resets_at: int | None
    five_hour: QuotaWindow | None
    seven_day: QuotaWindow | None


ALLOWED_STATUS = "allowed_warning"
"""成功した呼び出しにも付いてくる既知の許可状態。これ以外はレート制限で止まった印と読む(D-110)。"""


@dataclass(frozen=True, slots=True)
class InvocationScope:
    """`begin_invocation` が開いた子プロセス1本。何本目かと、その argv のダイジェストを持つ。"""

    index: int
    argv_digest: str


class RunMeter(UsageRecorder, Protocol):
    def begin_call(self, method: str) -> None:
        """`Llm` のメソッド呼び出し1回(`llm_calls`)。"""
        ...

    def begin_invocation(self, argv_digest: str) -> InvocationScope:
        """子プロセス1本の起動(`provider_invocations`)。以降の記録はこの1本に属する(D-107)。"""
        ...

    def end_invocation(self, model: str, turns: int | None, usage: TokenUsage | None, cost_usd: float | None) -> None:
        """子プロセス1本の結果。**成否の判定より先に**呼ぶ。取れなかった値は `None` で渡す。

        `cost_usd` はエンベロープの `total_cost_usd`(D-114)。
        """
        ...

    def record_quota(self, info: RateLimitInfo) -> None:
        """観測したレート枠。最初の値が開始時、最後の値が終了時になる(D-114)。"""
        ...

    def record_failure(self, kind: FailureKind) -> None:
        """いま開いている子プロセスの失敗。"""
        ...
