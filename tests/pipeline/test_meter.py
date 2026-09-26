"""NFR-7: 実行ごとの所要時間・呼び出し回数・notional コストの記録(C-019)。"""

import copy
import hashlib
import inspect
import json
import os
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO

import pytest

import quiz_sift.pipeline.meter as meter_module
from quiz_sift.pipeline.meter import PRICES, Meter, ModelPrice
from quiz_sift.pipeline.overhead import OverheadConstant, skills_digest
from quiz_sift.pipeline.run_sequence import aggregate, read_log
from quiz_sift.ports.llm import TokenUsage
from quiz_sift.ports.metering import FailureKind, QuotaWindow, RateLimitInfo, RunMeter

MODEL = "claude-opus-5"
SOURCE = "https://example.test/pricing"
LEGACY_PRICES: Mapping[str, ModelPrice] = MappingProxyType(
    {MODEL: ModelPrice(5.00, 6.25, 0.50, 25.00, priced_at=date(2026, 9, 22), source=SOURCE)}
)
"""T-002 の基準を書いたときの単価表。計算の追跡可能性を見るテストは表を注入する。"""

START_FIELDS = {
    "record",
    "run_id",
    "execution_id",
    "started_at",
    "cache_mode",
    "stage",
    "env_names",
    "metered_env_names",
}
"""設計 NFR-7 節「ログのフィールド一覧」の開始行。実装の定数を読まない。"""

END_FIELDS = {
    "record",
    "run_id",
    "execution_id",
    "started_at",
    "finished_at",
    "elapsed_sec",
    "llm_calls",
    "provider_invocations",
    "model_turns",
    "turns_by_model",
    "max_concurrency",
    "tokens",
    "tokens_by_model",
    "calls_by_model",
    "estimated_cost",
    "disclaimer",
    "prices",
    "overhead",
    "failures_by_kind",
    "failed_invocations",
    "invocations_without_usage",
    "invocations_without_turns",
    "rate_limit_stopped_invocations",
    "ended_by_rate_limit",
    "quota_at_start",
    "quota_at_end",
    "overage_invocations",
    "invocations_without_rate_limit_event",
    "overage_check",
    "stop",
    "sequence",
}
"""設計 NFR-7 節「ログのフィールド一覧」の終了行。実装の定数を読まない。"""

QUOTA_FIELDS = {"status", "rateLimitType", "isUsingOverage", "overageStatus", "resetsAt", "five_hour", "seven_day"}

FAILURE_KINDS = {"spawn", "timeout", "parse", "schema", "truncated", "incomplete_ids", "provider_error"}
BILLING_WORDS = ("billed", "charged", "実課金")


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 100.0

    def now(self) -> datetime:
        return datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds


def read_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def end_lines(path: Path) -> list[dict[str, object]]:
    return [line for line in read_lines(path) if line["record"] == "end"]


def quota(
    status: str, resets_at: int, five: float, seven: float, *, is_using_overage: bool | None = False
) -> RateLimitInfo:
    return RateLimitInfo(
        status=status,
        resets_at=resets_at,
        five_hour=QuotaWindow(utilization=five, resets_at=resets_at),
        seven_day=QuotaWindow(utilization=seven, resets_at=resets_at + 1000),
        rate_limit_type="five_hour",
        is_using_overage=is_using_overage,
        overage_status="rejected",
    )


def make_meter(
    log: Path,
    clock: FakeClock | None = None,
    *,
    run_id: str = "run-1",
    execution_id: str = "exec-1",
    env_names: tuple[str, ...] = (),
    prices: Mapping[str, ModelPrice] = PRICES,
    overhead: Mapping[str, OverheadConstant] = MappingProxyType({}),
) -> Meter:
    return Meter(
        clock or FakeClock(),
        log_path=log,
        run_id=run_id,
        execution_id=execution_id,
        stage="cluster",
        cache_mode="normal",
        env_names=env_names,
        prices=prices,
        overhead=overhead,
    )


def finish(meter: Meter, exit_code: int = 0, exit_reason: str | None = None) -> dict[str, object]:
    return meter.finish(exit_code=exit_code, exit_reason=exit_reason)


def test_meter_records_run_fields(tmp_path: Path) -> None:
    log = tmp_path / "runs" / "meter.jsonl"
    clock = FakeClock()

    first = make_meter(log, clock, run_id="run-1", prices=LEGACY_PRICES)
    first.record(MODEL, TokenUsage(1000, 0, 0, 200))
    first.record(MODEL, TokenUsage(3000, 0, 0, 800))
    clock.seconds += 12.5
    finish(first)

    second = make_meter(log, clock, run_id="run-2", prices=LEGACY_PRICES)
    second.record(MODEL, TokenUsage(10, 0, 0, 10))
    clock.seconds += 1.0
    finish(second)

    lines = end_lines(log)
    assert len(lines) == 2, "実行1回につき終了行が1行追記される"
    entry = lines[0]
    assert {"run_id", "elapsed_sec", "calls_by_model", "estimated_cost"} <= entry.keys()
    assert entry["run_id"] == first.run_id
    assert lines[1]["run_id"] == second.run_id != first.run_id
    assert entry["elapsed_sec"] == pytest.approx(12.5)
    assert lines[1]["elapsed_sec"] == pytest.approx(1.0)
    assert entry["calls_by_model"] == {
        MODEL: {
            "calls": 2,
            "input_tokens": 4000,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 1000,
        }
    }
    # 4000 × $5 + 1000 × $25 per 1M
    cost = entry["estimated_cost"]
    assert isinstance(cost, dict)
    assert cost["currency"] == "USD"
    assert cost["computed"] == pytest.approx(0.045)
    assert cost["by_model"] == {MODEL: pytest.approx(0.045)}


def test_token_breakdown_and_price_provenance(tmp_path: Path) -> None:
    log = tmp_path / "meter.jsonl"
    meter = make_meter(log, prices=LEGACY_PRICES)
    meter.record(
        MODEL,
        TokenUsage(
            input_tokens=1_000, cache_creation_input_tokens=20_000, cache_read_input_tokens=300_000, output_tokens=4_000
        ),
    )
    finish(meter)
    (entry,) = end_lines(log)

    # 4区分が畳まれずにそのまま残る。
    tokens = entry["calls_by_model"][MODEL]  # type: ignore[index]
    assert tokens == {
        "calls": 1,
        "input_tokens": 1_000,
        "cache_creation_input_tokens": 20_000,
        "cache_read_input_tokens": 300_000,
        "output_tokens": 4_000,
    }

    # 単価表が同じ粒度で同じ行にあり、priced_at と取得元を持つ。
    price = entry["prices"][MODEL]  # type: ignore[index]
    assert price == {
        "input_per_mtok": 5.00,
        "cache_write_per_mtok": 6.25,
        "cache_read_per_mtok": 0.50,
        "output_per_mtok": 25.00,
        "priced_at": "2026-09-22",
        "source": SOURCE,
    }

    # ログの行だけから推定コストを再計算でき、記録値と一致する。
    recomputed = (
        tokens["input_tokens"] * price["input_per_mtok"]
        + tokens["cache_creation_input_tokens"] * price["cache_write_per_mtok"]
        + tokens["cache_read_input_tokens"] * price["cache_read_per_mtok"]
        + tokens["output_tokens"] * price["output_per_mtok"]
    ) / 1_000_000
    assert entry["estimated_cost"]["computed"] == pytest.approx(recomputed)  # type: ignore[index]
    # 入力を1値に畳んで入力単価を掛けた場合とは一致しない(畳むと推定がずれる)。
    collapsed = ((1_000 + 20_000 + 300_000) * 5.00 + 4_000 * 25.00) / 1_000_000
    assert recomputed != pytest.approx(collapsed)


def test_notional_cost_computed_declared_and_unavailable(tmp_path: Path) -> None:
    prices = MappingProxyType({"m": ModelPrice(2.0, 3.0, 0.5, 10.0, priced_at=date(2026, 9, 25), source=SOURCE)})
    meter = make_meter(tmp_path / "a.jsonl", prices=prices)
    meter.begin_invocation("a")
    meter.end_invocation("m", 1, TokenUsage(600_000, 200_000, 0, 100_000), 1.0, False, None)
    meter.begin_invocation("a")
    meter.end_invocation("m", 1, TokenUsage(400_000, 0, 2_000_000, 0), 4.0, False, None)
    cost = finish(meter)["estimated_cost"]
    # 1M × $2 + 0.2M × $3 + 2M × $0.5 + 0.1M × $10 = 2 + 0.6 + 1 + 1
    computed = 4.6
    assert cost == {
        "notional": True,
        "currency": "USD",
        "status": "recorded",
        "computed": pytest.approx(computed),
        "declared": pytest.approx(5.0),
        "relative_diff": pytest.approx((5.0 - computed) / computed),
        "by_model": {"m": pytest.approx(computed)},
    }

    zero = MappingProxyType({"m": ModelPrice(2.0, 3.0, 0.0, 10.0, priced_at=date(2026, 9, 25), source=SOURCE)})
    meter = make_meter(tmp_path / "b.jsonl", prices=zero)
    meter.begin_invocation("a")
    meter.end_invocation("m", 1, TokenUsage(1, 1, 1, 1), 0.5, False, None)
    cost = finish(meter)["estimated_cost"]
    assert isinstance(cost, dict)
    assert (cost["status"], cost["reason"], cost["computed"]) == ("unavailable", "zero_price", None)
    assert cost["declared"] == pytest.approx(0.5)

    meter = make_meter(tmp_path / "c.jsonl")
    meter.begin_invocation("a")
    meter.end_invocation("claude-haiku-4.5", 1, TokenUsage(1, 0, 0, 1), None, False, None)  # 一字違いの ID
    entry = finish(meter)
    cost = entry["estimated_cost"]
    assert isinstance(cost, dict)
    assert (cost["status"], cost["reason"], cost["computed"]) == ("unavailable", "unknown_model", None)
    assert entry["tokens_by_model"] == {"claude-haiku-4.5": {
        "input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1
    }}  # fmt: skip


def test_price_table_has_two_models_with_sources() -> None:
    assert set(PRICES) == {"claude-opus-5-5", "claude-haiku-4-5"}
    for price in PRICES.values():
        assert (
            min(price.input_per_mtok, price.cache_write_per_mtok, price.cache_read_per_mtok, price.output_per_mtok) > 0
        )
        assert isinstance(price.priced_at, date)
        assert price.source.startswith("https://")


def test_price_table_carries_its_date() -> None:
    with pytest.raises(TypeError):
        ModelPrice(5.0, 6.25, 0.5, 25.0)  # type: ignore[call-arg]


def test_failed_and_usage_less_invocations_are_counted(tmp_path: Path) -> None:
    meter = make_meter(tmp_path / "meter.jsonl")
    model = "claude-opus-5-5"
    # 失敗した呼び出しも usage を先に記録する。
    meter.begin_invocation("a")
    meter.end_invocation(model, 2, TokenUsage(100, 0, 0, 7), 0.01, False, None)
    meter.record_failure(FailureKind.SCHEMA)
    # result が来ずに終わった子プロセス。
    meter.begin_invocation("a")
    meter.end_invocation(model, None, None, None, None, None)
    meter.record_failure(FailureKind.TIMEOUT)
    # usage はあるがターンが取れなかった呼び出し。
    meter.begin_invocation("a")
    meter.end_invocation(model, None, TokenUsage(5, 0, 0, 1), None, None, None)
    entry = finish(meter, 1, "StageFailed")

    assert entry["invocations_without_usage"] == 1
    assert entry["invocations_without_turns"] == 2
    assert entry["tokens"] == {
        "input_tokens": 105,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 8,
    }
    failures = entry["failures_by_kind"]
    assert isinstance(failures, dict)
    assert failures.keys() == FAILURE_KINDS
    assert failures["schema"] == failures["timeout"] == 1
    assert sum(failures.values()) == 2
    assert entry["failed_invocations"] == 2
    assert entry["ended_by_rate_limit"] == {
        "value": False,
        "status": None,
        "resetsAt": None,
        "five_hour_resetsAt": None,
        "seven_day_resetsAt": None,
    }


def test_overhead_is_unknown_on_argv_mismatch_and_quota_is_recorded(tmp_path: Path) -> None:
    measured = date(2026, 9, 26)
    bucket, cli, skills = "cache_creation_input_tokens", "2.1.281", "s-ok"
    table = MappingProxyType(
        {
            "claude-opus-5-5": OverheadConstant("claude-opus-5-5", 1200.0, bucket, measured, cli, "d-ok", skills),
            "claude-haiku-4-5": OverheadConstant("claude-haiku-4-5", 900.0, bucket, measured, cli, "d-old", skills),
        }
    )
    meter = make_meter(tmp_path / "meter.jsonl", overhead=table)
    meter.begin_invocation("d-ok")
    meter.record_quota(quota("allowed_warning", 5000, 0.3, 0.95))
    meter.end_invocation("claude-opus-5-5", 2, TokenUsage(1, 0, 0, 1), None, False, skills)
    meter.begin_invocation("d-ok")
    meter.end_invocation("claude-opus-5-5", 3, TokenUsage(1, 0, 0, 1), None, False, skills)
    meter.begin_invocation("d-new")
    meter.end_invocation("claude-haiku-4-5", 2, TokenUsage(1, 0, 0, 1), None, False, skills)
    meter.begin_invocation("d-new")
    meter.record_quota(quota("allowed_warning", 5000, 0.4, 0.96))
    meter.end_invocation("unmeasured-model", 1, TokenUsage(1, 0, 0, 1), None, False, skills)
    entry = finish(meter)

    assert entry["overhead"] == {
        "claude-opus-5-5": {
            "status": "recorded",
            "per_turn_tokens": 1200.0,
            "bucket": "cache_creation_input_tokens",
            "measured_at": "2026-09-26",
            "cli_version": "2.1.281",
            "argv_digest": "d-ok",
            "skills_digest": "s-ok",
            "turns": 5,
        },
        "claude-haiku-4-5": {
            "status": "unknown",
            "reason": "argv_mismatch",
            "argv_digests": ["d-new"],
            "skills_digests": ["s-ok"],
            "turns": 2,
        },
        "unmeasured-model": {
            "status": "unknown",
            "reason": "no_constant",
            "argv_digests": ["d-new"],
            "skills_digests": ["s-ok"],
            "turns": 1,
        },
    }
    start, end = entry["quota_at_start"], entry["quota_at_end"]
    assert isinstance(start, dict) and isinstance(end, dict)
    assert (start["five_hour"], start["seven_day"]) == (
        {"utilization": 0.3, "resetsAt": 5000},
        {"utilization": 0.95, "resetsAt": 6000},
    )
    assert (end["five_hour"], end["seven_day"]) == (
        {"utilization": 0.4, "resetsAt": 5000},
        {"utilization": 0.96, "resetsAt": 6000},
    )


def billing_words_in(value: object) -> list[str]:
    """キーと値を再帰的に走査し、実課金額を主張する語を返す。"""
    if isinstance(value, dict):
        return [word for key, item in value.items() for word in billing_words_in(key) + billing_words_in(item)]
    if isinstance(value, list):
        return [word for item in value for word in billing_words_in(item)]
    text = str(value).lower()
    return [word for word in BILLING_WORDS if word in text]


def test_output_is_notional_and_never_claims_billing(tmp_path: Path) -> None:
    meter = make_meter(tmp_path / "meter.jsonl")
    meter.begin_call("classify_clusters")
    meter.begin_invocation("a")
    meter.record_quota(quota("allowed_warning", 1, 0.1, 0.2))
    meter.end_invocation("claude-opus-5-5", 2, TokenUsage(10, 20, 30, 40), 0.001, False, None)
    finish(meter)
    (line,) = end_lines(tmp_path / "meter.jsonl")

    cost = line["estimated_cost"]
    assert isinstance(cost, dict)
    assert cost["notional"] is True
    disclaimers = [value for value in line.values() if isinstance(value, str) and "notional" in value]
    assert len(disclaimers) == 1, "同じ行に免責の1行がある"
    assert billing_words_in(line) == []

    # 判定が空振りしていないこと: 語を1つ差し込むと赤になる。
    for word, where in (("billed", "value"), ("charged", "key"), ("実課金", "value")):
        tainted = copy.deepcopy(line)
        if where == "key":
            tainted[f"{word}_usd"] = 1.0
        else:
            tainted["estimated_cost"]["status"] = word  # type: ignore[index]
        assert billing_words_in(tainted) == [word]


def test_a_run_is_finished_once(tmp_path: Path) -> None:
    meter = make_meter(tmp_path / "meter.jsonl")
    finish(meter)
    with pytest.raises(RuntimeError):
        finish(meter)


def test_allowed_status_failure_is_not_a_rate_limit_stop(tmp_path: Path) -> None:
    opus = "claude-opus-5-5"
    usage = TokenUsage(1, 0, 0, 1)

    # 不具合の再現: 最後のイベントが allowed のまま PROVIDER_ERROR で落ちた子はレート枠による停止ではない。
    meter = make_meter(tmp_path / "allowed.jsonl")
    meter.begin_invocation("a")
    meter.record_quota(quota("allowed", 1_790_000_000, 0.9, 0.99))
    meter.end_invocation(opus, 1, usage, None, True, None)
    meter.record_failure(FailureKind.PROVIDER_ERROR)
    entry = finish(meter, 1, "StageFailed")
    ended = entry["ended_by_rate_limit"]
    assert isinstance(ended, dict)
    assert ended["value"] is False
    assert ended["status"] == "allowed", "status は真偽に関わらず生のまま残す"
    assert entry["rate_limit_stopped_invocations"] == 0
    assert entry["stop"] == {"exit_code": 1, "exit_reason": "StageFailed", "kind": "other"}

    # 失敗した子のすべてが述語を満たす。成功した子の後のイベントは判定に使わない。
    meter = make_meter(tmp_path / "rejected.jsonl")
    meter.begin_invocation("a")
    meter.record_quota(quota("allowed_warning", 1_780_000_000, 0.5, 0.6))
    meter.record_quota(quota("rejected", 1_790_000_000, 1.0, 0.99))
    meter.end_invocation(opus, 1, usage, None, True, None)
    meter.record_failure(FailureKind.PROVIDER_ERROR)
    meter.begin_invocation("a")
    meter.record_quota(quota("rejected", 1_790_000_100, 1.0, 0.99))
    meter.end_invocation(opus, 1, usage, None, True, None)
    meter.record_failure(FailureKind.PROVIDER_ERROR)
    entry = finish(meter, 1, "StageFailed")
    assert entry["ended_by_rate_limit"] == {
        "value": True,
        "status": "rejected",
        "resetsAt": 1_790_000_100,
        "five_hour_resetsAt": 1_790_000_100,
        "seven_day_resetsAt": 1_790_001_100,
    }
    assert entry["rate_limit_stopped_invocations"] == 2
    assert entry["stop"] == {"exit_code": 1, "exit_reason": "StageFailed", "kind": "rate_limit"}

    # A が rejected で、B が allowed で落ちた実行は偽。述語は子ごとに、その子自身の最後のイベントで評価する。
    meter = make_meter(tmp_path / "mixed.jsonl")
    meter.begin_invocation("a")
    meter.record_quota(quota("rejected", 1_790_000_000, 1.0, 0.99))
    meter.end_invocation(opus, 1, usage, None, True, None)
    meter.record_failure(FailureKind.PROVIDER_ERROR)
    meter.begin_invocation("a")
    meter.record_quota(quota("allowed", 1_790_000_000, 0.2, 0.3))
    meter.end_invocation(opus, 1, usage, None, True, None)
    meter.record_failure(FailureKind.PROVIDER_ERROR)
    entry = finish(meter, 1, "StageFailed")
    assert entry["ended_by_rate_limit"]["value"] is False  # type: ignore[index]
    assert entry["rate_limit_stopped_invocations"] == 1

    # 逆順も同じ: 実行の最後のイベント(B の allowed)を A の判定に借りない。
    meter = make_meter(tmp_path / "borrow.jsonl")
    meter.begin_invocation("a")
    meter.record_quota(quota("rejected", 1_790_000_000, 1.0, 0.99))
    meter.end_invocation(opus, 1, usage, None, True, None)
    meter.record_failure(FailureKind.PROVIDER_ERROR)
    meter.begin_invocation("a")
    meter.record_quota(quota("allowed", 1_790_000_000, 0.2, 0.3))
    meter.end_invocation(opus, 1, usage, None, False, None)
    entry = finish(meter, 1, "StageFailed")
    assert entry["ended_by_rate_limit"]["value"] is True  # type: ignore[index]
    assert entry["rate_limit_stopped_invocations"] == 1


def test_overage_counts_and_check(tmp_path: Path) -> None:
    expected = ["self", "model", "turns", "usage", "cost_usd", "is_error", "skills_digest"]
    assert list(inspect.signature(Meter.end_invocation).parameters) == expected
    assert list(inspect.signature(RunMeter.end_invocation).parameters) == expected
    opus = "claude-opus-5-5"
    usage = TokenUsage(1, 0, 0, 1)

    meter = make_meter(tmp_path / "early.jsonl")
    with pytest.raises(RuntimeError):
        meter.record_quota(quota("allowed", 1, 0.1, 0.2))

    # passed: すべての子にイベントが届き、isUsingOverage はどれも偽。
    meter = make_meter(tmp_path / "passed.jsonl")
    for _ in range(2):
        meter.begin_invocation("a")
        meter.record_quota(quota("allowed", 1, 0.1, 0.2))
        meter.end_invocation(opus, 2, usage, None, False, None)
    entry = finish(meter)
    assert (entry["overage_invocations"], entry["invocations_without_rate_limit_event"]) == (0, 0)
    assert entry["overage_check"] == "passed"

    # unknown: 起動できなかった子(begin_invocation と SPAWN だけ)もイベントの届かなかった子に数える(D-140)。
    meter = make_meter(tmp_path / "unknown.jsonl")
    meter.begin_invocation("a")
    meter.record_quota(quota("allowed", 1, 0.1, 0.2, is_using_overage=None))
    meter.end_invocation(opus, 2, usage, None, False, None)
    meter.begin_invocation("a")
    meter.record_failure(FailureKind.SPAWN)
    entry = finish(meter, 1, "StageFailed")
    assert (entry["overage_invocations"], entry["invocations_without_rate_limit_event"]) == (0, 1)
    assert entry["overage_check"] == "unknown"

    # failed: isUsingOverage が1回でも真だった子の数。同じ子で2回真でも1と数える。unknown より優先する。
    meter = make_meter(tmp_path / "failed.jsonl")
    meter.begin_invocation("a")
    meter.record_quota(quota("allowed", 1, 0.1, 0.2, is_using_overage=True))
    meter.record_quota(quota("allowed", 1, 0.1, 0.2, is_using_overage=False))
    meter.end_invocation(opus, 2, usage, None, False, None)
    meter.begin_invocation("a")
    meter.record_quota(quota("allowed", 1, 0.1, 0.2, is_using_overage=True))
    meter.record_quota(quota("rejected", 1, 0.1, 0.2, is_using_overage=True))
    meter.end_invocation(opus, 2, usage, None, True, None)
    meter.record_failure(FailureKind.PROVIDER_ERROR)
    meter.begin_invocation("a")
    meter.record_failure(FailureKind.SPAWN)
    entry = finish(meter, 1, "StageFailed")
    assert (entry["overage_invocations"], entry["invocations_without_rate_limit_event"]) == (2, 1)
    assert entry["overage_check"] == "failed"


class WriteCounter:
    """`open` が返したファイルへの write の回数を数える。"""

    def __init__(self, file: BinaryIO, writes: list[bytes]) -> None:
        self._file = file
        self._writes = writes

    def __enter__(self) -> "WriteCounter":
        self._file.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._file.__exit__(*exc)  # type: ignore[arg-type]

    def write(self, data: bytes) -> int:
        self._writes.append(data)
        return self._file.write(data)

    def __getattr__(self, name: str) -> object:
        return getattr(self._file, name)


def test_start_and_end_lines_carry_the_design_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "out" / "meter.jsonl"
    log.parent.mkdir()
    torn = '{"record": "start", "run_id": "x'
    log.write_text(torn, encoding="utf-8")  # 電源断で途中まで書かれた行

    writes: list[bytes] = []
    synced: list[bytes] = []

    def counting_open(path: Path, mode: str) -> WriteCounter:
        return WriteCounter(open(path, mode), writes)  # type: ignore[arg-type]  # noqa: SIM115

    def fsync(fd: int) -> None:
        synced.append(log.read_bytes())  # fsync の時点で行がファイルに出ている(flush 済み)

    monkeypatch.setattr(meter_module, "open", counting_open, raising=False)
    monkeypatch.setattr(os, "fsync", fsync)

    clock = FakeClock()
    meter = make_meter(log, clock, env_names=("PATH", "HOME", "ANTHROPIC_API_KEY", "AWS_PROFILE"))
    assert len(writes) == len(synced) == 1
    assert synced[0].endswith(writes[0]) and writes[0].startswith(b"\n") and writes[0].endswith(b"\n")

    meter.begin_call("classify_clusters")
    meter.begin_invocation("argv-1")
    meter.record_quota(quota("allowed_warning", 1000, 0.1, 0.5))
    meter.end_invocation("claude-opus-5-5", 2, TokenUsage(100, 10, 1000, 50), 0.01, False, None)
    meter.begin_invocation("argv-1")
    meter.record_quota(quota("allowed", 1002, 0.2, 0.7, is_using_overage=None))
    meter.end_invocation("claude-opus-5-5", 3, TokenUsage(100, 0, 1000, 50), 0.01, False, None)
    clock.seconds += 7.0
    entry = finish(meter)
    assert len(writes) == len(synced) == 2, "各行は1回の write と fsync"
    assert synced[1].endswith(writes[1]) and not writes[1].startswith(b"\n")

    text = log.read_text(encoding="utf-8")
    first, *rest = text.splitlines()
    assert first == torn, "途中まで書かれた行と連結させない"
    start, end = (json.loads(line) for line in rest)
    assert start.keys() == START_FIELDS
    assert start == {
        "record": "start",
        "run_id": "run-1",
        "execution_id": "exec-1",
        "started_at": "2026-09-23T12:00:00+00:00",
        "cache_mode": "normal",
        "stage": "cluster",
        "env_names": ["ANTHROPIC_API_KEY", "AWS_PROFILE", "HOME", "PATH"],
        "metered_env_names": ["ANTHROPIC_API_KEY", "AWS_PROFILE"],
    }
    assert end == json.loads(json.dumps(entry))
    assert end.keys() == END_FIELDS
    assert "material" not in json.dumps(end), "実行ログに material は置かない(D-116)"
    assert (end["record"], end["run_id"], end["execution_id"]) == ("end", "run-1", "exec-1")
    assert (end["llm_calls"], end["provider_invocations"], end["model_turns"]) == (1, 2, 5)
    assert end["elapsed_sec"] == pytest.approx(7.0)
    assert end["quota_at_start"] == {
        "status": "allowed_warning",
        "rateLimitType": "five_hour",
        "isUsingOverage": False,
        "overageStatus": "rejected",
        "resetsAt": 1000,
        "five_hour": {"utilization": 0.1, "resetsAt": 1000},
        "seven_day": {"utilization": 0.5, "resetsAt": 2000},
    }
    assert end["quota_at_end"].keys() == QUOTA_FIELDS
    assert end["quota_at_end"]["isUsingOverage"] is None
    assert end["stop"] == {"exit_code": 0, "exit_reason": None, "kind": "completed"}

    # rate_limit_event が届かなかった実行の quota は null。
    meter = make_meter(log, execution_id="exec-2")
    meter.begin_invocation("argv-1")
    meter.record_failure(FailureKind.SPAWN)
    entry = finish(meter, 1, "StageFailed")
    assert (entry["quota_at_start"], entry["quota_at_end"]) == (None, None)
    assert entry["stop"] == {"exit_code": 1, "exit_reason": "StageFailed", "kind": "other"}
    tail = log.read_text(encoding="utf-8").splitlines()[3:]
    assert [json.loads(line)["record"] for line in tail] == ["start", "end"]


def test_overhead_is_unknown_on_skills_mismatch(tmp_path: Path) -> None:
    def expected_digest(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    synced = skills_digest(["pdf", "docx"])
    assert synced == skills_digest(["docx", "pdf"]) == expected_digest('["docx", "pdf"]')
    assert skills_digest(None) == expected_digest("absent")
    assert skills_digest([]) == expected_digest("[]") != skills_digest(None)

    measured, bucket, cli = date(2026, 9, 26), "cache_creation_input_tokens", "2.1.282"
    opus, haiku, sonnet = "claude-opus-5-5", "claude-haiku-4-5", "claude-sonnet-5"
    table = MappingProxyType(
        {
            opus: OverheadConstant(opus, 1200.0, bucket, measured, cli, "d", synced),
            haiku: OverheadConstant(haiku, 900.0, bucket, measured, cli, "d-old", synced),
            sonnet: OverheadConstant(sonnet, 1000.0, bucket, measured, cli, "d", synced),
        }
    )
    absent = skills_digest(None)
    meter = make_meter(tmp_path / "meter.jsonl", overhead=table)
    usage = TokenUsage(1, 0, 0, 1)
    for model, skills in ((opus, synced), (opus, absent), (haiku, absent), (sonnet, synced), (sonnet, synced)):
        meter.begin_invocation("d")
        meter.end_invocation(model, 2, usage, None, False, skills)
    overhead = finish(meter)["overhead"]
    assert isinstance(overhead, dict)

    assert overhead[opus] == {
        "status": "unknown",
        "reason": "skills_mismatch",
        "argv_digests": ["d"],
        "skills_digests": sorted([synced, absent]),
        "turns": 4,
    }
    assert (overhead[haiku]["status"], overhead[haiku]["reason"]) == ("unknown", "argv_mismatch"), "argv を先に比べる"
    assert overhead[sonnet]["status"] == "recorded"
    assert overhead[sonnet]["skills_digest"] == synced


def test_end_line_carries_the_sequence_aggregate(tmp_path: Path) -> None:
    log = tmp_path / "meter.jsonl"
    clock = FakeClock()

    def sequence_meter(execution_id: str, stage: str, cache_mode: str) -> Meter:
        return Meter(
            clock,
            log_path=log,
            run_id="seq",
            execution_id=execution_id,
            stage=stage,
            cache_mode=cache_mode,
            env_names=(),
            overhead=MappingProxyType({}),
        )

    make_meter(log, run_id="other")  # 別の列の閉じていない開始行は、この列の集約に入らない

    first = sequence_meter("exec-a", "cluster", "fresh")
    first.begin_call("classify_clusters")
    first.begin_invocation("argv-1")
    first.record_quota(quota("rejected", 1000, 1.0, 0.5))
    first.end_invocation("claude-opus-5-5", 1, TokenUsage(10, 0, 0, 5), 0.001, True, None)
    first.record_failure(FailureKind.PROVIDER_ERROR)
    clock.seconds += 3.0
    stopped = finish(first, 1, "StageFailed")
    assert stopped["sequence"] == json.loads(json.dumps(aggregate(read_log(log), "seq")))
    assert isinstance(stopped["sequence"], dict)
    assert (stopped["sequence"]["status"], stopped["sequence"]["not_completable_reasons"]) == ("resumable", [])

    second = sequence_meter("exec-b", "audio", "resume")
    second.begin_call("script")
    second.begin_invocation("argv-1")
    second.record_quota(quota("allowed", 2000, 0.1, 0.5))
    second.end_invocation("claude-opus-5-5", 2, TokenUsage(20, 0, 0, 5), 0.002, False, None)
    clock.seconds += 4.0
    done = finish(second)

    written = end_lines(log)[-1]
    assert written == json.loads(json.dumps(done))
    sequence = written["sequence"]
    assert isinstance(sequence, dict)
    assert sequence == json.loads(json.dumps(aggregate(read_log(log), "seq"))), "ファイル上の行と終了行を合わせた集約"
    assert sequence["status"] == "completed"
    assert sequence["not_completable_reasons"] == [], "いま締めている実行の開始行を閉じていないものに数えない"
    assert (sequence["llm_calls"], sequence["provider_invocations"], sequence["model_turns"]) == (2, 2, 3)
    assert sequence["elapsed_sec_sum"] == pytest.approx(7.0)
    assert [entry["execution_id"] for entry in sequence["quota"]] == ["exec-a", "exec-b"]
