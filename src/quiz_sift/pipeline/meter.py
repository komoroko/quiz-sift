"""処理コストと所要時間の計測(NFR-7)。

実行1回ごとに、設計 NFR-7 節の「ログのフィールド一覧」を JSON Lines の1行として追記する。
推定コストの算出根拠を**ログだけから**追跡できるよう、モデル別の内訳トークンと、計算に使った
単価表(`priced_at` と取得元つき)を同じ行に書く。

トークンは input / output の2値に畳まない(AR-29)。単価表も同じ粒度で持つ。
コストは公開単価表からの名目値(notional)で、支払った額を主張しない(NFR-8)。
失敗した呼び出しのトークンも数える(AR-65)。単価表に無いモデルでも例外にしない。
"""

import json
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from datetime import date
from functools import reduce
from pathlib import Path
from types import MappingProxyType

from quiz_sift.pipeline.overhead import OVERHEAD, OverheadConstant
from quiz_sift.ports.clock import Clock
from quiz_sift.ports.llm import TokenUsage
from quiz_sift.ports.metering import (
    ALLOWED_STATUS,
    MAX_CONCURRENCY,
    FailureKind,
    InvocationScope,
    QuotaWindow,
    RateLimitInfo,
)

_PER = 1_000_000
"""単価表の単位(USD per 1M tokens)。"""

PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"

DISCLAIMER = "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """1モデルの単価(USD per 1M tokens)。`priced_at` は単価を確かめた日、`source` はその取得元。"""

    input_per_mtok: float
    cache_write_per_mtok: float
    """5 分 TTL のキャッシュ書き込み。"""
    cache_read_per_mtok: float
    output_per_mtok: float
    """思考トークンを含む出力の単価。"""
    priced_at: date
    source: str

    def cost(self, usage: TokenUsage) -> float:
        return (
            usage.input_tokens * self.input_per_mtok
            + usage.cache_creation_input_tokens * self.cache_write_per_mtok
            + usage.cache_read_input_tokens * self.cache_read_per_mtok
            + usage.output_tokens * self.output_per_mtok
        ) / _PER

    def has_zero(self) -> bool:
        return 0 in (self.input_per_mtok, self.cache_write_per_mtok, self.cache_read_per_mtok, self.output_per_mtok)


PRICES: Mapping[str, ModelPrice] = MappingProxyType(
    {
        # 書き込みは入力の 1.25 倍。読み出しは Opus 5.5 だけ 0.05 倍、他は 0.1 倍(料金ページの脚注)。
        "claude-opus-5-5": ModelPrice(
            input_per_mtok=4.00,
            cache_write_per_mtok=5.00,
            cache_read_per_mtok=0.20,
            output_per_mtok=20.00,
            priced_at=date(2026, 9, 25),
            source=PRICING_SOURCE,
        ),
        "claude-haiku-4-5": ModelPrice(
            input_per_mtok=1.00,
            cache_write_per_mtok=1.25,
            cache_read_per_mtok=0.10,
            output_per_mtok=5.00,
            priced_at=date(2026, 9, 25),
            source=PRICING_SOURCE,
        ),
    }
)

_ZERO = TokenUsage(0, 0, 0, 0)


def _add(a: TokenUsage, b: TokenUsage) -> TokenUsage:
    return TokenUsage(*(getattr(a, f.name) + getattr(b, f.name) for f in fields(TokenUsage)))


def _window(window: QuotaWindow | None) -> dict[str, object] | None:
    return None if window is None else {"utilization": window.utilization, "resetsAt": window.resets_at}


def _quota(info: RateLimitInfo | None) -> dict[str, object] | None:
    if info is None:
        return None
    return {
        "status": info.status,
        "resetsAt": info.resets_at,
        "five_hour": _window(info.five_hour),
        "seven_day": _window(info.seven_day),
    }


class Meter:
    """実行1回分の計測器。`ports.metering.RunMeter` を満たし、LLM 実装に結線して使う。"""

    def __init__(
        self,
        clock: Clock,
        *,
        prices: Mapping[str, ModelPrice] = PRICES,
        overhead: Mapping[str, OverheadConstant] = OVERHEAD,
    ) -> None:
        self.run_id = uuid.uuid4().hex
        self._clock = clock
        self._prices = prices
        self._overhead = overhead
        self._started_at = clock.now()
        self._started = clock.monotonic()
        self._llm_calls = 0
        self._invocation: InvocationScope | None = None
        self._invocation_quota: RateLimitInfo | None = None
        self._calls: dict[str, int] = {}
        self._tokens: dict[str, TokenUsage] = {}
        self._turns: dict[str, int] = {}
        self._digests: dict[str, set[str]] = {}
        self._declared: float | None = None
        self._without_usage = 0
        self._without_turns = 0
        self._failures: Counter[FailureKind] = Counter()
        self._stopping_failure: tuple[FailureKind, RateLimitInfo | None] | None = None
        self._quota_at_start: RateLimitInfo | None = None
        self._quota_at_end: RateLimitInfo | None = None
        self._finished = False

    def record(self, model: str, usage: TokenUsage) -> None:
        self._tokens[model] = _add(self._tokens.get(model, _ZERO), usage)
        self._calls[model] = self._calls.get(model, 0) + 1

    def begin_call(self, method: str) -> None:
        self._llm_calls += 1

    def begin_invocation(self, argv_digest: str) -> InvocationScope:
        index = 0 if self._invocation is None else self._invocation.index + 1
        self._invocation = InvocationScope(index=index, argv_digest=argv_digest)
        self._invocation_quota = None
        return self._invocation

    def end_invocation(self, model: str, turns: int | None, usage: TokenUsage | None, cost_usd: float | None) -> None:
        if usage is None:
            self._without_usage += 1
        else:
            self.record(model, usage)
        if turns is None:
            self._without_turns += 1
        else:
            self._turns[model] = self._turns.get(model, 0) + turns
        if cost_usd is not None:
            self._declared = (self._declared or 0.0) + cost_usd
        if self._invocation is not None:
            self._digests.setdefault(model, set()).add(self._invocation.argv_digest)

    def record_quota(self, info: RateLimitInfo) -> None:
        if self._quota_at_start is None:
            self._quota_at_start = info
        self._quota_at_end = info
        self._invocation_quota = info

    def record_failure(self, kind: FailureKind) -> None:
        self._failures[kind] += 1
        # 失敗は下流へ進ませないので、最後に記録された失敗が実行を止めた失敗になる。
        self._stopping_failure = (kind, self._invocation_quota)

    def finish(self, log_path: Path) -> dict[str, object]:
        """実行を締め、1行を `log_path` に追記して、書いた内容を返す。"""
        if self._finished:
            raise RuntimeError("この計測はもう締めてある")
        self._finished = True
        entry: dict[str, object] = {
            "run_id": self.run_id,
            "started_at": self._started_at.isoformat(),
            "finished_at": self._clock.now().isoformat(),
            "elapsed_sec": self._clock.monotonic() - self._started,
            "llm_calls": self._llm_calls,
            "provider_invocations": 0 if self._invocation is None else self._invocation.index + 1,
            "model_turns": sum(self._turns.values()),
            "turns_by_model": dict(self._turns),
            "max_concurrency": MAX_CONCURRENCY,
            "tokens": asdict(reduce(_add, self._tokens.values(), _ZERO)),
            "tokens_by_model": {model: asdict(tokens) for model, tokens in self._tokens.items()},
            "calls_by_model": {
                model: {"calls": self._calls[model], **asdict(tokens)} for model, tokens in self._tokens.items()
            },
            "estimated_cost": self._estimated_cost(),
            "disclaimer": DISCLAIMER,
            "prices": {
                model: {**asdict(price), "priced_at": price.priced_at.isoformat()}
                for model, price in self._prices.items()
            },
            "overhead": self._overhead_by_model(),
            "failures_by_kind": {kind.value: self._failures[kind] for kind in FailureKind},
            "invocations_without_usage": self._without_usage,
            "invocations_without_turns": self._without_turns,
            "ended_by_rate_limit": self._ended_by_rate_limit(),
            "quota_at_start": _quota(self._quota_at_start),
            "quota_at_end": _quota(self._quota_at_end),
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def _estimated_cost(self) -> dict[str, object]:
        cost: dict[str, object] = {"notional": True, "currency": "USD", "declared": self._declared}
        # 単価が 0 の表で「記録済み」と言うと、常に 0.0 を返す実装と見分けがつかない(AR-36)。
        if any(model not in self._prices for model in self._tokens):
            return {**cost, "status": "unavailable", "reason": "unknown_model", "computed": None, "relative_diff": None}
        if any(self._prices[model].has_zero() for model in self._tokens):
            return {**cost, "status": "unavailable", "reason": "zero_price", "computed": None, "relative_diff": None}
        by_model = {model: self._prices[model].cost(tokens) for model, tokens in self._tokens.items()}
        computed = sum(by_model.values())
        relative_diff = None if self._declared is None or computed == 0 else (self._declared - computed) / computed
        return {
            **cost,
            "status": "recorded",
            "computed": computed,
            "relative_diff": relative_diff,
            "by_model": by_model,
        }

    def _overhead_by_model(self) -> dict[str, dict[str, object]]:
        # material(合計 − Σ overhead×turns)は置かない(D-116)。
        out: dict[str, dict[str, object]] = {}
        for model, turns in self._turns.items():
            constant = self._overhead.get(model)
            seen = sorted(self._digests.get(model, set()))
            if constant is None:
                out[model] = {"status": "unknown", "reason": "no_constant", "argv_digests": seen, "turns": turns}
            elif seen != [constant.argv_digest]:
                out[model] = {"status": "unknown", "reason": "argv_mismatch", "argv_digests": seen, "turns": turns}
            else:
                out[model] = {
                    "status": "recorded",
                    "per_turn_tokens": constant.per_turn_tokens,
                    "bucket": constant.bucket,
                    "measured_at": constant.measured_at.isoformat(),
                    "cli_version": constant.cli_version,
                    "argv_digest": constant.argv_digest,
                    "turns": turns,
                }
        return out

    def _ended_by_rate_limit(self) -> dict[str, object]:
        # D-110: status と resetsAt は真偽に関わらず生のまま残し、人が読み直せるようにする。
        if self._stopping_failure is None:
            return {"value": False, "status": None, "resetsAt": None}
        kind, info = self._stopping_failure
        value = kind is FailureKind.PROVIDER_ERROR and info is not None and info.status != ALLOWED_STATUS
        return {
            "value": value,
            "status": None if info is None else info.status,
            "resetsAt": None if info is None else info.resets_at,
        }
