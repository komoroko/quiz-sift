"""RunMeter と FailureKind(C-019 / D-104)。"""

import ast
import inspect
from pathlib import Path
from typing import get_protocol_members

import quiz_sift.ports.metering as metering
from quiz_sift.pipeline.meter import Meter
from quiz_sift.ports.llm import UsageRecorder
from quiz_sift.ports.metering import FailureKind, RunMeter

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
