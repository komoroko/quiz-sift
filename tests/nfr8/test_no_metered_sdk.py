"""既定構成の実行が SDK(`anthropic`)を読み込まない(NFR-8 (a)、C-020 A-1 / A-2)。

`sys.modules` はプロセス全域なので、判定は新規インタプリタ(サブプロセス)で行う(AR-50)。同じプロセスには
テストコードから SDK アダプタを直接構築するテストがあり、同居させると順序次第で赤になる。

`anthropic` の import 可能性は `sys.path` の先頭に置いたスタブパッケージで満たす(AR-49)。判定に要るのは
「読み込まれたか」だけで、スタブは `messages.create` に形の合う応答を返す。`claude` は PATH に置いた fake の
実行ファイル、最小ホームはテストが作った tmp の `HOME` の下に T-029 の実測形で置く。
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from support.claude_fake import EXECUTABLE_DIR

SRC = Path(__file__).resolve().parents[2] / "src"
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

STUB_SDK = '''"""テスト用の anthropic スタブ。`messages.create` はスキーマに形の合う応答を返す。"""

import json
from types import SimpleNamespace

types = SimpleNamespace(Message=object)


class APIError(Exception):
    pass


def _filled(schema, payload):
    if "groups" in schema.get("properties", {}):
        keys = [question["key"] for question in payload["questions"]]
        return {"groups": [{"label": "合成グループ", "question_ids": keys}], "unassigned": []}
    if schema.get("type") == "object":
        return {key: _filled(value, payload) for key, value in schema["properties"].items()}
    if schema.get("type") == "array":
        return [_filled(schema["items"], payload)]
    return schema["enum"][0] if "enum" in schema else "合成の応答"


class _Messages:
    def create(self, **request):
        payload = json.loads(request["messages"][0]["content"])
        output = _filled(request["output_config"]["format"]["schema"], payload)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(output, ensure_ascii=False))],
            usage=SimpleNamespace(
                input_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=1
            ),
        )


class Anthropic:
    def __init__(self):
        self.messages = _Messages()
'''

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
    # 選択点を SDK アダプタへ差し替えた版(negative control)。composition.py の本文を書き換えて読み込む。
    source = Path(composition.__file__).read_text(encoding="utf-8")
    for old, new in (
        (
            "from quiz_sift.adapters.llm_claude_cli import ClaudeCliLlm",
            "from quiz_sift.adapters.llm_anthropic import AnthropicLlm",
        ),
        ("return ClaudeCliLlm(meter)", "return AnthropicLlm(meter)"),
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
            "answer": "A",
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


def test_swapping_in_the_sdk_adapter_goes_red(tmp_path: Path) -> None:
    report, (start, end) = run_default(tmp_path, "sdk")
    assert report["importable"] is True
    assert report["loaded_at_start"] is False
    # 差し替え版でもパイプラインは SDK アダプタを実際に使った(import だけで赤になったのではない)。
    assert report["built"] == ["AnthropicLlm"]
    assert report["exit_code"] == 0
    assert (start["record"], end["record"]) == ("start", "end")
    assert end["llm_calls"] >= 1
    # 同じ判定が赤になる。
    assert report["anthropic_loaded"] is True
