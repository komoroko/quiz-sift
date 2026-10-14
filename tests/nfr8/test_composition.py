"""既定 `Llm` の選択点が `composition.build_default_llm` ただ1つであること(NFR-8 (c)、C-020 A-3)。"""

import ast
import inspect
from pathlib import Path

from quiz_sift import composition

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "quiz_sift"
COMPOSITION = PACKAGE / "composition.py"

FORBIDDEN = (
    ast.If,
    ast.Match,
    ast.Try,
    ast.TryStar,
    ast.Subscript,
    ast.IfExp,
    ast.BoolOp,
    ast.Lambda,
    ast.Attribute,
    ast.comprehension,
    ast.Import,
    ast.ImportFrom,
)
"""関数の本体に現れてはならない節(要件の列挙に、`from` 形の import と `except*` を足したもの)。"""

LLM_METHODS = frozenset(
    {"classify_clusters", "summarize_cluster", "generate_recall", "describe_term", "check_entailments"}
)
"""`ports.llm.Llm` のメソッド。これをすべて定義するクラスを `Llm` の実装と見なす。"""


def selection_point_violations(source: str) -> list[str]:
    """`build_default_llm` が「return 文1つ、右辺はクラス名の直接呼び出し」から外れている点を返す。

    名前がクラスかどうかは、この判定の外で `inspect.isclass` が確かめる。
    """
    module = ast.parse(source)
    (function,) = [n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "build_default_llm"]
    problems: list[str] = []
    if len(function.body) != 1 or not isinstance(function.body[0], ast.Return):
        problems.append("本体が return 文1つでない")
    else:
        value = function.body[0].value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
            problems.append("右辺が名前の直接呼び出しでない")
    body = [node for statement in function.body for node in ast.walk(statement)]
    problems.extend(f"{type(node).__name__} を含む" for node in body if isinstance(node, FORBIDDEN))
    parameters = {arg.arg for arg in function.args.args}
    callees = {node.func.id for node in body if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    names = {node.id for node in body if isinstance(node, ast.Name)}
    problems.extend(f"引数でも呼び出すクラスでもない名前 {name}" for name in sorted(names - parameters - callees))
    if len(callees) != 1:
        problems.append(f"呼び出しが {len(callees)} 個")
    if function.args.defaults or function.args.kw_defaults or function.args.kwonlyargs:
        problems.append("引数に既定値かキーワード専用引数がある")
    return problems


def llm_classes() -> set[str]:
    """`src/` で `Llm` のメソッドをすべて定義しているクラスの名前。"""
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                defined = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
                if LLM_METHODS <= defined:
                    found.add(node.name)
    return found


def constructions(classes: set[str], root: Path = PACKAGE) -> list[str]:
    """`root` の配下で `classes` のどれかを呼び出している式の位置(`相対パス:行`)。属性越しの呼び出しも数える。"""
    sites: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in classes:
                sites.append(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    return sites


def test_single_static_selection_point() -> None:
    source = COMPOSITION.read_text(encoding="utf-8")
    assert selection_point_violations(source) == []

    # 呼び出している名前は関数ではなくクラスである(関数へのすり替えを塞ぐ)。
    module = ast.parse(source)
    (function,) = [node for node in module.body if isinstance(node, ast.FunctionDef)]
    returned = function.body[0]
    assert isinstance(returned, ast.Return) and isinstance(returned.value, ast.Call)
    assert isinstance(returned.value.func, ast.Name)
    selected = returned.value.func.id
    assert inspect.isclass(getattr(composition, selected))

    # モジュールにはこの関数以外を置かない(docstring と import を除く)。
    others = [
        node for node in module.body[1:] if not isinstance(node, ast.Import | ast.ImportFrom) and node is not function
    ]
    assert others == []

    # src/ 全体で、Llm の実装を構築する式は composition.py の1箇所だけ。
    classes = llm_classes()
    assert "ClaudeCliLlm" in classes  # 走査が空振りしていない
    assert selected in classes
    assert constructions(classes) == [f"composition.py:{returned.value.lineno}"]


def test_selection_point_check_goes_red_on_each_escape() -> None:
    """判定が赤くなり得ること: 分岐・環境変数・属性越し・関数へのすり替えの形をそれぞれ拒む。"""
    head = "def build_default_llm(meter):\n"
    rejected = {
        "if": "    if meter:\n        return A(meter)\n    return B(meter)\n",
        "ifexp": "    return (A if meter else B)(meter)\n",
        "environ": "    return A(os.environ['X'])\n",
        "attribute": "    return adapters.A(meter)\n",
        "subscript": "    return TABLE['a'](meter)\n",
        "boolop": "    return A(meter or None)\n",
        "lambda": "    return A((lambda: meter)())\n",
        "comprehension": "    return A([m for m in meter])\n",
        "import": "    import os\n    return A(meter)\n",
        "try": "    try:\n        return A(meter)\n    except Exception:\n        return B(meter)\n",
        "match": "    match meter:\n        case _:\n            return A(meter)\n",
        "nested": "    return A(B())\n",
        "free-name": "    return A(OTHER)\n",
        "two-statements": "    llm = A(meter)\n    return llm\n",
    }
    for name, body in rejected.items():
        assert selection_point_violations(head + body), name
    assert selection_point_violations(head + "    return A(meter)\n") == []


def test_construction_scan_sees_attribute_calls(tmp_path: Path) -> None:
    """構築の走査が属性越しの呼び出しも数えること(cli がアダプタを直接組み立てる形)。"""
    (tmp_path / "composition.py").write_text("def build_default_llm(meter):\n    return ClaudeCliLlm(meter)\n")
    (tmp_path / "cli.py").write_text("llm = llm_claude_cli.ClaudeCliLlm(meter)\n")
    assert constructions(llm_classes(), tmp_path) == ["cli.py:1", "composition.py:2"]
