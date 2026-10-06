"""既定構成の実行が SDK(`anthropic`)を読み込まない(NFR-8 (a)、C-020 A-1 / A-2)。

`sys.modules` はプロセス全域なので、判定は新規インタプリタ(サブプロセス)で行う(AR-50)。同じプロセスの
ほかのテストが何を読み込んだかに判定を左右させない。

`anthropic` の import 可能性は `sys.path` の先頭に置いたスタブパッケージで満たす(AR-49)。判定に要るのは
「読み込まれたか」だけ。
陰性対照は、anthropic を import する合成のアダプタを既定の選択点へ差し込む(C-027)。`claude` は PATH に置いた fake の
実行ファイル、最小ホームはテストが作った tmp の `HOME` の下に T-029 の実測形で置く。
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from support.claude_fake import EXECUTABLE_DIR

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
DECK = "synthetic"

METERED_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_REGION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "CLOUD_ML_REGION",
)
"""NFR-8 が列挙した 13 個の metered 認証変数(要件から書き写す。製品の定数は読まない)。"""

STUB_SDK = '''"""テスト用の anthropic スタブ。判定に要るのは「読み込まれたか」だけ。"""


class Anthropic:
    pass
'''

SDK_ADAPTER_MODULE = "synthetic_sdk_adapter"

SDK_ADAPTER = '''"""anthropic を import する合成の LLM アダプタ(陰性対照)。呼び出しは `ClaudeCliLlm` に任せる。"""

import anthropic

from quiz_sift.adapters.llm_claude_cli import ClaudeCliLlm


class SdkImportingLlm(ClaudeCliLlm):
    def __init__(self, meter):
        super().__init__(meter)
        self.sdk_client = anthropic.Anthropic()
'''
"""消えた SDK アダプタに代わる陰性対照。製品の外(テストが作る tmp)に置き、既定の選択点へ差し込む。"""

DRIVER = '''"""新規インタプリタで既定構成のクラスタリング段を1回走らせ、判定に要る事実を JSON で書く。"""

import importlib.util
import json
import sys
import types
from pathlib import Path

variant, root, stub, report = sys.argv[1:]
spec = importlib.util.find_spec("anthropic")
importable = spec is not None and spec.origin == stub
loaded_at_start = "anthropic" in sys.modules

import quiz_sift
from quiz_sift import composition

if variant == "sdk":
    # 選択点を anthropic を import する合成のアダプタへ差し替えた版(negative control)。
    # composition.py の本文を書き換えて読み込む。
    source = Path(composition.__file__).read_text(encoding="utf-8")
    for old, new in (
        (
            "from quiz_sift.adapters.llm_claude_cli import ClaudeCliLlm",
            "from synthetic_sdk_adapter import SdkImportingLlm",
        ),
        ("return ClaudeCliLlm(meter)", "return SdkImportingLlm(meter)"),
    ):
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    swapped = types.ModuleType(composition.__name__)
    swapped.__file__ = composition.__file__
    exec(compile(source, composition.__file__, "exec"), swapped.__dict__)
    sys.modules[composition.__name__] = swapped
    quiz_sift.composition = swapped
    composition = swapped

selected = composition.build_default_llm
built = []


def recording(meter):
    llm = selected(meter)
    built.append(type(llm).__name__)
    return llm


composition.build_default_llm = recording

from quiz_sift import cli

try:
    code = cli.main(["--root", root, "cluster", "synthetic"])
except SystemExit as exc:
    code = exc.code
Path(report).write_text(
    json.dumps(
        {
            "importable": importable,
            "loaded_at_start": loaded_at_start,
            "anthropic_loaded": "anthropic" in sys.modules,
            "built": built,
            "exit_code": code,
        }
    ),
    encoding="utf-8",
)
'''


def make_measured_home(home: Path) -> None:
    """T-029 の実測形の最小ホーム(資格情報ファイルを含む)。"""
    claude = home / ".claude"
    (claude / "skills" / "synced" / "example").mkdir(parents=True)
    (claude / "plugins" / "synced" / "builtin").mkdir(parents=True)
    (claude / "projects" / "-tmp-quiz-sift-cwd-x").mkdir(parents=True)
    (home / ".claude.json").write_text("{}")
    (claude / "settings.json").write_text(json.dumps({"theme": "dark"}))
    (claude / "skills" / "synced" / "example" / "SKILL.md").write_text("# example\n")
    (claude / "plugins" / "synced" / "builtin" / "plugin.json").write_text("{}")
    (claude / "projects" / "-tmp-quiz-sift-cwd-x" / "session.jsonl").write_text("{}\n")
    (claude / ".credentials.json").write_text('{"token": "dummy"}')


def write_deck(root: Path) -> None:
    """合成デッキ。要約の語句(fake とスタブが返す「合成の応答」)が問題文に実在する。"""
    root.mkdir()
    (root / ".gitignore").write_text("data/\nout/\n", encoding="utf-8")
    deck_dir = root / "data" / DECK
    deck_dir.mkdir(parents=True)
    (deck_dir / "deck.yaml").write_text(f"deck_id: {DECK}\ntitle: 合成デッキ\nlanguage: ja\n", encoding="utf-8")
    rows = [
        {
            "id": f"q{n}",
            "stem": f"合成の応答を問う合成の問題 {n}",
            "options": [{"id": label, "text": f"合成の選択肢 {label}{n}"} for label in "ABCD"],
            "answers": ["A"],
        }
        for n in range(3)
    ]
    (deck_dir / "questions.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def run_default(tmp_path: Path, variant: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """`variant`("default" か "sdk")で新規インタプリタを走らせ、報告と meter の行(開始行・終了行)を返す。"""
    stub_dir = tmp_path / "stub"
    (stub_dir / "anthropic").mkdir(parents=True)
    stub = stub_dir / "anthropic" / "__init__.py"
    stub.write_text(STUB_SDK, encoding="utf-8")
    (stub_dir / f"{SDK_ADAPTER_MODULE}.py").write_text(SDK_ADAPTER, encoding="utf-8")
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    home = tmp_path / "home"
    make_measured_home(home / ".quiz-sift-claude-home")
    root = tmp_path / "root"
    write_deck(root)
    report = tmp_path / "report.json"

    env = {
        **os.environ,
        **{name: f"synthetic-{name.lower()}" for name in METERED_ENV},
        "HOME": str(home),
        "PATH": f"{EXECUTABLE_DIR}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": f"{stub_dir}{os.pathsep}{SRC}",
    }
    completed = subprocess.run(
        [sys.executable, str(driver), variant, str(root), str(stub), str(report)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    log = root / "out" / DECK / "meter.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return json.loads(report.read_text(encoding="utf-8")), rows


def test_default_run_never_loads_the_sdk(tmp_path: Path) -> None:
    report, (start, end) = run_default(tmp_path, "default")
    # 前提: anthropic は import できる(できなければ skip せず、ここで落ちる)。
    assert report["importable"] is True
    assert report["loaded_at_start"] is False
    # positive control: 既定の選択点を通り、LLM が実際に呼ばれた。
    assert report["built"] == ["ClaudeCliLlm"]
    assert report["exit_code"] == 0
    assert (start["record"], end["record"]) == ("start", "end")
    assert end["llm_calls"] >= 1
    assert start["metered_env_names"] == sorted(METERED_ENV)  # 親環境に 13 個が入っていた
    # 判定: 実行の後も anthropic は sys.modules に無い。
    assert report["anthropic_loaded"] is False


def test_swapping_in_an_sdk_importing_adapter_goes_red(tmp_path: Path) -> None:
    report, (start, end) = run_default(tmp_path, "sdk")
    assert report["importable"] is True
    assert report["loaded_at_start"] is False
    # 差し替え版でもパイプラインは合成のアダプタを実際に使った(差し替えに失敗して赤になったのではない)。
    assert report["built"] == ["SdkImportingLlm"]
    assert report["exit_code"] == 0
    assert (start["record"], end["record"]) == ("start", "end")
    assert end["llm_calls"] >= 1
    # 同じ判定が赤になる。
    assert report["anthropic_loaded"] is True


def test_c027_anthropic_adapter_is_gone() -> None:
    """SDK アダプタのファイルが無く、src と tests のどこからも参照されない(C-027)。名前はここで組み立てる。"""
    module, cls = "llm_" + "anthropic", "Anthropic" + "Llm"
    assert not (SRC / "quiz_sift" / "adapters" / f"{module}.py").exists()

    def hits(name: str) -> list[str]:
        return [
            path.relative_to(REPO).as_posix()
            for root in (SRC, REPO / "tests")
            for path in sorted(root.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and name.encode() in path.read_bytes()
        ]

    assert hits(module) == [] and hits(cls) == []
    # 走査が空振りしていない: 既定のアダプタの名前は src と tests の両方で見つかる。
    found = hits("llm_" + "claude_cli")
    assert any(p.startswith("src/") for p in found) and any(p.startswith("tests/") for p in found)
