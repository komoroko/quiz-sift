"""開発環境のスケルトンが組み上がっていることの確認。

品質ゲートが「収集対象ゼロ」で落ちないための最小の1本でもある。製品コードが入り次第、
このファイルは役目を終えるので削除する。

テストが `src/` ではなく `tests/` にあるのは、`.rein/config.yaml` の `guard.paths` が
`src/` を含み、mandate が承認されるまで書き込みが拒否されるため。Loose Rein の規則が
`tests/` を非ガードの置き場として名指ししている。
"""

import sys
import tomllib
from pathlib import Path


def test_python_version_matches_the_pinned_runtime() -> None:
    assert sys.version_info[:2] == (3, 13)


def test_pyproject_declares_the_src_layout() -> None:
    raw = Path(__file__).resolve().parent.parent / "pyproject.toml"
    config = tomllib.loads(raw.read_text(encoding="utf-8"))
    assert config["tool"]["pytest"]["ini_options"]["pythonpath"] == ["src"]
