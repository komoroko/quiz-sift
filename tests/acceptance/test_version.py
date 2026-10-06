"""製品のバージョンの受入テスト(C-022)。claim ごとに1本。"""

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import quiz_sift

_REPO = Path(__file__).resolve().parents[2]
_VERSION = "0.1.1"
_OLD_VERSION = ".".join(("0", "1", "0"))
"""旧版の文字列。組み立てで作り、このファイル自身が走査で数えられないようにする。"""


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _locked_quiz_sift() -> dict[str, Any]:
    lock = tomllib.loads((_REPO / "uv.lock").read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = [package for package in lock["package"] if package["name"] == "quiz-sift"]
    (entry,) = entries
    return entry


def test_c022_version_matches_in_four_places() -> None:
    """受入基準 A-1: pyproject.toml・`__version__`・uv.lock の quiz-sift 項目・`--version` が 0.1.1 で一致する。"""
    completed = subprocess.run(
        [sys.executable, "-m", "quiz_sift.cli", "--version"],
        env={**os.environ, "PYTHONPATH": str(_REPO / "src")},
        capture_output=True,
        text=True,
        check=True,
    )
    assert _pyproject()["project"]["version"] == _VERSION
    assert quiz_sift.__version__ == _VERSION
    assert _locked_quiz_sift()["version"] == _VERSION
    assert completed.stdout.strip() == f"quiz-sift {_VERSION}"


def test_c022_old_version_is_gone() -> None:
    """受入基準 A-2: src・tests・pyproject.toml・uv.lock の quiz-sift 項目に旧版が残らない。"""
    files = [
        path
        for top in ("src", "tests")
        for path in sorted((_REPO / top).rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    ]
    assert len(files) > 50
    leftovers = [str(path.relative_to(_REPO)) for path in files if _OLD_VERSION.encode() in path.read_bytes()]
    assert leftovers == []
    assert _OLD_VERSION not in (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert _locked_quiz_sift()["version"] != _OLD_VERSION
