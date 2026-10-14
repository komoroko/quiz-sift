"""処理コストと所要時間の計測(NFR-7)。

実行1回ごとに、設計 NFR-7 節の「ログのフィールド一覧」の開始行と終了行を JSON Lines で追記する。
推定コストの算出根拠を**ログだけから**追跡できるよう、モデル別の内訳トークンと、計算に使った
単価表(`priced_at` と取得元つき)を同じ行に書く。

トークンは input / output の2値に畳まない(AR-29)。単価表も同じ粒度で持つ。
コストは公開単価表からの名目値(notional)で、支払った額を主張しない(NFR-8)。
失敗した呼び出しのトークンも数える(AR-65)。単価表に無いモデルでも例外にしない。
レート枠による停止と overage は子プロセスごとに、その子自身の `rate_limit_event` で判定する(D-128、AR-113a)。
"""

import json
import os
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass, fields
from datetime import date
from functools import reduce
from pathlib import Path
from types import MappingProxyType

from quiz_sift.pipeline.overhead import OVERHEAD, OverheadConstant
from quiz_sift.pipeline.run_sequence import DISCLAIMER, aggregate, read_log
from quiz_sift.ports.clock import Clock
from quiz_sift.ports.llm import TokenUsage
from quiz_sift.ports.metering import (
    MAX_CONCURRENCY,
    FailureKind,
    InvocationScope,
    QuotaWindow,
    RateLimitInfo,
    rate_limit_stop,
)

_PER = 1_000_000
"""単価表の単位(USD per 1M tokens)。"""

PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"

CACHE_MODES = ("fresh", "resume", "normal")

METERED_ENV_NAMES = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_REGION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "CLOUD_ML_REGION",
    }
)
"""NFR-8 が列挙した metered 認証変数(AR-48)。開始行には名前だけを書き、値は書かない(NFR-3)。"""


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
        # 書き込みは入力の 1.25 倍。読み出しは Opus 5.5 と Sonnet 5.5 は 0.05 倍、他は 0.1 倍(料金ページの脚注)。
        "claude-opus-5-5": ModelPrice(
            input_per_mtok=4.00,
            cache_write_per_mtok=5.00,
            cache_read_per_mtok=0.20,
            output_per_mtok=20.00,
            priced_at=date(2026, 9, 25),
            source=PRICING_SOURCE,
        ),
        "claude-sonnet-5-5": ModelPrice(
            input_per_mtok=2.00,
            cache_write_per_mtok=2.50,
            cache_read_per_mtok=0.10,
            output_per_mtok=10.00,
            priced_at=date(2026, 10, 13),
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
        "rateLimitType": info.rate_limit_type,
        "isUsingOverage": info.is_using_overage,
        "overageStatus": info.overage_status,
        "resetsAt": info.resets_at,
        "five_hour": _window(info.five_hour),
        "seven_day": _window(info.seven_day),
    }


@dataclass(slots=True)
class _Invocation:
    """子プロセス1本について、レート枠による停止と overage の判定に要るもの。"""

    argv_digest: str
    failure: FailureKind | None = None
    is_error: bool | None = None
    last_quota: RateLimitInfo | None = None
    """この子自身が最後に出した `rate_limit_event`。"""
    quota_events: int = 0
    overage: bool = False
    """`isUsingOverage` が1回でも真だったか。"""
    reissued: bool = False
    """後に出し直しの子が続いたか。真ならレート枠による停止の判定から外す(D-241)。"""


class Meter:
    """実行1回分の計測器。`ports.metering.RunMeter` を満たし、LLM 実装に結線して使う。

    作った時点で開始行を、`finish` で終了行を `log_path` へ追記する。
    """

    def __init__(
        self,
        clock: Clock,
        *,
        log_path: Path,
        run_id: str,
        execution_id: str,
        stage: str,
        cache_mode: str,
        env_names: Collection[str],
        prices: Mapping[str, ModelPrice] = PRICES,
        overhead: Mapping[str, OverheadConstant] = OVERHEAD,
    ) -> None:
        if cache_mode not in CACHE_MODES:
            raise ValueError(f"cache_mode は {CACHE_MODES} のどれか: {cache_mode!r}")
        self.run_id = run_id
        self.execution_id = execution_id
        self._log_path = log_path
        self._clock = clock
        self._prices = prices
        self._overhead = overhead
        self._started_at = clock.now()
        self._started = clock.monotonic()
        self._llm_calls = 0
        self._invocations: list[_Invocation] = []
        self._calls: dict[str, int] = {}
        self._tokens: dict[str, TokenUsage] = {}
        self._turns: dict[str, int] = {}
        self._argv_digests: dict[str, set[str]] = {}
        self._skills_digests: dict[str, set[str]] = {}
        self._declared: float | None = None
        self._without_usage = 0
        self._without_turns = 0
        self._failures: Counter[FailureKind] = Counter()
        self._quota_at_start: RateLimitInfo | None = None
        self._quota_at_end: RateLimitInfo | None = None
        self._finished = False
        self._append(
            {
                "record": "start",
                "run_id": run_id,
                "execution_id": execution_id,
                "started_at": self._started_at.isoformat(),
                "cache_mode": cache_mode,
                "stage": stage,
                "env_names": sorted(env_names),
                "metered_env_names": sorted(METERED_ENV_NAMES.intersection(env_names)),
            }
        )

    def record(self, model: str, usage: TokenUsage) -> None:
        self._tokens[model] = _add(self._tokens.get(model, _ZERO), usage)
        self._calls[model] = self._calls.get(model, 0) + 1

    def begin_call(self, method: str) -> None:
        self._llm_calls += 1

    def begin_invocation(self, argv_digest: str, *, reissue: bool = False) -> InvocationScope:
        if reissue:
            if not self._invocations:
                raise RuntimeError("出し直す子プロセスがまだ無い")
            previous = self._invocations[-1]
            if previous.failure is not FailureKind.TRUNCATED:
                raise RuntimeError(f"出し直しの前の子が TRUNCATED で終わっていない: {previous.failure}")
            previous.reissued = True
        self._invocations.append(_Invocation(argv_digest))
        return InvocationScope(index=len(self._invocations) - 1, argv_digest=argv_digest)

    def end_invocation(
        self,
        model: str,
        turns: int | None,
        usage: TokenUsage | None,
        cost_usd: float | None,
        is_error: bool | None,
        skills_digest: str | None,
    ) -> None:
        invocation = self._open()
        invocation.is_error = is_error
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
        self._argv_digests.setdefault(model, set()).add(invocation.argv_digest)
        skills = self._skills_digests.setdefault(model, set())
        if skills_digest is not None:
            skills.add(skills_digest)

    def record_quota(self, info: RateLimitInfo) -> None:
        invocation = self._open()
        invocation.last_quota = info
        invocation.quota_events += 1
        invocation.overage = invocation.overage or info.is_using_overage is True
        if self._quota_at_start is None:
            self._quota_at_start = info
        self._quota_at_end = info

    def record_failure(self, kind: FailureKind) -> None:
        invocation = self._open()
        if invocation.failure is not None:
            raise RuntimeError(f"子プロセス {len(self._invocations) - 1} の失敗は記録済み: {invocation.failure}")
        invocation.failure = kind
        self._failures[kind] += 1

    def stop_seen(self) -> bool:
        """これまでの子のどれかに overage が現れたか、レート枠で止まったか(D-144)。

        真なら同じ段の残りの呼び出しを発行しない。overage の後は従量課金になり、枠で止まった後の呼び出しは
        再開時に捨てられるので、どちらも続ける利得が無い。
        """
        return any(invocation.overage or self._stopped_by_rate_limit(invocation) for invocation in self._invocations)

    def _open(self) -> _Invocation:
        """いま開いている子プロセス(最後に `begin_invocation` したもの)。"""
        if not self._invocations:
            raise RuntimeError("begin_invocation の前に子プロセスの記録が来た")
        return self._invocations[-1]

    def finish(self, *, exit_code: int, exit_reason: str | None) -> dict[str, object]:
        """実行を締め、終了行を追記して、書いた内容を返す。

        `exit_reason` は実行を止めた例外のクラス名で、例外で抜けていなければ `None`。
        """
        if self._finished:
            raise RuntimeError("この計測はもう締めてある")
        self._finished = True
        ended = self._ended_by_rate_limit()
        overage = sum(invocation.overage for invocation in self._invocations)
        without_event = sum(invocation.quota_events == 0 for invocation in self._invocations)
        if overage:
            overage_check = "failed"
        elif without_event:
            overage_check = "unknown"
        else:
            overage_check = "passed"
        if exit_code == 0:
            stop_kind = "completed"
        elif ended["value"]:
            stop_kind = "rate_limit"
        else:
            stop_kind = "other"
        entry: dict[str, object] = {
            "record": "end",
            "run_id": self.run_id,
            "execution_id": self.execution_id,
            "started_at": self._started_at.isoformat(),
            "finished_at": self._clock.now().isoformat(),
            "elapsed_sec": self._clock.monotonic() - self._started,
            "llm_calls": self._llm_calls,
            "provider_invocations": len(self._invocations),
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
            "failed_invocations": sum(invocation.failure is not None for invocation in self._invocations),
            "invocations_without_usage": self._without_usage,
            "invocations_without_turns": self._without_turns,
            "rate_limit_stopped_invocations": sum(self._stopped_by_rate_limit(i) for i in self._invocations),
            "ended_by_rate_limit": ended,
            "quota_at_start": _quota(self._quota_at_start),
            "quota_at_end": _quota(self._quota_at_end),
            "overage_invocations": overage,
            "invocations_without_rate_limit_event": without_event,
            "overage_check": overage_check,
            "stop": {"exit_code": exit_code, "exit_reason": exit_reason, "kind": stop_kind},
        }
        # その時点までの列の集約。いま書く終了行を足すので、締めている実行の開始行は閉じたものに数える(D-139)。
        entry["sequence"] = aggregate([*read_log(self._log_path), entry], self.run_id)
        self._append(entry)
        return entry

    def _append(self, entry: dict[str, object]) -> None:
        """1行を1回の write で追記し、fsync まで済ませる。

        末尾が改行でなければ(電源断で途中まで書かれた行)、改行を1つ先に書いて連結させない(AR-132)。
        """
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log_path, "a+b") as log:
            if log.seek(0, os.SEEK_END) > 0:
                log.seek(-1, os.SEEK_END)
                if log.read(1) != b"\n":
                    line = b"\n" + line
            log.write(line)
            log.flush()
            os.fsync(log.fileno())

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
            argv = sorted(self._argv_digests.get(model, set()))
            skills = sorted(self._skills_digests.get(model, set()))
            seen: dict[str, object] = {"argv_digests": argv, "skills_digests": skills, "turns": turns}
            if constant is None:
                out[model] = {"status": "unknown", "reason": "no_constant", **seen}
            elif argv != [constant.argv_digest]:
                out[model] = {"status": "unknown", "reason": "argv_mismatch", **seen}
            elif skills != [constant.skills_digest]:
                out[model] = {"status": "unknown", "reason": "skills_mismatch", **seen}
            else:
                out[model] = {
                    "status": "recorded",
                    "per_turn_tokens": constant.per_turn_tokens,
                    "bucket": constant.bucket,
                    "measured_at": constant.measured_at.isoformat(),
                    "cli_version": constant.cli_version,
                    "argv_digest": constant.argv_digest,
                    "skills_digest": constant.skills_digest,
                    "turns": turns,
                }
        return out

    @staticmethod
    def _stopped_by_rate_limit(invocation: _Invocation) -> bool:
        return invocation.failure is not None and rate_limit_stop(
            invocation.failure, invocation.is_error, invocation.last_quota
        )

    def _ended_by_rate_limit(self) -> dict[str, object]:
        # 失敗した子が1本以上あり、そのすべてが述語を満たすときだけ真(D-128)。出し直された子は回復の途中なので
        # 外す(D-241)。status と3つの resetsAt はその実行の最後のイベントの値を、真偽に関わらず生のまま残す。
        failed = [i for i in self._invocations if i.failure is not None and not i.reissued]
        last = self._quota_at_end
        return {
            "value": bool(failed) and all(self._stopped_by_rate_limit(invocation) for invocation in failed),
            "status": None if last is None else last.status,
            "resetsAt": None if last is None else last.resets_at,
            "five_hour_resetsAt": None if last is None or last.five_hour is None else last.five_hour.resets_at,
            "seven_day_resetsAt": None if last is None or last.seven_day is None else last.seven_day.resets_at,
        }
