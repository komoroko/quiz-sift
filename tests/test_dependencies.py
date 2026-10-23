"""依存の解決結果についての検査。

setuptools 81.0.0 に PYSEC-2026-3447 が見つかり、修正は 83.0.0 に入った(D-165)。torch 2.11 が
`setuptools<82` を要求するので、setuptools を上げると torch は 2.10 系に下がる。制約が外されたり、
lock が古い解決に戻ったりしたら、ここで赤になる。
"""

import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _release(version: str) -> tuple[int, ...]:
    """`2.10.0+cu128` のような版文字列から、ローカル版を除いた数値部分を取り出す。"""
    return tuple(int(part) for part in version.split("+", 1)[0].split("."))


def _locked(packages: list[dict[str, Any]], name: str) -> str:
    versions = [package["version"] for package in packages if package["name"] == name]
    assert len(versions) == 1, f"uv.lock に {name} がちょうど1つ無い: {versions}"
    version: str = versions[0]
    return version


def test_setuptools_is_constrained_to_the_fixed_version() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "setuptools>=83" in pyproject["tool"]["uv"].get("constraint-dependencies", [])

    packages = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
    assert _release(_locked(packages, "setuptools")) >= (83,)
    for name in ("torch", "torchaudio"):
        version = _locked(packages, name)
        assert _release(version)[:2] == (2, 10), f"{name} が 2.10 系でない: {version}"
        assert version.endswith("+cu128"), f"{name} が cu128 ビルドでない: {version}"
