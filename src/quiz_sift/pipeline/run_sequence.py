"""実行の列の集約と状態(NFR-7、ADR-018、D-139・D-145)。

同じ `run_id` で連なる実行の開始行と終了行を読み、回数・トークン・コストは和、並行度は最大、
経過時間は実行時間の和と全体の壁時計の両方として合算する。列の `status` は
`completed` / `resumable` / `not_completable` のどれか1つで、人はこれを読んで `--resume` するかを決める。

入力は `meter.jsonl` の各行を読んだ dict で、JSON として読めなかった行は `None` で表す(行番号は位置 + 1)。
旧形式の行を読む分岐は作らない。対象 `run_id` の行が既知の形でなければ例外で止まる。
"""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

DISCLAIMER = "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。"

FINAL_STAGE = "audio"
"""列の最終段。最後の実行がこの段を exit 0 で終えた列だけが `completed`(D-145)。"""

type Line = Mapping[str, Any] | None
"""`meter.jsonl` の1行。JSON のオブジェクトとして読めなかった行は `None`。"""

_START_KEYS = frozenset(
    {"record", "run_id", "execution_id", "started_at", "cache_mode", "stage", "env_names", "metered_env_names"}
)
_END_KEYS = frozenset(
    {
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
        "estimated_cost",
        "failures_by_kind",
        "invocations_without_usage",
        "invocations_without_turns",
        "ended_by_rate_limit",
        "quota_at_start",
        "quota_at_end",
        "overage_invocations",
        "invocations_without_rate_limit_event",
        "stop",
    }
)
_SUMMED = (
    "llm_calls",
    "provider_invocations",
    "model_turns",
    "invocations_without_usage",
    "invocations_without_turns",
    "overage_invocations",
    "invocations_without_rate_limit_event",
)


class UnknownRunError(LookupError):
    """開始行の無い `run_id` を問い合わせた。"""


def read_log(path: Path) -> list[Line]:
    """`meter.jsonl` を行ごとに読む。UTF-8 の JSON オブジェクトとして読めない行は `None` にし、例外にしない。"""
    lines: list[Line] = []
    for raw in path.read_bytes().splitlines():
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            lines.append(None)
            continue
        lines.append(value if isinstance(value, dict) else None)
    return lines


def _check(line: Mapping[str, Any], number: int) -> None:
    record = line.get("record")
    keys = {"start": _START_KEYS, "end": _END_KEYS}.get(record) if isinstance(record, str) else None
    if keys is None:
        raise ValueError(f"{number} 行目: record が start でも end でもない: {record!r}")
    missing = keys - line.keys()
    if missing:
        raise ValueError(f"{number} 行目: {record} 行に必須キーが無い: {sorted(missing)}")


def _sum_maps(maps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mapping in maps:
        for key, value in mapping.items():
            out[key] = _sum_maps([out.get(key, {}), value]) if isinstance(value, Mapping) else out.get(key, 0) + value
    return out


def _estimated_cost(ends: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    costs = [end["estimated_cost"] for end in ends]
    declared_values = [cost["declared"] for cost in costs if cost["declared"] is not None]
    declared = sum(declared_values) if declared_values else None
    cost: dict[str, object] = {"notional": True, "currency": "USD", "declared": declared}
    # 1実行でも記録済みでなければ、和は記録済みと言えない(AR-36)。
    if not costs or any(cost["status"] != "recorded" for cost in costs):
        return {**cost, "status": "unavailable", "reason": "not_all_recorded", "computed": None, "relative_diff": None}
    computed = sum(cost["computed"] for cost in costs)
    relative_diff = None if declared is None or computed == 0 else (declared - computed) / computed
    return {**cost, "status": "recorded", "computed": computed, "relative_diff": relative_diff}


def aggregate(lines: Sequence[Line], run_id: str) -> dict[str, object]:
    """`run_id` の列の集約と状態を返す。

    `Meter.finish` は、いま書く終了行を `lines` の末尾に足して呼ぶ。これで締めている実行の開始行は閉じた
    ものとして数える。開始行の無い `run_id` は `UnknownRunError`。
    """
    starts: list[tuple[int, Mapping[str, Any]]] = []
    ends: dict[str, Mapping[str, Any]] = {}
    unreadable: list[int] = []
    for number, line in enumerate(lines, start=1):
        if line is None:
            unreadable.append(number)
            continue
        if "run_id" not in line:
            raise ValueError(f"{number} 行目: run_id が無い")
        if line["run_id"] != run_id:
            continue
        _check(line, number)
        execution_id = line["execution_id"]
        if line["record"] == "start":
            if any(start["execution_id"] == execution_id for _, start in starts):
                raise ValueError(f"{number} 行目: 実行 {execution_id} の開始行が2つある")
            starts.append((number, line))
        elif execution_id in ends or not any(start["execution_id"] == execution_id for _, start in starts):
            raise ValueError(f"{number} 行目: 実行 {execution_id} の終了行に対応する開始行が無いか、終了行が2つある")
        else:
            ends[execution_id] = line
    if not starts:
        raise UnknownRunError(f"run_id {run_id} の開始行が無い")

    first_line = starts[0][0]
    closed = [ends[start["execution_id"]] for _, start in starts if start["execution_id"] in ends]
    summed = {key: sum(end[key] for end in closed) for key in _SUMMED}
    metered = sorted({name for _, start in starts for name in start["metered_env_names"]})
    if summed["overage_invocations"]:
        overage_check = "failed"
    elif summed["invocations_without_rate_limit_event"]:
        overage_check = "unknown"
    else:
        overage_check = "passed"
    quota = [
        {"execution_id": end["execution_id"], "at_start": end["quota_at_start"], "at_end": end["quota_at_end"]}
        for end in closed
    ]
    last_end = list(ends.values())[-1] if ends else None

    reasons: list[str] = []
    if starts[0][1]["cache_mode"] != "fresh":
        reasons.append("first_not_fresh")
    for _, start in starts:
        execution_id = start["execution_id"]
        end = ends.get(execution_id)
        if end is None:
            reasons.append(f"unclosed_start:{execution_id}")
        elif end["stop"]["exit_code"] != 0 and not end["ended_by_rate_limit"]["value"]:
            reasons.append(f"stopped_other:{execution_id}")
        if start["metered_env_names"]:
            reasons.append(f"metered_env_names:{execution_id}")
    if summed["overage_invocations"]:
        reasons.append("overage_invocations")
    reasons.extend(f"unreadable_line:{number}" for number in unreadable if number > first_line)

    last_start = starts[-1][1]
    final = ends.get(last_start["execution_id"])
    if reasons:
        status = "not_completable"
    elif final is not None and final["stop"]["exit_code"] == 0 and last_start["stage"] == FINAL_STAGE:
        status = "completed"
    else:
        status = "resumable"

    return {
        "status": status,
        "not_completable_reasons": reasons,
        "unreadable_lines_before": sum(number < first_line for number in unreadable),
        **summed,
        "turns_by_model": _sum_maps([end["turns_by_model"] for end in closed]),
        "tokens": _sum_maps([end["tokens"] for end in closed]),
        "tokens_by_model": _sum_maps([end["tokens_by_model"] for end in closed]),
        "failures_by_kind": _sum_maps([end["failures_by_kind"] for end in closed]),
        "max_concurrency": max((end["max_concurrency"] for end in closed), default=0),
        "elapsed_sec_sum": sum(end["elapsed_sec"] for end in closed),
        "wall_clock_sec": None
        if last_end is None
        else _seconds_between(starts[0][1]["started_at"], last_end["finished_at"]),
        "estimated_cost": _estimated_cost(closed),
        "disclaimer": DISCLAIMER,
        "quota": quota,
        "quota_missing": {
            "at_start": sum(entry["at_start"] is None for entry in quota),
            "at_end": sum(entry["at_end"] is None for entry in quota),
        },
        "overage_check": overage_check,
        "metered_env_names": metered,
    }


def _seconds_between(started_at: str, finished_at: str) -> float:
    return (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds()
