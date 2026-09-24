"""NFR-7: 実行ごとの所要時間・呼び出し回数・notional コストの記録(C-019)。"""

import copy
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from quiz_sift.pipeline.meter import PRICES, Meter, ModelPrice
from quiz_sift.pipeline.overhead import OverheadConstant
from quiz_sift.ports.llm import TokenUsage
from quiz_sift.ports.metering import FailureKind, QuotaWindow, RateLimitInfo, RunMeter

MODEL = "claude-opus-5"
SOURCE = "https://example.test/pricing"
LEGACY_PRICES: Mapping[str, ModelPrice] = MappingProxyType(
    {MODEL: ModelPrice(5.00, 6.25, 0.50, 25.00, priced_at=date(2026, 9, 22), source=SOURCE)}
)
"""T-002 の基準を書いたときの単価表。計算の追跡可能性を見るテストは表を注入する。"""

NFR7_FIELDS = {
    "run_id",
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
    "estimated_cost",
    "prices",
    "overhead",
    "failures_by_kind",
    "invocations_without_usage",
    "invocations_without_turns",
    "ended_by_rate_limit",
    "quota_at_start",
    "quota_at_end",
}
"""設計 NFR-7 節「ログのフィールド一覧」から写した全キー。実装の定数を読まない。"""

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


def quota(status: str, resets_at: int, five: float, seven: float) -> RateLimitInfo:
    return RateLimitInfo(
        status=status,
        resets_at=resets_at,
        five_hour=QuotaWindow(utilization=five, resets_at=resets_at),
        seven_day=QuotaWindow(utilization=seven, resets_at=resets_at + 1000),
    )


def test_meter_records_run_fields(tmp_path: Path) -> None:
    log = tmp_path / "runs" / "meter.jsonl"
    clock = FakeClock()

    first = Meter(clock, prices=LEGACY_PRICES)
    first.record(MODEL, TokenUsage(1000, 0, 0, 200))
    first.record(MODEL, TokenUsage(3000, 0, 0, 800))
    clock.seconds += 12.5
    first.finish(log)

    second = Meter(clock, prices=LEGACY_PRICES)
    second.record(MODEL, TokenUsage(10, 0, 0, 10))
    clock.seconds += 1.0
    second.finish(log)

    lines = read_lines(log)
    assert len(lines) == 2, "実行1回につき1行が追記される"
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
    meter = Meter(FakeClock(), prices=LEGACY_PRICES)
    meter.record(
        MODEL,
        TokenUsage(
            input_tokens=1_000, cache_creation_input_tokens=20_000, cache_read_input_tokens=300_000, output_tokens=4_000
        ),
    )
    meter.finish(log)
    (entry,) = read_lines(log)

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


def test_meter_line_carries_every_nfr7_field(tmp_path: Path) -> None:
    clock = FakeClock()
    meter: RunMeter = Meter(clock)
    opus, haiku = "claude-opus-5-5", "claude-haiku-4-5"
    first, middle, last = (quota("allowed_warning", 1000 + i, 0.1 * i, 0.5 + 0.1 * i) for i in range(3))

    meter.begin_call("classify_clusters")
    meter.begin_invocation("argv-1")
    meter.record_quota(first)
    meter.end_invocation(opus, 2, TokenUsage(100, 10, 1000, 50), 0.01)
    meter.begin_invocation("argv-1")
    meter.record_quota(middle)
    meter.end_invocation(opus, 3, TokenUsage(100, 0, 1000, 50), 0.01)
    meter.begin_call("summarize_cluster")
    meter.begin_invocation("argv-2")
    meter.end_invocation(haiku, 2, TokenUsage(10, 0, 0, 5), None)
    meter.begin_call("describe_term")
    meter.begin_invocation("argv-2")
    meter.record_quota(last)
    meter.end_invocation(haiku, None, None, None)
    clock.seconds += 7.0
    assert isinstance(meter, Meter)
    entry = meter.finish(tmp_path / "meter.jsonl")

    (line,) = read_lines(tmp_path / "meter.jsonl")
    assert line == json.loads(json.dumps(entry))
    missing = NFR7_FIELDS - line.keys()
    assert not missing, f"フィールド一覧のキーが欠けている: {missing}"
    assert "material" not in json.dumps(line), "実行ログに material は置かない(D-116)"

    assert line["llm_calls"] == 3
    assert line["provider_invocations"] == 4
    assert line["model_turns"] == 7
    assert line["turns_by_model"] == {opus: 5, haiku: 2}
    assert line["max_concurrency"] == 1
    assert line["elapsed_sec"] == pytest.approx(7.0)
    assert line["tokens"] == {
        "input_tokens": 210,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 2000,
        "output_tokens": 105,
    }
    assert line["tokens_by_model"] == {
        opus: {
            "input_tokens": 200,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 2000,
            "output_tokens": 100,
        },
        haiku: {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 5},
    }
    assert line["quota_at_start"] == {
        "status": "allowed_warning",
        "resetsAt": 1000,
        "five_hour": {"utilization": 0.0, "resetsAt": 1000},
        "seven_day": {"utilization": 0.5, "resetsAt": 2000},
    }
    assert line["quota_at_end"] == {
        "status": "allowed_warning",
        "resetsAt": 1002,
        "five_hour": {"utilization": pytest.approx(0.2), "resetsAt": 1002},
        "seven_day": {"utilization": pytest.approx(0.7), "resetsAt": 2002},
    }


def test_notional_cost_computed_declared_and_unavailable(tmp_path: Path) -> None:
    prices = MappingProxyType({"m": ModelPrice(2.0, 3.0, 0.5, 10.0, priced_at=date(2026, 9, 25), source=SOURCE)})
    meter = Meter(FakeClock(), prices=prices)
    meter.begin_invocation("a")
    meter.end_invocation("m", 1, TokenUsage(600_000, 200_000, 0, 100_000), 1.0)
    meter.begin_invocation("a")
    meter.end_invocation("m", 1, TokenUsage(400_000, 0, 2_000_000, 0), 4.0)
    cost = meter.finish(tmp_path / "a.jsonl")["estimated_cost"]
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
    meter = Meter(FakeClock(), prices=zero)
    meter.end_invocation("m", 1, TokenUsage(1, 1, 1, 1), 0.5)
    cost = meter.finish(tmp_path / "b.jsonl")["estimated_cost"]
    assert isinstance(cost, dict)
    assert (cost["status"], cost["reason"], cost["computed"]) == ("unavailable", "zero_price", None)
    assert cost["declared"] == pytest.approx(0.5)

    meter = Meter(FakeClock())
    meter.begin_invocation("a")
    meter.end_invocation("claude-haiku-4.5", 1, TokenUsage(1, 0, 0, 1), None)  # 一字違いの ID
    entry = meter.finish(tmp_path / "c.jsonl")
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
    meter = Meter(FakeClock())
    model = "claude-opus-5-5"
    # 失敗した呼び出しも usage を先に記録する。
    meter.begin_invocation("a")
    meter.end_invocation(model, 2, TokenUsage(100, 0, 0, 7), 0.01)
    meter.record_failure(FailureKind.SCHEMA)
    # result が来ずに終わった子プロセス。
    meter.begin_invocation("a")
    meter.end_invocation(model, None, None, None)
    meter.record_failure(FailureKind.TIMEOUT)
    # usage はあるがターンが取れなかった呼び出し。
    meter.begin_invocation("a")
    meter.end_invocation(model, None, TokenUsage(5, 0, 0, 1), None)
    entry = meter.finish(tmp_path / "meter.jsonl")

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
    assert entry["ended_by_rate_limit"] == {"value": False, "status": None, "resetsAt": None}


@pytest.mark.parametrize(
    ("status", "kind", "same_invocation", "expected"),
    [
        ("rejected", FailureKind.PROVIDER_ERROR, True, True),
        ("allowed_warning", FailureKind.PROVIDER_ERROR, True, False),
        ("rejected", FailureKind.PARSE, True, False),
        ("rejected", FailureKind.PROVIDER_ERROR, False, False),
    ],
)
def test_ended_by_rate_limit_follows_d110(
    tmp_path: Path, status: str, kind: FailureKind, same_invocation: bool, expected: bool
) -> None:
    meter = Meter(FakeClock())
    meter.begin_invocation("a")
    meter.record_quota(quota(status, 1_790_000_000, 0.9, 0.99))
    if not same_invocation:
        meter.end_invocation("claude-opus-5-5", 1, TokenUsage(1, 0, 0, 1), None)
        meter.begin_invocation("a")
    meter.end_invocation("claude-opus-5-5", 1, TokenUsage(1, 0, 0, 1), None)
    meter.record_failure(kind)
    ended = meter.finish(tmp_path / "meter.jsonl")["ended_by_rate_limit"]
    assert isinstance(ended, dict)
    assert ended["value"] is expected
    if expected:
        assert (ended["status"], ended["resetsAt"]) == (status, 1_790_000_000)


def test_overhead_is_unknown_on_argv_mismatch_and_quota_is_recorded(tmp_path: Path) -> None:
    measured = date(2026, 9, 26)
    bucket, cli = "cache_creation_input_tokens", "2.1.281"
    table = MappingProxyType(
        {
            "claude-opus-5-5": OverheadConstant("claude-opus-5-5", 1200.0, bucket, measured, cli, "d-ok"),
            "claude-haiku-4-5": OverheadConstant("claude-haiku-4-5", 900.0, bucket, measured, cli, "d-old"),
        }
    )
    meter = Meter(FakeClock(), overhead=table)
    meter.begin_invocation("d-ok")
    meter.record_quota(quota("allowed_warning", 5000, 0.3, 0.95))
    meter.end_invocation("claude-opus-5-5", 2, TokenUsage(1, 0, 0, 1), None)
    meter.begin_invocation("d-ok")
    meter.end_invocation("claude-opus-5-5", 3, TokenUsage(1, 0, 0, 1), None)
    meter.begin_invocation("d-new")
    meter.end_invocation("claude-haiku-4-5", 2, TokenUsage(1, 0, 0, 1), None)
    meter.begin_invocation("d-new")
    meter.record_quota(quota("allowed_warning", 5000, 0.4, 0.96))
    meter.end_invocation("unmeasured-model", 1, TokenUsage(1, 0, 0, 1), None)
    entry = meter.finish(tmp_path / "meter.jsonl")

    assert entry["overhead"] == {
        "claude-opus-5-5": {
            "status": "recorded",
            "per_turn_tokens": 1200.0,
            "bucket": "cache_creation_input_tokens",
            "measured_at": "2026-09-26",
            "cli_version": "2.1.281",
            "argv_digest": "d-ok",
            "turns": 5,
        },
        "claude-haiku-4-5": {"status": "unknown", "reason": "argv_mismatch", "argv_digests": ["d-new"], "turns": 2},
        "unmeasured-model": {"status": "unknown", "reason": "no_constant", "argv_digests": ["d-new"], "turns": 1},
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
    meter = Meter(FakeClock())
    meter.begin_call("classify_clusters")
    meter.begin_invocation("a")
    meter.record_quota(quota("allowed_warning", 1, 0.1, 0.2))
    meter.end_invocation("claude-opus-5-5", 2, TokenUsage(10, 20, 30, 40), 0.001)
    meter.finish(tmp_path / "meter.jsonl")
    (line,) = read_lines(tmp_path / "meter.jsonl")

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
    meter = Meter(FakeClock())
    meter.finish(tmp_path / "meter.jsonl")
    with pytest.raises(RuntimeError):
        meter.finish(tmp_path / "meter.jsonl")
