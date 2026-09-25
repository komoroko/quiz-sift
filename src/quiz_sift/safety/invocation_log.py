"""`claude` の子プロセス1本の逐語ログ(NFR-2、NFR-3、NFR-8 (h)、ADR-018)。

全文を後からマスクする実装にしない(AR-42)。`InvocationRecord` が項目を別々のフィールドとして持ち、
ログはそのレンダリングとして作る。stdin(教材本文を含むプロンプト)は sha256 と長さだけを持ち、
本文はどのフィールドにも入らない。stdout / stderr は構築時に教材本文(`MaterialTextFilter`)と
認証情報(`mask_secrets`)を伏せた文字列として持つ。
"""

import hashlib
import json
import logging
from dataclasses import asdict, dataclass

from quiz_sift.ports.llm import TokenUsage
from quiz_sift.ports.metering import FailureKind, RateLimitInfo
from quiz_sift.safety.log_filter import MaterialTextFilter
from quiz_sift.safety.secret_mask import mask_secrets

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InvocationRecord:
    """子プロセス1本の記録。`record_invocation` で構築する。"""

    argv: tuple[str, ...]
    exit_code: int | None
    """子の終了コード。起動失敗とタイムアウトでは `None`。"""
    duration_sec: float
    usage: TokenUsage | None
    failure_kind: FailureKind | None
    result_is_error: bool | None
    rate_limit_first: RateLimitInfo | None
    rate_limit_last: RateLimitInfo | None
    rate_limit_event_count: int
    overage_seen: bool
    stdin_sha256: str
    stdin_len: int
    """stdin の UTF-8 バイト数(`stdin_sha256` と同じバイト列の長さ)。"""
    stdout_masked: str
    stderr_masked: str

    def render(self) -> str:
        """ログ1行ぶんの JSON。フィールドをそのまま項目として出す。"""
        fields = asdict(self)
        fields["failure_kind"] = None if self.failure_kind is None else self.failure_kind.value
        return json.dumps(fields, ensure_ascii=False)


def record_invocation(
    *,
    argv: tuple[str, ...],
    exit_code: int | None,
    duration_sec: float,
    usage: TokenUsage | None,
    failure_kind: FailureKind | None,
    result_is_error: bool | None,
    rate_limit_first: RateLimitInfo | None,
    rate_limit_last: RateLimitInfo | None,
    rate_limit_event_count: int,
    overage_seen: bool,
    stdin: str,
    stdout: bytes,
    stderr: bytes,
    material: MaterialTextFilter,
) -> InvocationRecord:
    """stdin をダイジェストと長さに、stdout / stderr を伏せた文字列にして記録を作る。"""
    stdin_bytes = stdin.encode("utf-8")
    return InvocationRecord(
        argv=tuple(_mask(arg, material) for arg in argv),
        exit_code=exit_code,
        duration_sec=duration_sec,
        usage=usage,
        failure_kind=failure_kind,
        result_is_error=result_is_error,
        rate_limit_first=rate_limit_first,
        rate_limit_last=rate_limit_last,
        rate_limit_event_count=rate_limit_event_count,
        overage_seen=overage_seen,
        stdin_sha256=hashlib.sha256(stdin_bytes).hexdigest(),
        stdin_len=len(stdin_bytes),
        stdout_masked=_mask(_decode(stdout), material),
        stderr_masked=_mask(_decode(stderr), material),
    )


def log_invocation(record: InvocationRecord) -> None:
    """記録をログへ出す。子プロセスの出力がログへ届く経路はこれだけにする。"""
    logger.info("claude invocation %s", record.render())


def _decode(data: bytes) -> str:
    # 記録は子の出力をそのまま写すものなので、UTF-8 でないバイトも落とさずエスケープして残す。
    return data.decode("utf-8", errors="backslashreplace")


def _mask(text: str, material: MaterialTextFilter) -> str:
    return mask_secrets(material.mask(text))
