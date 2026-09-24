"""実 `claude` CLI の契約フィクスチャと init.tools 実測の検査(T-029 / C-020 NFR-8 (e) / C-018 NFR-6)。

サンドボックスには `claude` が無いので、証拠は `tests/measurements/collect_claude_cli.py` がホストで採った
`tests/fixtures/claude_cli/` と `docs/test/measurements/claude-cli-contract.md` で、このテストはその中身を
機械判定する。期待する argv の形は設計 NFR-8 節の (e) からここに書き写しており、採取スクリプトの定数は
読まない(採取側の定数と突き合わせると、両方が同じ誤りを持ったとき緑になる)。

判定(各受入基準に1本):

- A-2: meta の sha256 が本体と一致し、フィクスチャが success / invalid_schema の2組だけで、argv が
  設計 (e) の形。不正スキーマは非 0 終了・stderr が `Error:`・stdout 空、成功は終了コード 0(D-119)。
- A-3: 成功フィクスチャの init イベントの tools が {StructuredOutput}、apiKeySource が none で、md の記録が
  フィクスチャ本体から読んだ値と一致する。
- A-4: 採取時の HOME が最小ホーム、子の環境が4変数、親環境の metered 認証変数が 0 個で、記録に変数名が
  1つも現れない。最小ホームのファイル一覧が採取の前後で相対パスとして残り、送った本文は合成プロンプトだけ。
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "claude_cli"
CONTRACT = ROOT / "docs" / "test" / "measurements" / "claude-cli-contract.md"

CASES = ("success", "invalid_schema")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# docs/10-requirements.md NFR-8 の「metered 認証変数の集合」(AR-48)。採取スクリプトの定数は読まない。
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

# 設計 NFR-8 節 (e) の argv。値を取るフラグと、値を取らないフラグ。
VALUED_FLAGS = {
    "--output-format",
    "--max-turns",
    "--json-schema",
    "--model",
    "--system-prompt",
    "--mcp-config",
    "--settings",
    "--permission-mode",
}
BARE_FLAGS = {"-p", "--verbose", "--exclude-dynamic-system-prompt-sections", "--strict-mcp-config"}
MODELS = {"claude-opus-5-5", "claude-haiku-4-5"}


def meta(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load((FIXTURES / f"{name}.meta.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def events(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / f"{name}.stdout").read_text(encoding="utf-8").splitlines()]


def contract_record() -> dict[str, Any]:
    text = CONTRACT.read_text(encoding="utf-8")
    match = re.search(r"## 記録\n\n```yaml\n(.*?)\n```", text, re.S)
    assert match, "claude-cli-contract.md に「## 記録」の yaml ブロックが無い"
    loaded = yaml.safe_load(match.group(1))
    assert isinstance(loaded, dict)
    return loaded


def parse_argv(argv: list[str]) -> tuple[dict[str, str], set[str]]:
    """設計 (e) の argv を値付きフラグと素のフラグに分ける。未知のフラグや重複は落とす。"""
    assert argv[0] == "claude", argv[0]
    valued: dict[str, str] = {}
    bare: set[str] = set()
    rest = iter(argv[1:])
    for token in rest:
        if token in VALUED_FLAGS:
            assert token not in valued, f"{token} が2回ある"
            valued[token] = next(rest)
        else:
            assert token in BARE_FLAGS, f"設計 (e) に無い引数: {token}"
            assert token not in bare, f"{token} が2回ある"
            bare.add(token)
    return valued, bare


def assert_design_argv(argv: list[str]) -> dict[str, str]:
    valued, bare = parse_argv(argv)
    assert bare == BARE_FLAGS, f"素のフラグが設計 (e) と違う: 欠落 {sorted(BARE_FLAGS - bare)}"
    assert set(valued) == VALUED_FLAGS, f"値付きフラグが設計 (e) と違う: 欠落 {sorted(VALUED_FLAGS - set(valued))}"
    assert valued["--output-format"] == "stream-json"
    assert valued["--max-turns"] == "3"
    assert valued["--model"] in MODELS
    assert valued["--permission-mode"] == "default"
    assert json.loads(valued["--mcp-config"]) == {"mcpServers": {}}
    settings = json.loads(valued["--settings"])
    assert list(settings) == ["permissions"] and list(settings["permissions"]) == ["deny"], settings
    deny = settings["permissions"]["deny"]
    assert isinstance(deny, list) and deny and all(isinstance(t, str) for t in deny)
    assert "StructuredOutput" not in deny
    assert "--dangerously-skip-permissions" not in argv
    return valued


def test_fixtures_match_their_meta_and_the_design_argv() -> None:
    files = sorted(p.name for p in FIXTURES.iterdir() if p.suffix in {".stdout", ".yaml"})
    assert files == sorted(f"{name}{ext}" for name in CASES for ext in (".stdout", ".meta.yaml")), files

    for name in CASES:
        m = meta(name)
        body = (FIXTURES / f"{name}.stdout").read_bytes()
        assert m["stdout_sha256"] == hashlib.sha256(body).hexdigest(), name
        for key in ("collected_at", "claude_version", "model", "argv", "prompt", "exit_code", "stderr"):
            assert key in m, f"{name}.meta.yaml に {key} が無い"
        assert m["argv"][m["argv"].index("--model") + 1] == m["model"]

    success = meta("success")
    valued = assert_design_argv(success["argv"])
    assert isinstance(json.loads(valued["--json-schema"]), dict)
    assert success["exit_code"] == 0
    results = [e for e in events("success") if e.get("type") == "result"]
    assert len(results) == 1 and results[0]["subtype"] == "success", results

    invalid = meta("invalid_schema")
    assert invalid["exit_code"] != 0
    assert invalid["stderr"].startswith("Error:"), invalid["stderr"]
    assert invalid["stdout_sha256"] == EMPTY_SHA256
    # 不正なのは --json-schema だけで、ほかは成功と同じ argv であること。
    assert_design_argv(invalid["argv"])
    i = success["argv"].index("--json-schema") + 1
    assert invalid["argv"][:i] + invalid["argv"][i + 1 :] == success["argv"][:i] + success["argv"][i + 1 :]
    try:
        json.loads(invalid["argv"][i])
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("invalid_schema の --json-schema が JSON として読めてしまう")


def test_init_tools_is_exactly_structured_output() -> None:
    inits = [e for e in events("success") if e.get("type") == "system" and e.get("subtype") == "init"]
    assert len(inits) == 1
    init = inits[0]
    assert set(init["tools"]) == {"StructuredOutput"}, init["tools"]
    assert init["apiKeySource"] == "none", init["apiKeySource"]

    record = contract_record()
    assert record["init_tools"] == init["tools"]
    assert record["api_key_source"] == init["apiKeySource"]
    assert record["claude_version"] == meta("success")["claude_version"]

    # 実行中に許容集合外のツールが使われていないこと(AR-61)。
    used = {
        block["name"]
        for e in events("success")
        if e.get("type") == "assistant"
        for block in e["message"]["content"]
        if block.get("type") == "tool_use"
    }
    assert used <= {"StructuredOutput"}, used


def test_collection_ran_in_the_minimal_home_without_metered_vars() -> None:
    record = contract_record()
    home = record["home"]
    assert Path(home).name == ".quiz-sift-claude-home", home
    assert record["env_keys"] == ["HOME", "LANG", "PATH", "TZ"]
    assert record["metered_vars_checked"] == len(METERED_VARS)
    assert record["metered_vars_in_parent_env"] == []

    for key in ("home_files_before", "home_files_after"):
        files = record[key]
        assert isinstance(files, list) and files, key
        assert all(isinstance(f, str) and not f.startswith("/") and ".." not in f for f in files), key
        assert ".claude/.credentials.json" in files, key

    recorded = [CONTRACT.read_text(encoding="utf-8")]
    recorded += [(FIXTURES / f"{name}.meta.yaml").read_text(encoding="utf-8") for name in CASES]
    for text in recorded:
        found = [v for v in METERED_VARS if v in text]
        assert not found, f"metered 認証変数名が記録に現れる: {found}"

    for name in CASES:
        m = meta(name)
        assert m["home"] == home
        assert m["env_keys"] == record["env_keys"]
        # 子へ送った本文(stdin)は合成プロンプトだけで、教材本文は含まない(NFR-2)。
        assert m["prompt"].startswith("SYNTHETIC CONTRACT PROBE"), m["prompt"]
        assert "No study material is included." in m["prompt"]
