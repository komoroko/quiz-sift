"""ports の Protocol と、外部 SDK の import が adapters に閉じていること(C-019 / D-008)。"""

import ast
import inspect
import sys
from pathlib import Path

import pytest

from quiz_sift.ports.clock import Clock, SystemClock
from quiz_sift.ports.llm import Llm
from quiz_sift.ports.tts import PcmAudio, Tts

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "quiz_sift"
ADAPTERS = PACKAGE / "adapters"

NON_SDK_DEPENDENCIES = frozenset({"yaml", "fugashi", "unidic_lite"})
"""外部サービスに触らない実行時依存。adapters の外から import してよい。"""


def external_imports(source: str) -> set[str]:
    """標準ライブラリ・自パッケージ・非 SDK 依存のどれでもない import の最上位名。"""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    allowed = sys.stdlib_module_names | NON_SDK_DEPENDENCIES | {"quiz_sift"}
    return roots - allowed


def test_external_sdk_imports_live_only_in_adapters() -> None:
    offenders = {
        str(path.relative_to(PACKAGE)): found
        for path in PACKAGE.rglob("*.py")
        if ADAPTERS not in path.parents and (found := external_imports(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}

    # 検査器が空振りしていないこと: SDK の import を検出し、標準ライブラリ・自パッケージ・非 SDK 依存は数えない。
    assert external_imports("from anthropic.types import Message\n") == {"anthropic"}
    assert external_imports("import json\nfrom quiz_sift.ports.llm import Llm\nimport yaml\n") == set()

    # Llm / Tts / Clock が Protocol として定義されている。
    for port, methods in (
        (Llm, {"classify_clusters", "summarize_cluster", "generate_recall", "describe_term", "check_entailments"}),
        (Tts, {"synthesize"}),
        (Clock, {"now", "monotonic"}),
    ):
        assert getattr(port, "_is_protocol", False), port
        assert methods <= {name for name, _ in inspect.getmembers(port, inspect.isfunction)}, port


def test_system_clock_satisfies_the_port() -> None:
    clock: Clock = SystemClock()
    assert clock.now().tzinfo is not None
    assert clock.monotonic() <= clock.monotonic()


def test_pcm_audio_refuses_malformed_frames() -> None:
    with pytest.raises(ValueError):
        PcmAudio(samples=b"\x00", sample_rate=24000)
    with pytest.raises(ValueError):
        PcmAudio(samples=b"", sample_rate=0)
