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
    """値域は `allowed` / `allowed_warning` / `rejected`(claude 2.1.282 のスキーマ。枠切れの実物は未観測。D-128)。"""
    resets_at: int | None
    five_hour: QuotaWindow | None
    seven_day: QuotaWindow | None
    rate_limit_type: str | None
    is_using_overage: bool | None
    """`isUsingOverage`。キーが無ければ `None` で、「使っていない」とは読まない。"""
    overage_status: str | None


def rate_limit_stop(kind: FailureKind, is_error: bool | None, last: RateLimitInfo | None) -> bool:
    """失敗した子1本がレート枠で止まったか(D-128、D-135)。

    `last` はその子自身が最後に出した `rate_limit_event`。並行度に関係なく子ごとに評価する(AR-113a)。
    """
    return (
        kind is FailureKind.PROVIDER_ERROR
        and is_error is True
        and last is not None
        and last.status == "rejected"
        and last.is_using_overage is False
    )


@dataclass(frozen=True, slots=True)
class InvocationScope:
    """`begin_invocation` が開いた子プロセス1本。何本目かと、その argv のダイジェストを持つ。"""

    index: int
    argv_digest: str


class RunMeter(UsageRecorder, Protocol):
    def begin_call(self, method: str) -> None:
        """`Llm` のメソッド呼び出し1回(`llm_calls`)。"""
        ...

    def begin_invocation(self, argv_digest: str, *, reissue: bool = False) -> InvocationScope:
        """子プロセス1本の起動(`provider_invocations`)。以降の記録はこの1本に属する(D-107)。

        `reissue` が真なら、直前の `TRUNCATED` の子の出し直し(D-231)。出し直された子はレート枠による停止の
        判定から外れ、`failures_by_kind` には残る(D-241)。
        """
        ...

    def end_invocation(
        self,
        model: str,
        turns: int | None,
        usage: TokenUsage | None,
        cost_usd: float | None,
        is_error: bool | None,
        skills_digest: str | None,
    ) -> None:
        """子プロセス1本の結果。**成否の判定より先に**呼ぶ。取れなかった値は `None` で渡す。

        `cost_usd` はエンベロープの `total_cost_usd`(D-114)、`is_error` は result の `is_error`、
        `skills_digest` は `init` イベントの skills 一覧のダイジェスト(D-141)。
        """
        ...

    def record_quota(self, info: RateLimitInfo) -> None:
        """観測したレート枠。開いている子プロセスに属し、実行で最初の値が開始時、最後の値が終了時になる(D-114)。"""
        ...

    def record_failure(self, kind: FailureKind) -> None:
        """いま開いている子プロセスの失敗。"""
        ...
