"""A-1: ストアの所在とスキーマは store/location.py の1箇所で定義され、接続を開く経路はそこだけ(C-012)。"""

import ast
import re
import sqlite3
from pathlib import Path

from quiz_sift.domain.ids import DeckId
from quiz_sift.safety.gitignore_guard import is_ignored, parse_gitignore
from quiz_sift.store import location
from quiz_sift.store.deck_scope import DeckScopedStore

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
LOCATION = SRC / "quiz_sift" / "store" / "location.py"


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imports_sqlite3(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "sqlite3" for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "sqlite3":
            return True
    return False


def _imports_location_connect(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "quiz_sift.store.location":
            if any(alias.name in ("connect", "*") for alias in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and node.module == "quiz_sift.store":
            if any(alias.name == "location" for alias in node.names):
                return True
        if isinstance(node, ast.Import) and any(alias.name == "quiz_sift.store.location" for alias in node.names):
            return True
    return False


def test_single_definition_of_location_and_schema(tmp_path: Path) -> None:
    store_pkg = LOCATION.parent
    for module in _modules():
        text = module.read_text(encoding="utf-8")
        tree = ast.parse(text)
        if module != LOCATION:
            # sqlite3 を import できるのは location.py だけ。接続を開く第2の経路がコード上に無い。
            assert not _imports_sqlite3(tree), f"{module.relative_to(REPO)} が sqlite3 を import している"
            # スキーマの定義も location.py だけ。
            assert not re.search(r"CREATE\s+(TABLE|INDEX)", text, re.IGNORECASE), module
        if module.parent != store_pkg:
            # store/ の外は接続に触れず、DeckScopedStore だけを使う。
            assert not _imports_location_connect(tree), f"{module.relative_to(REPO)} が接続を直接開いている"

    # 所在は root/out/ の下で、.gitignore 対象(学習記録を追跡しない)。
    path = location.store_path(tmp_path)
    assert path == tmp_path / "out" / location.STORE_FILENAME
    rules = parse_gitignore((REPO / ".gitignore").read_text(encoding="utf-8"))
    assert is_ignored(path.relative_to(tmp_path).as_posix(), rules)

    # ストアを使うと、その所在にだけファイルができ、スキーマはすべて SCHEMA から来ている。
    DeckScopedStore(DeckId("aws-saa"), tmp_path).cluster_stats()
    created = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert path in created
    assert all(p.name.startswith(location.STORE_FILENAME) for p in created), created
    reference = sqlite3.connect(":memory:")
    reference.executescript(location.SCHEMA)
    actual = sqlite3.connect(path)
    try:
        query = "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        assert actual.execute(query).fetchall() == reference.execute(query).fetchall()
    finally:
        actual.close()
        reference.close()
