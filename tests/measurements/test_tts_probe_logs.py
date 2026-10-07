"""TTS プローブの記録の検査(T-069 / C-007 R-7、D-215)。

サンドボックスには CosyVoice も torch も無いので、証拠は `tests/fixtures/tts-probe/probe.py` がホストで書いた
`docs/test/measurements/tts-probe.md` で、このテストはその「## 記録」と「## 結論」を機械判定する。
無音判定は記録された区間列から製品の `has_pause_structure` で判定し直し、記録の真偽と突き合わせる。
記録が無ければ赤になる。
"""

import ast
import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

from quiz_sift.audio.silence import PAUSE_THRESHOLD_SECONDS, Run, has_pause_structure
from quiz_sift.script.generate import PAUSE_SECONDS

ROOT = Path(__file__).resolve().parent.parent.parent
RECORD = ROOT / "docs" / "test" / "measurements" / "tts-probe.md"
FIXTURE = ROOT / "tests" / "fixtures" / "tts-probe"
PROBE = "tests/fixtures/tts-probe/probe.py"
PYWORLD_GIT = "https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder"


def record_text() -> str:
    return RECORD.read_text(encoding="utf-8")


def probe_record() -> dict[str, Any]:
    match = re.search(r"## 記録\n\n```yaml\n(.*?)\n```", record_text(), re.S)
    assert match, "tts-probe.md に「## 記録」の yaml ブロックが無い"
    loaded = yaml.safe_load(match.group(1))
    assert isinstance(loaded, dict)
    return loaded


def conclusion_lines() -> list[str]:
    _, sep, tail = record_text().rpartition("\n## 結論\n")
    assert sep, "tts-probe.md に「## 結論」が無い"
    assert "\n## " not in tail, "「## 結論」が末尾の節でない"
    return [line for line in tail.splitlines() if line.strip()]


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_c007_probe_script_passes_the_silence_check() -> None:
    record = probe_record()
    # 記録を書いたのは今のプローブで、通した台本は今の合成フィクスチャ。
    assert record["probe"] == PROBE
    probe = ROOT / PROBE
    assert probe.is_file()
    script = FIXTURE / "script.json"
    assert record["script_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
    spec = json.loads(script.read_text(encoding="utf-8"))
    # 合成の台本(教材を含まない): すべての本文が「合成の」で始まる文で、デッキは実デッキと名前が重ならない。
    assert spec["deck_id"] == "tts-probe"
    for text in (spec["prompt"], *spec["answers"], spec["discrimination"]):
        assert text.startswith("合成の"), text

    # 製品の audio 段と同じ経路: synthesize_tracks(合成 → 間の挿入 → mp3 → 復号 → 無音判定)を呼び、LLM を呼ばない。
    modules = _imported_modules(probe)
    assert "quiz_sift.audio.synthesize" in modules
    assert "quiz_sift.adapters.tts_cosyvoice" in modules
    assert not {m for m in modules if "llm" in m or m in {"quiz_sift.cli", "quiz_sift.composition", "anthropic"}}
    assert "synthesize_tracks(" in probe.read_text(encoding="utf-8")
    assert record["llm_calls"] == 0

    # 文と間を含む: 問いかけ → 間 → 答え → 識別ポイントで、読み上げは間を除く3回。
    assert record["segments"] == ["Prompt", "Pause", "Answer", "Discrimination"]
    assert record["tts_calls"] == 3
    assert record["inserted_pause_seconds"] == PAUSE_SECONDS
    assert record["threshold_seconds"] == PAUSE_THRESHOLD_SECONDS
    assert record["mp3"].endswith(".mp3")
    assert re.fullmatch(r"[0-9a-f]{64}", record["mp3_sha256"])

    # 記録された区間列を製品の判定に掛け直すと真で、記録の真偽・結論と一致する。
    runs = [Run(r["voiced"], r["start"], r["seconds"]) for r in record["runs"]]
    assert abs(sum(r.seconds for r in runs) - record["track_seconds"]) < 0.05
    inner = [
        r
        for i, r in enumerate(runs)
        if not r.voiced
        and r.seconds >= PAUSE_THRESHOLD_SECONDS
        and any(x.voiced for x in runs[:i])
        and any(x.voiced for x in runs[i + 1 :])
    ]
    assert inner, "前後を有音に挟まれた 3 秒以上の無音区間が無い"
    assert has_pause_structure(runs) is True
    assert record["has_pause_structure"] is True
    assert conclusion_lines() == ["無音判定: 真"]


def test_probe_record_names_torch_and_cosyvoice() -> None:
    record = probe_record()
    assert re.fullmatch(r"2\.10\.\d+(?:\+\w+)?", record["torch_version"]), record["torch_version"]
    assert re.fullmatch(r"[0-9a-f]{40}", record["cosyvoice_commit"]), record["cosyvoice_commit"]
    assert isinstance(record["cosyvoice_dirty"], bool)
    assert Path(record["model_dir"]).name == "CosyVoice-300M-SFT"
    assert re.fullmatch(r"[0-9a-f]{40}", record["commit"])


def test_lock_pins_pyworld_to_the_upstream_tag_without_pkg_resources() -> None:
    # PyPI の pyworld 0.3.5 は `import pkg_resources` し、setuptools>=83(D-165)では CosyVoice ごと import できない。
    # プローブが走った環境は、上流の git タグ v0.3.6(importlib.metadata 版)に固定した lock から作ったもの。
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["uv"]["sources"]["pyworld"] == {"git": PYWORLD_GIT, "tag": "v0.3.6"}

    packages = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
    pyworld = [package for package in packages if package["name"] == "pyworld"]
    assert len(pyworld) == 1, pyworld
    assert pyworld[0]["version"] == "0.3.6"
    assert re.fullmatch(re.escape(PYWORLD_GIT) + r"\?tag=v0\.3\.6#[0-9a-f]{40}", pyworld[0]["source"]["git"])
