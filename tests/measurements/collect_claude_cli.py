"""実 `claude` CLI の出力契約フィクスチャと番兵実測を採取する(T-041 / C-020 NFR-8 (e) / C-018 NFR-6)。

**ホストで**人が走らせる。サンドボックスには `claude` が無く、ゲートはここで書いたファイルを読むだけ:

    PYTHONPATH=src uv run python tests/measurements/collect_claude_cli.py --repo <リポジトリ> contract
    PYTHONPATH=src uv run python tests/measurements/collect_claude_cli.py --repo <リポジトリ> home-probe
    PYTHONPATH=src uv run python tests/measurements/collect_claude_cli.py --repo <リポジトリ> flags
    PYTHONPATH=src uv run python tests/measurements/collect_claude_cli.py --repo <リポジトリ> hardening

前提(ADR-014 / ADR-017): 人が最小ホームを用意済みであること(`HOME=~/.quiz-sift-claude-home claude /login`)。

- argv は独自に持たず、製品の `build_argv`(`ClaudeCliLlm` と同じ関数)で組む(設計 NFR-8 第2段)。本文の
  フラグ(`--system-prompt` / `--json-schema`)の値だけが合成のもの。
- 子プロセスへ渡す環境は `HOME` / `PATH` / `LANG` / `TZ` の4つだけで、`HOME` は最小ホーム。作業ディレクトリは
  呼び出しごとに作る空ディレクトリ。
- 親環境に metered 認証変数(docs/10-requirements.md が列挙する13個)が1つでもあれば採取しない。記録するのは
  見つかった変数名の一覧だけで、値は書かない。
- 送るのは合成プロンプトだけ(NFR-2 に触れない)。

`contract`: 3件を採る。`success`(haiku)/ `success_opus`(opus。D-152)/ `invalid_schema`(JSON でない
`--json-schema`。モデルを呼ぶ前に終わる)。success に `rate_limit_event` が無ければ3回まで採り直す(D-133)。
`error_max_turns` は採らない(D-118 / ADR-016)。最小ホームのファイル一覧(相対パスだけ)を採取の前後で記録する。
出力は `tests/fixtures/claude_cli/<name>.stdout`(生の標準出力)と `<name>.meta.yaml`、
`docs/test/measurements/claude-cli-contract.md`。

`home-probe`: 4つの場所に実行ごとの乱数の合言葉を置き、`--safe-mode` あり・なしの argv で1回ずつ呼んで、
見えた合言葉をスキーマで返させる(NFR-8 (e)5)。positive control は、最終 argv からユーザ設定の遮断と
`--safe-mode` を外した argv(T-060 A-7)。後始末を確かめ、
`docs/test/measurements/claude-cli-home-probe.md` の「## 記録」を書き直す。作業ディレクトリの親は
このスクリプトが作る専用のディレクトリで、`/tmp` 直下には置かない(D-151)。

`flags`: NFR-8 のフラグの実測の第1段(T-054 / C-023・C-024)。`claude --version` と `--help` を採り、`--help` に
現れるフラグのうちユーザ設定の読み込み元とツールの有効化に関わるものを列挙する。有効ツールの許可リストの
候補フラグごとに、空の許可リストと `StructuredOutput` を明示したものの2通りで、拒否リストの無い argv(製品の
argv からユーザ設定の遮断と許可リストを外したもの)に候補を足して合成プロンプトを1回ずつ送り、`init.tools` と
構造化出力の有無を記録する。
権限の自動承認のフラグ(`--allowedTools` など)は許可リストの候補に数えない。
出力は `docs/test/measurements/claude-cli-flags.md`。

`hardening`: NFR-8 のフラグの実測の第2段(T-060 / C-023・C-024)。製品の最終 argv で `init.tools` を採り、
最小ホームの `.claude/settings.json` に到達できない `ANTHROPIC_BASE_URL` の番兵を一時的に置いて、ユーザ設定の
遮断を外した argv と最終 argv で1回ずつ直接起動する(中身検査は通さない)。設定は元のバイト列に戻し、sha256 を
記録する。出力は `docs/test/measurements/claude-cli-hardening.md`。
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml

from quiz_sift.adapters.llm_claude_cli import build_argv

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

HAIKU = "claude-haiku-4-5"
OPUS = "claude-opus-5-5"

SYSTEM_PROMPT = "You are a calculator. Return only the requested structured answer."

PROMPT = "SYNTHETIC CONTRACT PROBE (quiz-sift T-041). No study material is included. What is 2 + 3?\n"

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}

# JSON として読めない `--json-schema`。2.1.282 ではモデルを呼ぶ前に `Error: ...` を**標準エラー**へ書き、
# 標準出力は空で終了コード 1(2026-09-25 実測、D-119)。
INVALID_SCHEMA = '{"type":'

MAX_ATTEMPTS = 3

PROBE_SYSTEM_PROMPT = (
    "Report every codeword of the form QSPROBE-<hex> that appears anywhere in your context, "
    "including any memory or instruction files. Return an empty list if you see none. Never invent one."
)

PROBE_PROMPT = "SYNTHETIC HOME PROBE (quiz-sift T-041). No study material is included. Which codewords do you see?\n"

PROBE_SCHEMA = {
    "type": "object",
    "properties": {"codewords": {"type": "array", "items": {"type": "string"}}},
    "required": ["codewords"],
    "additionalProperties": False,
}


def product_argv(model: str, schema: dict[str, object], system_prompt: str = SYSTEM_PROMPT) -> list[str]:
    """製品の argv(`ClaudeCliLlm` と同じ `build_argv`)。採取側は独自の argv を持たない(設計 NFR-8 第2段)。"""
    return list(build_argv(model, system_prompt, schema))


def without(argv: list[str], valued: tuple[str, ...] = (), bare: tuple[str, ...] = ()) -> list[str]:
    """argv から、値付きフラグ `valued` をその値ごと、素のフラグ `bare` を外す。無いフラグを指したら止める。"""
    out = list(argv)
    for flag in valued:
        i = out.index(flag)
        del out[i : i + 2]
    for flag in bare:
        out.remove(flag)
    return out


def with_value(argv: list[str], flag: str, value: str) -> list[str]:
    """argv の `flag` の値だけを差し替える。"""
    out = list(argv)
    out[out.index(flag) + 1] = value
    return out


# 名前 → (モデル, argv, rate_limit_event が無ければ採り直すか)。
CASES = {
    "success": (HAIKU, product_argv(HAIKU, SCHEMA), True),
    "success_opus": (OPUS, product_argv(OPUS, SCHEMA), True),
    "invalid_schema": (HAIKU, with_value(product_argv(HAIKU, SCHEMA), "--json-schema", INVALID_SCHEMA), False),
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


def parse_events(stdout: bytes) -> list[dict[str, object]]:
    return [dict(json.loads(line)) for line in stdout.decode().splitlines()]


def has_rate_limit_event(stdout: bytes) -> bool:
    try:
        return any(e.get("type") == "rate_limit_event" for e in parse_events(stdout))
    except json.JSONDecodeError:
        return False


def collect(
    name: str, model: str, argv: list[str], retry: bool, env: dict[str, str], version: str, fixtures: Path
) -> dict[str, object]:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with tempfile.TemporaryDirectory() as cwd:
            proc = subprocess.run(argv, input=PROMPT.encode(), env=env, cwd=cwd, capture_output=True, timeout=300)
        if not retry or has_rate_limit_event(proc.stdout):
            break
        print(f"{name}: 試行 {attempt} に rate_limit_event が無い", file=sys.stderr)
    (fixtures / f"{name}.stdout").write_bytes(proc.stdout)
    meta: dict[str, object] = {
        "collected_at": collected_at,
        "claude_version": version,
        "model": model,
        "argv": argv,
        "env_keys": sorted(env),
        "home": env["HOME"],
        "prompt": PROMPT,
        "exit_code": proc.returncode,
        "stderr": proc.stderr.decode(errors="replace"),
        "stdout_sha256": hashlib.sha256(proc.stdout).hexdigest(),
        "attempts": attempt,
    }
    (fixtures / f"{name}.meta.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False))
    return meta


def init_event(events: list[dict[str, object]]) -> dict[str, object]:
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event
    raise SystemExit("標準出力に init イベントが無い")


def render_contract(record: dict[str, object], metas: dict[str, dict[str, object]]) -> str:
    rows = "\n".join(
        f"| `{name}` | `{meta['model']}` | {meta['collected_at']} | {meta['attempts']} | {meta['exit_code']} "
        f"| `sha256:{meta['stdout_sha256']}` |"
        for name, meta in metas.items()
    )
    return f"""# 実測ログ: claude CLI の出力契約と init.tools

NFR-8 (e)1(C-020)の「有効ツールが実際に許容集合に収まる」ことのホスト実測と、NFR-6(C-018)の契約
フィクスチャ(AR-43 / AR-63)の採取記録。ゲートは argv の形しか見えないので、ここだけが実 CLI の
`init.tools` を見る。argv は `--safe-mode` を含む設計 (e) の形(D-130 / D-133)。
`tests/measurements/collect_claude_cli.py contract` が書き、
`tests/measurements/test_claude_cli_logs.py` がフィクスチャ本体と突き合わせて機械判定する。

採取は最小ホーム(ADR-014)を `HOME` に向け、子プロセスへ渡す環境は `HOME` / `PATH` / `LANG` / `TZ` の
4つだけ。親環境に metered 認証変数(docs/10-requirements.md NFR-8 の13個)があれば採取しない。
記録するのは見つかった変数名の一覧だけで、値は書かない。送ったのは合成プロンプトだけで、
最小ホームのファイル一覧は相対パスだけを載せる(中身は載せない)。success に `rate_limit_event` が
無ければ3回まで採り直す。試行回数は表の「試行」列にある。

## フィクスチャ

| 名前 | モデル | 採取日時 | 試行 | 終了コード | 標準出力 |
|---|---|---|---|---|---|
{rows}

argv・合成プロンプト全文・`claude --version` の出力は `tests/fixtures/claude_cli/<名前>.meta.yaml` にある。

## 記録

```yaml
{yaml.safe_dump(record, allow_unicode=True, sort_keys=False).rstrip()}
```
"""


def contract(repo: Path, metered: list[str]) -> int:
    fixtures = repo / "tests" / "fixtures" / "claude_cli"
    fixtures.mkdir(parents=True, exist_ok=True)
    env = child_env()
    before = home_files()
    version = claude_version(env)
    metas = {
        name: collect(name, model, argv, retry, env, version, fixtures) for name, (model, argv, retry) in CASES.items()
    }
    after = home_files()

    observed: dict[str, object] = {}
    for name in ("success", "success_opus"):
        events = parse_events((fixtures / f"{name}.stdout").read_bytes())
        init = init_event(events)
        rate_limits = [e["rate_limit_info"] for e in events if e.get("type") == "rate_limit_event"]
        observed[name] = {
            "init_tools": init["tools"],
            "api_key_source": init["apiKeySource"],
            # `--safe-mode` の下では init にキー自体が無い(2026-09-26 実測)。null として読み、キーの有無も残す。
            "memory_paths": init.get("memory_paths"),
            "memory_paths_key_present": "memory_paths" in init,
            "rate_limit_events": [
                {"status": info["status"], "isUsingOverage": info["isUsingOverage"]}  # type: ignore[index]
                for info in rate_limits
            ],
        }
    record: dict[str, object] = {
        "claude_version": version,
        "home": str(MINIMAL_HOME),
        "env_keys": sorted(env),
        "metered_vars_checked": len(METERED_VARS),
        "metered_vars_in_parent_env": metered,
        "fixtures": observed,
        "home_files_before": before,
        "home_files_after": after,
    }
    path = repo / "docs" / "test" / "measurements" / "claude-cli-contract.md"
    path.write_text(render_contract(record, metas))
    print(yaml.safe_dump(observed, allow_unicode=True, sort_keys=False))
    return 0


# 番兵を置く4つの場所(記録上の名前 → 実パスの組み立て)。`parent` は作業ディレクトリの親。
PROBE_LOCATIONS: dict[str, Callable[[Path], Path]] = {
    "$HOME/.claude/CLAUDE.md": lambda parent: MINIMAL_HOME / ".claude" / "CLAUDE.md",
    "$HOME/.claude/rules/probe.md": lambda parent: MINIMAL_HOME / ".claude" / "rules" / "probe.md",
    "<parent>/CLAUDE.md": lambda parent: parent / "CLAUDE.md",
    "<parent>/.claude/CLAUDE.md": lambda parent: parent / ".claude" / "CLAUDE.md",
}


def probe_run(argv: list[str], env: dict[str, str], cwd: Path) -> dict[str, object]:
    cwd.mkdir()
    proc = subprocess.run(argv, input=PROBE_PROMPT.encode(), env=env, cwd=cwd, capture_output=True, timeout=300)
    events = parse_events(proc.stdout)
    results = [e for e in events if e.get("type") == "result"]
    if len(results) != 1 or proc.returncode != 0:
        raise SystemExit(f"番兵の呼び出しが失敗した: exit={proc.returncode} stderr={proc.stderr.decode()!r}")
    structured = results[0]["structured_output"]
    return {
        "argv": argv,
        "exit_code": proc.returncode,
        "memory_paths": init_event(events).get("memory_paths"),
        "returned": list(structured["codewords"]),  # type: ignore[index,call-overload]
        "stdout_sha256": hashlib.sha256(proc.stdout).hexdigest(),
    }


def home_probe(repo: Path, metered: list[str]) -> int:
    home_claude_md = MINIMAL_HOME / ".claude" / "CLAUDE.md"
    home_rules = MINIMAL_HOME / ".claude" / "rules"
    if home_claude_md.exists() or home_rules.exists():
        # 起動前検査(NFR-8 (b))が拒む状態。置き換えて戻す経路は持たない。
        print(f"最小ホームに {home_claude_md} か {home_rules} が既にある", file=sys.stderr)
        return 2

    env = child_env()
    version = claude_version(env)
    base = Path(tempfile.mkdtemp(prefix="quiz-sift-home-probe-"))
    parent = base / "parent"
    parent.mkdir()
    codewords = {loc: f"QSPROBE-{secrets.token_hex(4).upper()}" for loc in PROBE_LOCATIONS}
    paths = {loc: build(parent) for loc, build in PROBE_LOCATIONS.items()}
    safe_argv = product_argv(HAIKU, PROBE_SCHEMA, PROBE_SYSTEM_PROMPT)
    # positive control: 最終 argv からユーザ設定の遮断と `--safe-mode` を外す(T-060 A-7)。
    plain_argv = without(safe_argv, valued=(SETTINGS_BLOCK_FLAG,), bare=("--safe-mode",))
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        for loc, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"The codeword is {codewords[loc]}.\n")
        runs = {
            "safe_mode": probe_run(safe_argv, env, parent / "work-safe"),
            "without_safe_mode": probe_run(plain_argv, env, parent / "work-plain"),
        }
    finally:
        home_claude_md.unlink(missing_ok=True)
        (home_rules / "probe.md").unlink(missing_ok=True)
        if home_rules.exists():
            home_rules.rmdir()
        shutil.rmtree(base)

    record: dict[str, object] = {
        "collector": "tests/measurements/collect_claude_cli.py",
        "collected_at": collected_at,
        "claude_version": version,
        "model": HAIKU,
        "home": str(MINIMAL_HOME),
        "env_keys": sorted(env),
        "metered_vars_checked": len(METERED_VARS),
        "metered_vars_in_parent_env": metered,
        "parent_dir": str(parent),
        "parent_created_by_collector": True,
        "prompt": PROBE_PROMPT,
        "sentinels": [{"location": loc, "codeword": codewords[loc]} for loc in PROBE_LOCATIONS],
        "runs": runs,
        "cleanup": {
            "sentinels_remaining": [loc for loc, path in paths.items() if path.exists()],
            "home_claude_md_exists": home_claude_md.exists(),
            "home_rules_exists": home_rules.exists(),
            "parent_dir_exists": parent.exists(),
            "replaced_files": [],
        },
    }
    path = repo / "docs" / "test" / "measurements" / "claude-cli-home-probe.md"
    text = path.read_text(encoding="utf-8")
    kept = re.split(r"\n## 記録\n", text, maxsplit=1)[0].rstrip("\n")
    path.write_text(
        f"{kept}\n\n## 記録\n\n```yaml\n{yaml.safe_dump(record, allow_unicode=True, sort_keys=False).rstrip()}\n```\n"
    )
    print(yaml.safe_dump({k: {"returned": v["returned"]} for k, v in runs.items()}, allow_unicode=True))
    return 0


# `--help` から拾う、ユーザ設定の読み込み元に関わるフラグとツールの有効化に関わるフラグ。記録するのは
# このうち `--help` に現れたものだけ。
SETTINGS_FLAGS = ("--setting-sources", "--settings", "--safe-mode", "--bare", "--restricted")
TOOL_FLAGS = (
    "--tools",
    "--allowedTools",
    "--disallowedTools",
    "--restricted",
    "--bare",
    "--strict-mcp-config",
    "--mcp-config",
    "--disable-slash-commands",
)
# ユーザ設定を読まない指定の候補(設計 NFR-8)。
SETTINGS_BLOCK_FLAG = "--setting-sources"
# 有効ツールの許可リストの候補フラグと、それぞれに渡す値(空の許可リストと StructuredOutput の明示)。
ALLOWLIST_FLAGS = ("--tools",)
ALLOWLIST_VALUES = ("", "StructuredOutput")

FLAGS_PROMPT = "SYNTHETIC FLAG PROBE (quiz-sift T-054). No study material is included. What is 2 + 3?\n"


def help_flags(help_text: str) -> set[str]:
    """`--help` の Options 節にある長いフラグ名(`--allowedTools, --allowed-tools` のような別名を含む)。"""
    return {
        name
        for names in re.findall(r"^\s+(?:-\w, )?(--[\w-]+(?:, --[\w-]+)*)", help_text, re.M)
        for name in names.split(", ")
    }


def is_json(line: str) -> bool:
    try:
        json.loads(line)
    except json.JSONDecodeError:
        return False
    return True


def direct_run(argv: list[str], prompt: str, env: dict[str, str]) -> dict[str, object]:
    """中身検査を通さず `claude` を直接起動し、`init.tools`・構造化出力・失敗の原因を拾う。"""
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(argv, input=prompt.encode(), env=env, cwd=cwd, capture_output=True, timeout=300)
    # 読めない行を「init 無し」と記録すると、パーサの失敗が「許可リストの手段が無い」という実測にすり替わる。
    bad = [line for line in proc.stdout.decode(errors="replace").splitlines() if not is_json(line)]
    if bad:
        raise SystemExit(f"標準出力に JSON でない行が {len(bad)} 行ある。先頭: {bad[0][:120]!r}")
    events = parse_events(proc.stdout)
    inits = [e for e in events if e.get("type") == "system" and e.get("subtype") == "init"]
    results = [e for e in events if e.get("type") == "result"]
    structured = results[-1].get("structured_output") if results else None
    return {
        "argv": argv,
        "exit_code": proc.returncode,
        "stderr": proc.stderr.decode(errors="replace"),
        "init_tools": inits[0]["tools"] if inits else None,
        "result_is_error": results[-1].get("is_error") if results else None,
        "result_text": results[-1].get("result") if results else None,
        "structured_output": structured,
        "structured_output_returned": isinstance(structured, dict) and not results[-1].get("is_error"),
        "stdout_sha256": hashlib.sha256(proc.stdout).hexdigest(),
    }


def allowlist_run(flag: str, value: str, env: dict[str, str]) -> dict[str, object]:
    # 第1段の argv: 製品の argv から、第1段の後に足したユーザ設定の遮断と許可リストを外し、候補を末尾に足す。
    argv = [*without(product_argv(HAIKU, SCHEMA), valued=(SETTINGS_BLOCK_FLAG, *ALLOWLIST_FLAGS)), flag, value]
    run = direct_run(argv, FLAGS_PROMPT, env)
    del run["result_text"]
    return {"flag": flag, "value": value, **run}


def flags(repo: Path, metered: list[str]) -> int:
    env = child_env()
    version = claude_version(env)
    with tempfile.TemporaryDirectory() as cwd:
        help_text = subprocess.run(
            ["claude", "--help"], env=env, cwd=cwd, capture_output=True, text=True, check=True
        ).stdout
    present = help_flags(help_text)
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    runs = [
        allowlist_run(flag, value, env) for flag in ALLOWLIST_FLAGS if flag in present for value in ALLOWLIST_VALUES
    ]
    record: dict[str, object] = {
        "collector": "tests/measurements/collect_claude_cli.py",
        "collected_at": collected_at,
        "claude_version": version,
        "model": HAIKU,
        "home": str(MINIMAL_HOME),
        "env_keys": sorted(env),
        "metered_vars_checked": len(METERED_VARS),
        "metered_vars_in_parent_env": metered,
        "prompt": FLAGS_PROMPT,
        "help_sha256": hashlib.sha256(help_text.encode()).hexdigest(),
        "settings_flags": [f for f in SETTINGS_FLAGS if f in present],
        "tool_flags": [f for f in TOOL_FLAGS if f in present],
        "allowlist_runs": runs,
    }
    chosen = next(
        (r for r in runs if r["init_tools"] == ["StructuredOutput"] and r["structured_output_returned"]), None
    )
    settings_line = (
        f"ユーザ設定の遮断: あり({SETTINGS_BLOCK_FLAG})" if SETTINGS_BLOCK_FLAG in present else "ユーザ設定の遮断: なし"
    )
    allowlist_line = (
        f"許可リスト: 一致あり({chosen['flag']} {json.dumps(chosen['value'])})" if chosen else "許可リスト: 一致なし"
    )
    path = repo / "docs" / "test" / "measurements" / "claude-cli-flags.md"
    path.write_text(render_flags(record, settings_line, allowlist_line))
    print(f"{settings_line}\n{allowlist_line}")
    return 0


def render_flags(record: dict[str, object], settings_line: str, allowlist_line: str) -> str:
    return f"""# 実測ログ: claude CLI のフラグ(第1段)

NFR-8 のフラグの実測の第1段(T-054 / C-023・C-024)。製品の argv を変える前に、ホストの `claude` の
`--help` に現れるフラグのうち、ユーザ設定の読み込み元とツールの有効化に関わるものを列挙し、有効ツールの
許可リストの候補フラグごとに、拒否リスト(`--settings` の `permissions.deny`)を外した argv での
`init.tools` と、`--json-schema` の構造化出力が返ったかを記録する。権限の自動承認のフラグ
(`--allowedTools` など)は許可リストの候補に数えない。
`tests/measurements/collect_claude_cli.py flags` が書き、`tests/measurements/test_claude_cli_flags_logs.py` が
機械判定する。

採取は最小ホーム(ADR-014)を `HOME` に向け、子プロセスへ渡す環境は `HOME` / `PATH` / `LANG` / `TZ` の
4つだけ。送ったのは合成プロンプトだけ。`--help` の本文は載せず、sha256 だけを残す。

ユーザ設定の遮断は、ここでは `--help` にフラグがあるかだけを見る。効いているかの番兵の実測は第2段
(製品の argv を変えた後)で行う。

## 記録

```yaml
{yaml.safe_dump(record, allow_unicode=True, sort_keys=False).rstrip()}
```

## 結論

{settings_line}
{allowlist_line}
"""


HARDENING_PROMPT = "SYNTHETIC HARDENING PROBE (quiz-sift T-060). No study material is included. What is 2 + 3?\n"
# 読まれると呼び出しが失敗する番兵: 到達できない API の宛先(D-193)。
SENTINEL_URL = "http://127.0.0.1:9"
# 番兵のアドレスへの接続の失敗の印。2.1.290 は stream-json の下で stderr を空にし、`result` の本文に書く
# (2026-10-06 実測)ので、stderr と `result` の本文の両方から分類する。
CONNECTION_REFUSED = re.compile(r"ECONNREFUSED|Connection refused")


def failure_cause(run: dict[str, object]) -> str | None:
    """失敗した run の原因の分類。成功した run は None。"""
    if run["exit_code"] == 0 and run["result_is_error"] is False:
        return None
    text = f"{run['stderr']}\n{run['result_text'] or ''}"
    return "connection_refused" if CONNECTION_REFUSED.search(text) else "other"


def hardening(repo: Path, metered: list[str]) -> int:
    """NFR-8 のフラグの実測の第2段(T-060 / C-023・C-024)。製品の最終 argv で init.tools と SEC-001 の番兵を測る。"""
    settings = MINIMAL_HOME / ".claude" / "settings.json"
    original = settings.read_bytes()
    loaded = json.loads(original)
    if not isinstance(loaded, dict) or "env" in loaded:
        # 元の `env` を上書きすると戻すべき値と番兵が混ざる。起動前検査(D-122)が拒む状態でもある。
        print(f"{settings} が JSON オブジェクトでないか、既に env を持つ", file=sys.stderr)
        return 2
    sentinel_bytes = (json.dumps({**loaded, "env": {"ANTHROPIC_BASE_URL": SENTINEL_URL}}, indent=2) + "\n").encode()

    env = child_env()
    version = claude_version(env)
    final_argv = product_argv(HAIKU, SCHEMA)
    unblocked_argv = without(final_argv, valued=(SETTINGS_BLOCK_FLAG,))
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    init_run = direct_run(final_argv, HARDENING_PROMPT, env)
    try:
        settings.write_bytes(sentinel_bytes)
        sentinel_runs = {
            "without_settings_block": direct_run(unblocked_argv, HARDENING_PROMPT, env),
            "final_argv": direct_run(final_argv, HARDENING_PROMPT, env),
        }
    finally:
        settings.write_bytes(original)
    restored = settings.read_bytes()
    for run in sentinel_runs.values():
        run["failure_cause"] = failure_cause(run)

    record: dict[str, object] = {
        "collector": "tests/measurements/collect_claude_cli.py",
        "collected_at": collected_at,
        "claude_version": version,
        "model": HAIKU,
        "home": str(MINIMAL_HOME),
        "env_keys": sorted(env),
        "metered_vars_checked": len(METERED_VARS),
        "metered_vars_in_parent_env": metered,
        "prompt": HARDENING_PROMPT,
        "launch": "subprocess.run(direct, no preflight)",
        "init_tools_run": init_run,
        "sentinel": {
            "path": "$HOME/.claude/settings.json",
            "env": {"ANTHROPIC_BASE_URL": SENTINEL_URL},
            "sha256_original": hashlib.sha256(original).hexdigest(),
            "sha256_with_sentinel": hashlib.sha256(sentinel_bytes).hexdigest(),
            "sha256_after_restore": hashlib.sha256(restored).hexdigest(),
            "restored_content": json.loads(restored),
            "credentials_copied": False,
        },
        "sentinel_runs": sentinel_runs,
    }
    tools_line = (
        "init.tools: 一致({StructuredOutput})" if init_run["init_tools"] == ["StructuredOutput"] else "init.tools: 不一致"
    )
    negative, positive = sentinel_runs["without_settings_block"], sentinel_runs["final_argv"]
    sentinel_line = (
        f"番兵: 遮断なしで {negative['failure_cause']}、最終 argv で "
        f"{'成功' if positive['failure_cause'] is None else positive['failure_cause']}"
    )
    restore_line = f"後始末: {'元のバイト列に戻った' if restored == original else '元のバイト列と違う'}"
    path = repo / "docs" / "test" / "measurements" / "claude-cli-hardening.md"
    path.write_text(render_hardening(record, [tools_line, sentinel_line, restore_line]))
    print("\n".join([tools_line, sentinel_line, restore_line]))
    return 0


def render_hardening(record: dict[str, object], lines: list[str]) -> str:
    conclusion = "\n".join(lines)
    return f"""# 実測ログ: claude CLI のフラグ(第2段)

NFR-8 のフラグの実測の第2段(T-060 / C-023・C-024)。採取側は独自の argv を持たず、製品の
`build_argv`(`ClaudeCliLlm` と同じ関数)をそのまま使う。その最終 argv(拒否リスト無し)で:

1. `init.tools` が `{{StructuredOutput}}` と一致するか(C-024)。
2. SEC-001 の番兵(D-193、D-213): 最小ホームの `.claude/settings.json` をその場で一時的に書き換え、到達できない
   `ANTHROPIC_BASE_URL` を `env` に置く。最終 argv からユーザ設定の遮断(`--setting-sources` とその値)だけを
   外した argv では番兵のアドレスへの接続の失敗で落ち、最終 argv では成功するか(C-023)。起動は中身検査を
   通さない直接起動。後始末は元のバイト列の sha256 の一致で確かめる。資格情報は写さない。

`tests/measurements/collect_claude_cli.py hardening` が書き、`tests/measurements/test_claude_cli_hardening_logs.py`
が機械判定する。採取は最小ホーム(ADR-014)を `HOME` に向け、子プロセスへ渡す環境は `HOME` / `PATH` /
`LANG` / `TZ` の4つだけ。送ったのは合成プロンプトだけ。接続の失敗は stderr と `result` の本文から分類する
(2.1.290 は stream-json の下で stderr を空にし、`result` の本文に書く)。

## 記録

```yaml
{yaml.safe_dump(record, allow_unicode=True, sort_keys=False).rstrip()}
```

## 結論

{conclusion}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("what", choices=("contract", "home-probe", "flags", "hardening"))
    args = parser.parse_args()

    metered = sorted(v for v in METERED_VARS if v in os.environ)
    if metered:
        print(f"親環境に metered 認証変数がある: {metered}", file=sys.stderr)
        return 2
    if not (MINIMAL_HOME / ".claude" / ".credentials.json").is_file():
        print(f"最小ホームが用意されていない: HOME={MINIMAL_HOME} claude /login", file=sys.stderr)
        return 2
    commands = {"contract": contract, "home-probe": home_probe, "flags": flags, "hardening": hardening}
    return commands[args.what](args.repo, metered)


if __name__ == "__main__":
    sys.exit(main())
