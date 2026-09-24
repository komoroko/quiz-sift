"""実 `claude` CLI の出力契約フィクスチャを採取する(T-029 / C-020 NFR-8 (e) / C-018 NFR-6)。

**ホストで**人が1回走らせる。サンドボックスには `claude` が無く、ゲートはここで書いたファイルを読むだけ:

    python tests/measurements/collect_claude_cli.py --repo <リポジトリ>

前提(ADR-014): 人が最小ホームを用意済みであること(`HOME=~/.quiz-sift-claude-home claude /login`)。

- argv は設計 NFR-8 節の (e) をそのまま組む(`ClaudeCliLlm` はまだ無い。T-027 がこの meta の argv と突き合わせる)。
- 子プロセスへ渡す環境は `HOME` / `PATH` / `LANG` / `TZ` の4つだけで、`HOME` は最小ホーム。作業ディレクトリは
  呼び出しごとに `mkdtemp` で作る空ディレクトリ。
- 親環境に metered 認証変数(docs/10-requirements.md が列挙する13個)が1つでもあれば採取しない。記録するのは
  見つかった変数名の一覧だけで、値は書かない。
- 送るのは合成プロンプトだけ(NFR-2 に触れない)。2件:
  `success`(小さなスキーマ)/ `invalid_schema`(JSON でない `--json-schema`。モデルを呼ぶ前に終わる)。
  `error_max_turns` は採らない(D-118 / ADR-016。2.1.282 では `--max-turns 1` でも success で完了する)。
- 最小ホームのファイル一覧(相対パスだけ。中身は書かない)を採取の前後で記録する。

出力: `tests/fixtures/claude_cli/<name>.stdout`(生の標準出力)と `<name>.meta.yaml`、
`docs/test/measurements/claude-cli-contract.md`。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

MINIMAL_HOME = Path.home() / ".quiz-sift-claude-home"

# docs/10-requirements.md NFR-8 の「metered 認証変数の集合」(AR-48)。
METERED_VARS = (
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

MODEL = "claude-haiku-4-5"

# `--settings` の `permissions.deny` に並べるツール名。2026-09-25 に 2.1.282 の素の `init.tools` を
# (存在しないモデル名で、枠を消費せずに)読んだ24本に、他フラグの有無で現れる `Glob` / `Grep` などを足した。
# 列挙が正しいかは列挙の側では保証しない。判定は採取した `init.tools` が {StructuredOutput} と一致すること。
DENIED_TOOLS = (
    "Agent",
    "AskUserQuestion",
    "Bash",
    "BashOutput",
    "CronCreate",
    "CronDelete",
    "CronList",
    "DesignSync",
    "Edit",
    "EnterPlanMode",
    "EnterWorktree",
    "ExitPlanMode",
    "ExitWorktree",
    "Glob",
    "Grep",
    "KillShell",
    "LS",
    "LSP",
    "ListAgents",
    "Monitor",
    "MultiEdit",
    "NotebookEdit",
    "NotebookRead",
    "PushNotification",
    "Read",
    "RemoteTrigger",
    "ReportFindings",
    "ScheduleWakeup",
    "SendMessage",
    "Skill",
    "Task",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "TodoWrite",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
    "Write",
)

SYSTEM_PROMPT = "You are a calculator. Return only the requested structured answer."

PROMPT = "SYNTHETIC CONTRACT PROBE (quiz-sift T-029). No study material is included. What is 2 + 3?\n"

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}

# JSON として読めない `--json-schema`。2.1.282 ではモデルを呼ぶ前に `Error: ...` を**標準エラー**へ書き、
# 標準出力は空で終了コード 1(2026-09-25 実測、D-119)。
INVALID_SCHEMA = '{"type":'


def design_argv(json_schema: str) -> list[str]:
    """設計 NFR-8 節 (e) の argv。"""
    return [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "3",
        "--json-schema",
        json_schema,
        "--model",
        MODEL,
        "--system-prompt",
        SYSTEM_PROMPT,
        "--exclude-dynamic-system-prompt-sections",
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps({"mcpServers": {}}, separators=(",", ":")),
        "--settings",
        json.dumps({"permissions": {"deny": list(DENIED_TOOLS)}}, separators=(",", ":")),
        "--permission-mode",
        "default",
    ]


CASES = {
    "success": design_argv(json.dumps(SCHEMA, separators=(",", ":"))),
    "invalid_schema": design_argv(INVALID_SCHEMA),
}


def child_env() -> dict[str, str]:
    return {
        "HOME": str(MINIMAL_HOME),
        "PATH": os.environ["PATH"],
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TZ": os.environ.get("TZ") or "Asia/Tokyo",
    }


def home_files() -> list[str]:
    """最小ホーム配下のファイルの相対パス(中身は読まない)。"""
    return sorted(p.relative_to(MINIMAL_HOME).as_posix() for p in MINIMAL_HOME.rglob("*") if p.is_file())


def claude_version(env: dict[str, str]) -> str:
    with tempfile.TemporaryDirectory() as cwd:
        return subprocess.run(
            ["claude", "--version"], env=env, cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()


def collect(name: str, argv: list[str], env: dict[str, str], version: str, fixtures: Path) -> dict[str, object]:
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(argv, input=PROMPT.encode(), env=env, cwd=cwd, capture_output=True, timeout=300)
    (fixtures / f"{name}.stdout").write_bytes(proc.stdout)
    meta: dict[str, object] = {
        "collected_at": collected_at,
        "claude_version": version,
        "model": MODEL,
        "argv": argv,
        "env_keys": sorted(env),
        "home": env["HOME"],
        "prompt": PROMPT,
        "exit_code": proc.returncode,
        "stderr": proc.stderr.decode(errors="replace"),
        "stdout_sha256": hashlib.sha256(proc.stdout).hexdigest(),
    }
    (fixtures / f"{name}.meta.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False))
    return meta


def init_event(stdout: bytes) -> dict[str, object]:
    for line in stdout.decode().splitlines():
        event = json.loads(line)
        if event.get("type") == "system" and event.get("subtype") == "init":
            return dict(event)
    raise SystemExit("success の標準出力に init イベントが無い")


def render_contract(record: dict[str, object], metas: dict[str, dict[str, object]]) -> str:
    rows = "\n".join(
        f"| `{name}` | {meta['collected_at']} | {meta['exit_code']} | `sha256:{meta['stdout_sha256']}` |"
        for name, meta in metas.items()
    )
    return f"""# 実測ログ: claude CLI の出力契約と init.tools

NFR-8 (e)1(C-020)の「有効ツールが実際に許容集合に収まる」ことのホスト実測と、NFR-6(C-018)の契約
フィクスチャ(AR-43 / AR-63)の採取記録。ゲートは argv の形しか見えないので、ここだけが実 CLI の
`init.tools` を見る。`tests/measurements/collect_claude_cli.py` が書き、
`tests/measurements/test_claude_cli_logs.py` がフィクスチャ本体と突き合わせて機械判定する。

採取は最小ホーム(ADR-014)を `HOME` に向け、子プロセスへ渡す環境は `HOME` / `PATH` / `LANG` / `TZ` の
4つだけ。親環境に metered 認証変数(docs/10-requirements.md NFR-8 の13個)があれば採取しない。
記録するのは見つかった変数名の一覧だけで、値は書かない。送ったのは合成プロンプトだけで、
最小ホームのファイル一覧は相対パスだけを載せる(中身は載せない)。

## フィクスチャ

| 名前 | 採取日時 | 終了コード | 標準出力 |
|---|---|---|---|
{rows}

argv・合成プロンプト全文・`claude --version` の出力は `tests/fixtures/claude_cli/<名前>.meta.yaml` にある。

## 記録

```yaml
{yaml.safe_dump(record, allow_unicode=True, sort_keys=False).rstrip()}
```
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()

    metered = sorted(v for v in METERED_VARS if v in os.environ)
    if metered:
        print(f"親環境に metered 認証変数がある: {metered}", file=sys.stderr)
        return 2
    if not (MINIMAL_HOME / ".claude" / ".credentials.json").is_file():
        print(f"最小ホームが用意されていない: HOME={MINIMAL_HOME} claude /login", file=sys.stderr)
        return 2

    fixtures = args.repo / "tests" / "fixtures" / "claude_cli"
    fixtures.mkdir(parents=True, exist_ok=True)
    env = child_env()
    before = home_files()
    version = claude_version(env)
    metas = {name: collect(name, argv, env, version, fixtures) for name, argv in CASES.items()}
    after = home_files()

    init = init_event((fixtures / "success.stdout").read_bytes())
    tools = init["tools"]
    if not isinstance(tools, list):
        raise SystemExit("init イベントの tools が配列でない")
    record: dict[str, object] = {
        "claude_version": version,
        "home": str(MINIMAL_HOME),
        "env_keys": sorted(env),
        "metered_vars_checked": len(METERED_VARS),
        "metered_vars_in_parent_env": metered,
        "init_tools": [str(t) for t in tools],
        "api_key_source": init["apiKeySource"],
        "home_files_before": before,
        "home_files_after": after,
    }
    contract = args.repo / "docs" / "test" / "measurements" / "claude-cli-contract.md"
    contract.write_text(render_contract(record, metas))
    print(yaml.safe_dump({"init_tools": record["init_tools"], "api_key_source": record["api_key_source"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
