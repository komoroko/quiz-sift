"""NFR-7: 実行ごとの所要時間・呼び出し回数・推定コストの記録(C-019)。"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from quiz_sift.pipeline.meter import PRICES, Meter, ModelPrice
from quiz_sift.ports.llm import TokenUsage

MODEL = "claude-opus-5"


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 100.0

    def now(self) -> datetime:
        return datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds


def read_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_meter_records_run_fields(tmp_path: Path) -> None:
    log = tmp_path / "runs" / "meter.jsonl"
    clock = FakeClock()

    first = Meter(clock)
    first.record(MODEL, TokenUsage(1000, 0, 0, 200))
    first.record(MODEL, TokenUsage(3000, 0, 0, 800))
    clock.seconds += 12.5
    first.finish(log)

    second = Meter(clock)
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
    assert entry["estimated_cost"] == {
        "currency": "USD",
        "total": pytest.approx(0.045),
        "by_model": {MODEL: pytest.approx(0.045)},
    }


def test_token_breakdown_and_price_provenance(tmp_path: Path) -> None:
    log = tmp_path / "meter.jsonl"
    meter = Meter(FakeClock())
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

    # 単価表が同じ粒度で同じ行にあり、priced_at を持つ。
    price = entry["prices"][MODEL]  # type: ignore[index]
    assert price == {
        "input_per_mtok": 5.00,
        "cache_write_per_mtok": 6.25,
        "cache_read_per_mtok": 0.50,
        "output_per_mtok": 25.00,
        "priced_at": "2026-09-22",
    }

    # ログの行だけから推定コストを再計算でき、記録値と一致する。
    recomputed = (
        tokens["input_tokens"] * price["input_per_mtok"]
        + tokens["cache_creation_input_tokens"] * price["cache_write_per_mtok"]
        + tokens["cache_read_input_tokens"] * price["cache_read_per_mtok"]
        + tokens["output_tokens"] * price["output_per_mtok"]
    ) / 1_000_000
    assert entry["estimated_cost"]["total"] == pytest.approx(recomputed)  # type: ignore[index]
    # 入力を1値に畳んで入力単価を掛けた場合とは一致しない(畳むと推定がずれる)。
    collapsed = ((1_000 + 20_000 + 300_000) * 5.00 + 4_000 * 25.00) / 1_000_000
    assert recomputed != pytest.approx(collapsed)


def test_price_table_carries_its_date() -> None:
    assert all(isinstance(price.priced_at, date) for price in PRICES.values())
    with pytest.raises(TypeError):
        ModelPrice(5.0, 6.25, 0.5, 25.0)  # type: ignore[call-arg]


def test_unpriced_model_is_refused_rather_than_counted_free() -> None:
    meter = Meter(FakeClock())
    with pytest.raises(ValueError, match="単価表に無いモデル"):
        meter.record("claude-unknown", TokenUsage(1, 0, 0, 1))


def test_a_run_is_finished_once(tmp_path: Path) -> None:
    meter = Meter(FakeClock())
    meter.finish(tmp_path / "meter.jsonl")
    with pytest.raises(RuntimeError):
        meter.finish(tmp_path / "meter.jsonl")
