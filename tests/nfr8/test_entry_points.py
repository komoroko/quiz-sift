"""製品として到達可能な入口すべての import グラフに、metered API へ HTTP を発行しうるモジュールが無い
(NFR-8 (d)、C-020 A-4)。

入口は `[project.scripts]` だけではない(AR-69)。`__main__` ガードを持つモジュールと、`python -m <パッケージ>`
で走る `__main__.py` も入口に数える。import は関数の中にあるものも辿る(遅延 import も到達可能である)。
"""

import ast
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
PACKAGE = SRC / "quiz_sift"

METERED = frozenset(
    {
        "anthropic",
        "openai",
        "httpx",
        "requests",
        "aiohttp",
        "urllib3",
        "boto3",
        "botocore",
        "google",
        "vertexai",
        "litellm",
    }
)
"""metered API へ HTTP を発行しうる第三者パッケージ(従量課金の SDK と、その下で使われる HTTP クライアント)。"""


def module_path(name: str) -> Path | None:
    """`quiz_sift.*` のモジュール名に対応するファイル。無ければ `None`。"""
    base = SRC.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported(name: str, path: Path) -> set[str]:
    """`path` の中の import 文(関数の中を含む)が指すモジュール名。`from a import b` は `a.b` も候補に入れる。"""
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                base = f"{anchor}.{node.module}" if node.module else anchor
            else:
                assert node.module is not None
                base = node.module
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def reachable(entries: Iterable[str]) -> tuple[set[str], set[str]]:
    """入口から辿れる `quiz_sift` のモジュールと、そこが import する外部パッケージの最上位名。"""
    seen: set[str] = set()
    external: set[str] = set()
    pending = list(entries)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        path = module_path(name)
        if path is None:
            continue
        seen.add(name)
        # サブモジュールを import すると、親パッケージの __init__ も走る。
        parts = name.split(".")
        pending.extend(".".join(parts[:i]) for i in range(1, len(parts)))
        for target in imported(name, path):
            root = target.split(".")[0]
            if root == "quiz_sift":
                if module_path(target) is not None:
                    pending.append(target)
            elif root not in sys.stdlib_module_names:
                external.add(root)
    return seen, external


def has_main_guard(path: Path) -> bool:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in node.test.comparators)
        ):
            return True
    return False


def entry_modules() -> set[str]:
    """コンソールスクリプト・`__main__` ガードを持つモジュール・`__main__.py` のモジュール名。"""
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    entries = {target.split(":")[0] for target in scripts.values()}
    for path in PACKAGE.rglob("*.py"):
        if path.name == "__main__.py" or has_main_guard(path):
            entries.add(module_name(path))
    return entries


def test_no_entry_point_reaches_a_metered_client() -> None:
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts == {"quiz-sift": "quiz_sift.cli:main"}

    entries = entry_modules()
    assert {"quiz_sift.cli", "quiz_sift.server.__main__"} <= entries  # 走査が空振りしていない

    modules, external = reachable(entries)
    assert "quiz_sift.composition" in modules and "quiz_sift.adapters.llm_claude_cli" in modules
    assert external & METERED == set(), sorted(external)


def test_reachability_sees_the_sdk_when_it_is_imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """判定が赤くなり得ること: 入口の cli が関数の中で `anthropic` を遅延 import すると、辿った外部パッケージに入る。"""
    sdk_cli = tmp_path / "cli.py"
    sdk_cli.write_text(
        "from quiz_sift.adapters import llm_prompts\n\n\ndef build():\n    import anthropic\n\n    return anthropic\n",
        encoding="utf-8",
    )
    real = module_path
    monkeypatch.setattr(
        sys.modules[__name__], "module_path", lambda name: sdk_cli if name == "quiz_sift.cli" else real(name)
    )

    modules, external = reachable({"quiz_sift.cli"})
    assert {"quiz_sift.cli", "quiz_sift.adapters.llm_prompts"} <= modules
    assert "anthropic" in external & METERED


def main_calls(path: Path) -> list[ast.Call]:
    """`path` の中の `main(...)` と `<何か>.main(...)` の呼び出し。"""
    return [
        node
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "main")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "main")
        )
    ]


def argv_only(call: ast.Call) -> bool:
    """引数が無いか、位置引数1つ(argv)だけ。キーワードと `*` / `**` の展開は認めない。"""
    return not call.keywords and len(call.args) <= 1 and not any(isinstance(arg, ast.Starred) for arg in call.args)


def test_entry_points_call_main_with_argv_only() -> None:
    """製品の入口は `main` を argv だけで呼び、`llm_factory` / `preflight` の既定値を差し替えない(D-143、AR-126)。

    コンソールスクリプトはラッパが `main()` を引数なしで呼ぶので、対象が関数そのものであることを確かめる。
    """
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts == {"quiz-sift": "quiz_sift.cli:main"}

    calls = {name: main_calls(path) for name in entry_modules() if (path := module_path(name)) is not None}
    assert calls["quiz_sift.cli"]  # 走査が空振りしていない(`sys.exit(main())`)
    offenders = [f"{name}:{call.lineno}" for name, found in calls.items() for call in found if not argv_only(call)]
    assert offenders == []


def test_argv_only_rejects_a_substituted_default(tmp_path: Path) -> None:
    """判定が赤くなり得ること: 既定値を差し替える呼び出しを拒み、argv だけの呼び出しは通す。"""
    entry = tmp_path / "entry.py"
    entry.write_text("sys.exit(main())\nmain(argv, preflight=lambda: None)\n", encoding="utf-8")
    plain, substituted = sorted(main_calls(entry), key=lambda call: call.lineno)
    assert argv_only(plain)
    assert not argv_only(substituted)
