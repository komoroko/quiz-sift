"""NFR-7: 実行の列の集約と状態(C-019、ADR-018、D-145)。"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from quiz_sift.pipeline.run_sequence import UnknownRunError, aggregate, read_log

RUN = "run-1"
OPUS, HAIKU = "claude-opus-5-5", "claude-haiku-4-5"
FAILURE_KINDS = ("spawn", "timeout", "parse", "schema", "truncated", "incomplete_ids", "provider_error")


def start(
    execution_id: str,
    *,
    cache_mode: str = "resume",
    stage: str = "cluster",
    started_at: str = "2026-09-26T10:00:00+00:00",
    metered: Sequence[str] = (),
    run_id: str = RUN,
) -> dict[str, Any]:
    return {
        "record": "start",
        "run_id": run_id,
        "execution_id": execution_id,
        "started_at": started_at,
        "cache_mode": cache_mode,
        "stage": stage,
        "env_names": sorted({"PATH", *metered}),
        "metered_env_names": list(metered),
    }


def quota(utilization: float, resets_at: int) -> dict[str, Any]:
    window = {"utilization": utilization, "resetsAt": resets_at}
    return {
        "status": "allowed",
        "rateLimitType": "five_hour",
        "isUsingOverage": False,
        "overageStatus": "rejected",
        "resetsAt": resets_at,
        "five_hour": window,
        "seven_day": window,
    }


def tokens(input_tokens: int, cache_write: int, cache_read: int, output: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output,
    }


def end(
    execution_id: str,
    *,
    exit_code: int = 0,
    rate_limit: bool = False,
    overage: int = 0,
    finished_at: str = "2026-09-26T10:01:00+00:00",
    run_id: str = RUN,
    **values: Any,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "record": "end",
        "run_id": run_id,
        "execution_id": execution_id,
        "started_at": "2026-09-26T10:00:00+00:00",
        "finished_at": finished_at,
        "elapsed_sec": 1.0,
        "llm_calls": 1,
        "provider_invocations": 1,
        "model_turns": 1,
        "turns_by_model": {OPUS: 1},
        "max_concurrency": 1,
        "tokens": tokens(1, 0, 0, 1),
        "tokens_by_model": {OPUS: tokens(1, 0, 0, 1)},
        "calls_by_model": {},
        "estimated_cost": {
            "notional": True,
            "currency": "USD",
            "declared": None,
            "status": "recorded",
            "computed": 0.1,
            "relative_diff": None,
        },
        "failures_by_kind": dict.fromkeys(FAILURE_KINDS, 0),
        "invocations_without_usage": 0,
        "invocations_without_turns": 0,
        "ended_by_rate_limit": {"value": rate_limit},
        "quota_at_start": None,
        "quota_at_end": None,
        "overage_invocations": overage,
        "invocations_without_rate_limit_event": 0,
        "stop": {"exit_code": exit_code, "exit_reason": None if exit_code == 0 else "StageFailed"},
    }
    return {**line, **values}


def test_aggregate_sums_max_and_times() -> None:
    failures = dict.fromkeys(FAILURE_KINDS, 0)
    lines = [
        start("e1", cache_mode="fresh", started_at="2026-09-26T10:00:00+00:00"),
        end(
            "e1",
            exit_code=1,
            rate_limit=True,
            finished_at="2026-09-26T10:30:00+00:00",
            elapsed_sec=1800.5,
            llm_calls=10,
            provider_invocations=12,
            model_turns=13,
            turns_by_model={OPUS: 13},
            max_concurrency=1,
            tokens=tokens(100, 10, 1000, 50),
            tokens_by_model={OPUS: tokens(100, 10, 1000, 50)},
            estimated_cost={"status": "recorded", "computed": 1.0, "declared": 1.1},
            failures_by_kind={**failures, "provider_error": 1},
            invocations_without_usage=0,
            invocations_without_turns=0,
            quota_at_start=quota(0.1, 100),
            quota_at_end=quota(1.0, 100),
            overage_invocations=0,
            invocations_without_rate_limit_event=0,
        ),
        start("other", run_id="run-2"),  # 別の列の行は数えない
        end("other", run_id="run-2", llm_calls=1000),
        start("e2", stage="recall", started_at="2026-09-26T16:00:00+00:00"),
        end(
            "e2",
            finished_at="2026-09-26T16:10:00+00:00",
            elapsed_sec=600.25,
            llm_calls=5,
            provider_invocations=6,
            model_turns=7,
            turns_by_model={OPUS: 3, HAIKU: 4},
            max_concurrency=3,
            tokens=tokens(30, 3, 300, 30),
            tokens_by_model={OPUS: tokens(10, 1, 100, 10), HAIKU: tokens(20, 2, 200, 20)},
            estimated_cost={"status": "recorded", "computed": 0.5, "declared": None},
            failures_by_kind={**failures, "timeout": 2},
            invocations_without_usage=2,
            invocations_without_turns=2,
            quota_at_start=None,
            quota_at_end=quota(0.3, 200),
            overage_invocations=0,
            invocations_without_rate_limit_event=2,
        ),
        start("e3", stage="audio", started_at="2026-09-26T17:00:00+00:00", metered=("ANTHROPIC_API_KEY",)),
        end(
            "e3",
            finished_at="2026-09-26T17:00:30+00:00",
            elapsed_sec=30.0,
            llm_calls=1,
            provider_invocations=1,
            model_turns=2,
            turns_by_model={HAIKU: 2},
            max_concurrency=2,
            tokens=tokens(5, 0, 0, 5),
            tokens_by_model={HAIKU: tokens(5, 0, 0, 5)},
            estimated_cost={"status": "recorded", "computed": 0.5, "declared": 0.4},
            failures_by_kind={**failures, "timeout": 1, "schema": 1},
            invocations_without_usage=1,
            invocations_without_turns=0,
            quota_at_start=quota(0.4, 300),
            quota_at_end=None,
            overage_invocations=1,
            invocations_without_rate_limit_event=0,
        ),
    ]
    sequence = aggregate(lines, RUN)

    assert (sequence["llm_calls"], sequence["provider_invocations"], sequence["model_turns"]) == (16, 19, 22)
    assert sequence["turns_by_model"] == {OPUS: 16, HAIKU: 6}
    assert sequence["tokens"] == tokens(135, 13, 1300, 85)
    assert sequence["tokens_by_model"] == {OPUS: tokens(110, 11, 1100, 60), HAIKU: tokens(25, 2, 200, 25)}
    assert (sequence["invocations_without_usage"], sequence["invocations_without_turns"]) == (3, 2)
    assert sequence["failures_by_kind"] == {**failures, "provider_error": 1, "timeout": 3, "schema": 1}
    assert (sequence["overage_invocations"], sequence["invocations_without_rate_limit_event"]) == (1, 2)
    assert sequence["max_concurrency"] == 3
    assert sequence["elapsed_sec_sum"] == pytest.approx(2430.75)
    assert sequence["wall_clock_sec"] == pytest.approx(7 * 3600 + 30), "最初の started_at から最後の finished_at"
    assert sequence["estimated_cost"] == {
        "notional": True,
        "currency": "USD",
        "status": "recorded",
        "computed": pytest.approx(2.0),
        "declared": pytest.approx(1.5),
        "relative_diff": pytest.approx(-0.25),
    }
    disclaimer = sequence["disclaimer"]
    assert isinstance(disclaimer, str) and "notional" in disclaimer
    assert sequence["quota"] == [
        {"execution_id": "e1", "at_start": quota(0.1, 100), "at_end": quota(1.0, 100)},
        {"execution_id": "e2", "at_start": None, "at_end": quota(0.3, 200)},
        {"execution_id": "e3", "at_start": quota(0.4, 300), "at_end": None},
    ]
    assert sequence["quota_missing"] == {"at_start": 1, "at_end": 1}
    assert sequence["overage_check"] == "failed"
    assert sequence["metered_env_names"] == ["ANTHROPIC_API_KEY"]

    # overage が無ければ、rate_limit_event の届かない呼び出しの和で unknown。
    without_overage = [*lines[:-2], lines[-2], {**lines[-1], "overage_invocations": 0}]
    assert aggregate(without_overage, RUN)["overage_check"] == "unknown"
    # 1実行でも recorded でなければ、列の estimated_cost は recorded にならない。
    unavailable = {"status": "unavailable", "reason": "zero_price", "computed": None, "declared": 0.4}
    cost = aggregate([*lines[:-1], {**lines[-1], "estimated_cost": unavailable}], RUN)["estimated_cost"]
    assert isinstance(cost, dict)
    assert (cost["status"], cost["computed"], cost["notional"]) == ("unavailable", None, True)
    only_first = aggregate(lines[:2], RUN)
    assert (only_first["overage_check"], only_first["metered_env_names"]) == ("passed", [])


def fresh(execution_id: str = "e1", stage: str = "cluster", **kwargs: Any) -> dict[str, Any]:
    return start(execution_id, cache_mode="fresh", stage=stage, **kwargs)


STATUS_TABLE: list[tuple[str, list[dict[str, Any]], str, list[str]]] = [
    (
        "completed",
        [fresh(), end("e1", exit_code=1, rate_limit=True), start("e2", stage="audio"), end("e2")],
        "completed",
        [],
    ),
    ("stage_by_stage", [fresh(), end("e1"), start("e2", stage="audio"), end("e2")], "completed", []),
    ("rate_limited_last", [fresh(), end("e1", exit_code=1, rate_limit=True)], "resumable", []),
    ("recall_exit_0_last", [fresh(), end("e1"), start("e2", stage="recall"), end("e2")], "resumable", []),
    ("first_resume", [start("e1", stage="audio"), end("e1")], "not_completable", ["first_not_fresh"]),
    (
        "first_normal",
        [start("e1", stage="audio", cache_mode="normal"), end("e1")],
        "not_completable",
        ["first_not_fresh"],
    ),
    (
        "other_stop_in_between",
        [fresh(), end("e1", exit_code=1, rate_limit=False), start("e2", stage="audio"), end("e2")],
        "not_completable",
        ["stopped_other:e1"],
    ),
    ("overage", [fresh(stage="audio"), end("e1", overage=1)], "not_completable", ["overage_invocations"]),
    (
        "metered_env",
        [fresh(), end("e1"), start("e2", stage="audio", metered=("AWS_PROFILE",)), end("e2")],
        "not_completable",
        ["metered_env_names:e2"],
    ),
    (
        "unclosed_start",
        [fresh(), start("e2", stage="audio"), end("e2")],
        "not_completable",
        ["unclosed_start:e1"],
    ),
]


@pytest.mark.parametrize(
    ("lines", "status", "reasons"),
    [pytest.param(lines, status, reasons, id=name) for name, lines, status, reasons in STATUS_TABLE],
)
def test_status_table(lines: list[dict[str, Any]], status: str, reasons: list[str]) -> None:
    sequence = aggregate(lines, RUN)
    assert (sequence["status"], sequence["not_completable_reasons"]) == (status, reasons)


def test_status_of_an_unknown_run_is_an_error() -> None:
    with pytest.raises(UnknownRunError):
        aggregate([fresh(), end("e1")], "no-such-run")


def write_log(path: Path, *lines: dict[str, Any] | bytes) -> Path:
    path.write_bytes(
        b"".join(
            (line if isinstance(line, bytes) else json.dumps(line, ensure_ascii=False).encode("utf-8")) + b"\n"
            for line in lines
        )
    )
    return path


def test_unreadable_lines(tmp_path: Path) -> None:
    torn = b'{"record": "end", "run_id": "run-1", "execution_id": "e'  # 電源断で途中まで書かれた行
    broken_utf8 = '{"record": "start", "run_id": "日'.encode()[:-1]

    before = write_log(
        tmp_path / "before.jsonl", start("x", run_id="run-0"), torn, broken_utf8, fresh(stage="audio"), end("e1")
    )
    lines = read_log(before)
    assert lines[1:3] == [None, None]
    sequence = aggregate(lines, RUN)
    assert (sequence["status"], sequence["not_completable_reasons"]) == ("completed", [])
    assert sequence["unreadable_lines_before"] == 2

    after = write_log(
        tmp_path / "after.jsonl",
        fresh(),
        end("e1", exit_code=1, rate_limit=True),
        torn,
        start("e2", stage="audio"),
        end("e2"),
    )
    sequence = aggregate(read_log(after), RUN)
    assert (sequence["status"], sequence["not_completable_reasons"]) == ("not_completable", ["unreadable_line:3"])
    assert sequence["unreadable_lines_before"] == 0

    # 既知の形でない行: 対象 run_id なら例外、別の列の行なら数えない。
    incomplete = {key: value for key, value in end("e1").items() if key != "stop"}
    with pytest.raises(ValueError, match="stop"):
        aggregate([fresh(), incomplete], RUN)
    assert aggregate([fresh(), end("e1"), {**incomplete, "run_id": "run-2"}], RUN)["status"] == "resumable"
    with pytest.raises(ValueError, match="record"):
        aggregate([fresh(), {**end("e1"), "record": "finish"}], RUN)
    with pytest.raises(ValueError, match="2つ"):
        aggregate([fresh(), end("e1"), end("e1")], RUN)
