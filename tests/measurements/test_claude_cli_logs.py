"""実 `claude` CLI の契約フィクスチャと init.tools 実測の検査(T-041 / C-020 NFR-8 (e) / C-018 NFR-6)。

サンドボックスには `claude` が無いので、証拠は `tests/measurements/collect_claude_cli.py` がホストで採った
`tests/fixtures/claude_cli/` と `docs/test/measurements/claude-cli-contract.md` で、このテストはその中身を
機械判定する。期待する argv の形は設計 NFR-8 節の (e) と、T-054 の結論で選んだフラグ(ユーザ設定の遮断・
空の許可リスト。拒否リストは無い。D-208)からここに書き写しており、採取スクリプトの定数は読まない(採取側の
定数と突き合わせると、両方が同じ誤りを持ったとき緑になる)。それとは別に、フィクスチャの argv が製品の
`build_argv` の出力そのものであることも確かめる(T-060 A-7)。

判定(各受入基準に1本):

- A-2: meta の sha256 が本体と一致し、フィクスチャが success / success_opus / invalid_schema の3組だけで、
  argv が `--safe-mode` を含む設計 (e) の形。success は haiku・success_opus は opus で終了コード 0。
  不正スキーマは非 0 終了・stderr が `Error:`・stdout 空(D-119 / D-133)。
- A-3: 2件の成功フィクスチャの init イベントの tools が {StructuredOutput}、apiKeySource が none、
  memory_paths が null で、md の記録がフィクスチャ本体から読んだ値と一致する。`--safe-mode` の下では
  init に memory_paths のキー自体が無い(2026-09-26 実測)ので、null はキーの不在として読む。
- A-4: 採取時の HOME が最小ホーム、子の環境が4変数、親環境の metered 認証変数が 0 個で、記録に変数名が
  1つも現れない。最小ホームのファイル一覧が採取の前後で相対パスとして残り、送った本文は合成プロンプトだけ。
- A-5: 2件の成功フィクスチャのそれぞれに rate_limit_event があり、status が allowed / allowed_warning、
  isUsingOverage が false。meta の試行回数は3以下(D-133)。
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from quiz_sift.adapters.llm_claude_cli import argv_digest, build_argv

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "claude_cli"
CONTRACT = ROOT / "docs" / "test" / "measurements" / "claude-cli-contract.md"

CASES = ("success", "success_opus", "invalid_schema")
SUCCESSES = {"success": "claude-haiku-4-5", "success_opus": "claude-opus-5-5"}
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
    "--setting-sources",
    "--tools",
    "--permission-mode",
}
BARE_FLAGS = {"-p", "--verbose", "--exclude-dynamic-system-prompt-sections", "--strict-mcp-config", "--safe-mode"}
MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"}


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
    # ユーザ設定を1つも読まず、有効ツールは空の許可リストで絞る(T-054 の結論)。
    assert valued["--setting-sources"] == ""
    assert valued["--tools"] == ""
    assert "--dangerously-skip-permissions" not in argv
    return valued


def test_fixtures_match_their_meta_and_the_design_argv() -> None:
    files = sorted(p.name for p in FIXTURES.iterdir() if p.suffix in {".stdout", ".yaml"})
    assert files == sorted(f"{name}{ext}" for name in CASES for ext in (".stdout", ".meta.yaml")), files

    for name in CASES:
        m = meta(name)
        body = (FIXTURES / f"{name}.stdout").read_bytes()
        assert m["stdout_sha256"] == hashlib.sha256(body).hexdigest(), name
        for key in ("collected_at", "claude_version", "model", "argv", "prompt", "exit_code", "stderr", "attempts"):
            assert key in m, f"{name}.meta.yaml に {key} が無い"
        assert m["argv"][m["argv"].index("--model") + 1] == m["model"]

    for name, model in SUCCESSES.items():
        m = meta(name)
        assert m["model"] == model, name
        valued = assert_design_argv(m["argv"])
        assert isinstance(json.loads(valued["--json-schema"]), dict)
        assert m["exit_code"] == 0, name
        results = [e for e in events(name) if e.get("type") == "result"]
        assert len(results) == 1 and results[0]["subtype"] == "success", results

    success = meta("success")
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
    record = contract_record()
    assert set(record["fixtures"]) == set(SUCCESSES), record["fixtures"]
    for name in SUCCESSES:
        inits = [e for e in events(name) if e.get("type") == "system" and e.get("subtype") == "init"]
        assert len(inits) == 1, name
        init = inits[0]
        assert set(init["tools"]) == {"StructuredOutput"}, (name, init["tools"])
        assert init["apiKeySource"] == "none", (name, init["apiKeySource"])
        assert init.get("memory_paths") is None, (name, init.get("memory_paths"))

        recorded = record["fixtures"][name]
        assert recorded["init_tools"] == init["tools"], name
        assert recorded["api_key_source"] == init["apiKeySource"], name
        assert recorded["memory_paths"] is None and init.get("memory_paths") is None, name
        assert recorded["memory_paths_key_present"] == ("memory_paths" in init), name

        # 実行中に許容集合外のツールが使われていないこと(AR-61)。
        used = {
            block["name"]
            for e in events(name)
            if e.get("type") == "assistant"
            for block in e["message"]["content"]
            if block.get("type") == "tool_use"
        }
        assert used <= {"StructuredOutput"}, (name, used)
    assert record["claude_version"] == meta("success")["claude_version"]


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


def test_success_fixtures_carry_a_rate_limit_event() -> None:
    record = contract_record()
    for name in SUCCESSES:
        infos = [e["rate_limit_info"] for e in events(name) if e.get("type") == "rate_limit_event"]
        assert infos, f"{name} に rate_limit_event が無い"
        for info in infos:
            assert info["status"] in {"allowed", "allowed_warning"}, (name, info["status"])
            assert info["isUsingOverage"] is False, (name, info["isUsingOverage"])
        assert record["fixtures"][name]["rate_limit_events"] == [
            {"status": info["status"], "isUsingOverage": info["isUsingOverage"]} for info in infos
        ], name
        assert 1 <= meta(name)["attempts"] <= 3, (name, meta(name)["attempts"])


def test_contract_fixtures_were_taken_with_the_product_argv() -> None:
    """フィクスチャの argv は製品の `build_argv` の出力そのもので、採取側の独自 argv ではない(T-060 A-7)。"""
    for name, model in SUCCESSES.items():
        argv = meta(name)["argv"]
        assert_design_argv(argv)
        system_prompt = argv[argv.index("--system-prompt") + 1]
        schema = json.loads(argv[argv.index("--json-schema") + 1])
        assert argv == list(build_argv(model, system_prompt, schema)), name

    # 不正スキーマは `--json-schema` の値だけが違う。本文フラグの値を伏せたダイジェストで突き合わせる。
    invalid = meta("invalid_schema")["argv"]
    assert argv_digest(invalid) == argv_digest(build_argv(meta("invalid_schema")["model"], "", {}))
