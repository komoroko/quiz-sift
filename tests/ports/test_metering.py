"""RunMeter と FailureKind、レート枠による停止の述語(C-019 / D-104 / D-128 / D-135)。"""

import ast
import inspect
from pathlib import Path
from typing import get_protocol_members

import pytest

import quiz_sift.ports.metering as metering
from quiz_sift.pipeline.meter import Meter
from quiz_sift.ports.llm import UsageRecorder
from quiz_sift.ports.metering import FailureKind, QuotaWindow, RateLimitInfo, RunMeter, rate_limit_stop

EXPECTED_METHODS = {"record", "begin_call", "begin_invocation", "end_invocation", "record_quota", "record_failure"}
"""チケットから写した RunMeter の口。実装の定義を読まない。"""

EXPECTED_KINDS = {"SPAWN", "TIMEOUT", "PARSE", "SCHEMA", "TRUNCATED", "INCOMPLETE_IDS", "PROVIDER_ERROR"}
"""設計 R-3 節の7種。"""


def satisfies(cls: type, protocol: type) -> bool:
    """`cls` が `protocol` の全メソッドを同じ引数名で持つか。"""
    for name in get_protocol_members(protocol):
        method = getattr(cls, name, None)
        if not callable(method):
            return False
        wanted = list(inspect.signature(getattr(protocol, name)).parameters)
        if list(inspect.signature(method).parameters) != wanted:
            return False
    return True


def imported_modules(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def test_run_meter_protocol_and_failure_kinds() -> None:
    assert UsageRecorder in RunMeter.__mro__
    assert get_protocol_members(RunMeter) == EXPECTED_METHODS
    assert satisfies(Meter, RunMeter)

    # 検査器が空振りしていないこと: メソッドを1つ欠いたクラスは満たさない。
    class Partial:
        record = Meter.record
        begin_call = Meter.begin_call
        begin_invocation = Meter.begin_invocation
        end_invocation = Meter.end_invocation
        record_quota = Meter.record_quota

    assert not satisfies(Partial, RunMeter)

    assert {kind.name for kind in FailureKind} == EXPECTED_KINDS
    assert len({kind.value for kind in FailureKind}) == 7

    source = Path(inspect.getfile(metering)).read_text(encoding="utf-8")
    assert not any(module.startswith("quiz_sift.adapters") for module in imported_modules(source))
    assert "quiz_sift.adapters.x" in {m for m in imported_modules("from quiz_sift.adapters.x import y\n")}


def rate_limit(status: str, is_using_overage: bool | None) -> RateLimitInfo:
    return RateLimitInfo(
        status=status,
        resets_at=1_790_000_000,
        five_hour=QuotaWindow(utilization=1.0, resets_at=1_790_000_000),
        seven_day=QuotaWindow(utilization=0.5, resets_at=1_790_500_000),
        rate_limit_type="five_hour",
        is_using_overage=is_using_overage,
        overage_status="rejected",
    )


REJECTED = rate_limit("rejected", False)


@pytest.mark.parametrize(
    ("kind", "is_error", "last", "expected"),
    [
        pytest.param(FailureKind.PROVIDER_ERROR, True, REJECTED, True, id="all-five-hold"),
        pytest.param(FailureKind.PARSE, True, REJECTED, False, id="kind-not-provider-error"),
        pytest.param(FailureKind.PROVIDER_ERROR, False, REJECTED, False, id="is-error-false"),
        pytest.param(FailureKind.PROVIDER_ERROR, None, REJECTED, False, id="is-error-missing"),
        pytest.param(FailureKind.PROVIDER_ERROR, True, None, False, id="no-event"),
        pytest.param(FailureKind.PROVIDER_ERROR, True, rate_limit("allowed", False), False, id="status-allowed"),
        pytest.param(
            FailureKind.PROVIDER_ERROR, True, rate_limit("allowed_warning", False), False, id="status-allowed-warning"
        ),
        pytest.param(FailureKind.PROVIDER_ERROR, True, rate_limit("rejected", True), False, id="overage-true"),
        pytest.param(FailureKind.PROVIDER_ERROR, True, rate_limit("rejected", None), False, id="overage-missing"),
    ],
)
def test_rate_limit_stop_predicate_truth_table(
    kind: FailureKind, is_error: bool | None, last: RateLimitInfo | None, expected: bool
) -> None:
    assert rate_limit_stop(kind, is_error, last) is expected
    assert {"rate_limit_type", "is_using_overage", "overage_status"} <= set(RateLimitInfo.__slots__)
    source = Path(inspect.getfile(metering)).read_text(encoding="utf-8")
    assert "ALLOWED_STATUS" not in source, "D-110 の判定の名前は消す(D-128)"
