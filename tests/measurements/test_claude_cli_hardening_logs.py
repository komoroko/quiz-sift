"""子 CLI のフラグの実測 第2段の記録の検査(T-060 / C-023・C-024 NFR-8)。

サンドボックスには `claude` が無いので、証拠は `tests/measurements/collect_claude_cli.py hardening` がホストで
書いた `docs/test/measurements/claude-cli-hardening.md` で、このテストはその「## 記録」と「## 結論」を機械判定する。
記録の argv は製品の `build_argv` の出力と突き合わせる(採取側は独自の argv を持たない)。接続の失敗の分類は
記録の分類を信じず、stderr と `result` の本文からここで分類し直す。
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from quiz_sift.adapters.llm_claude_cli import build_argv

ROOT = Path(__file__).resolve().parent.parent.parent
RECORD = ROOT / "docs" / "test" / "measurements" / "claude-cli-hardening.md"
COLLECTOR = "tests/measurements/collect_claude_cli.py"
SETTINGS_BLOCK_FLAG = "--setting-sources"
CONNECTION_REFUSED = re.compile(r"ECONNREFUSED|Connection refused")
# 最小ホームの settings.json の、実測前の中身(NFR-3)。
MINIMAL_HOME_SETTINGS = {"theme": "dark"}
CREDENTIAL_MARKS = ("accessToken", "refreshToken", "sk-ant-", ".credentials.json")


def record_text() -> str:
    return RECORD.read_text(encoding="utf-8")


def record() -> dict[str, Any]:
    match = re.search(r"## 記録\n\n```yaml\n(.*?)\n```", record_text(), re.S)
    assert match, "claude-cli-hardening.md に「## 記録」の yaml ブロックが無い"
    loaded = yaml.safe_load(match.group(1))
    assert isinstance(loaded, dict)
    return loaded


def conclusion() -> list[str]:
    return record_text().rpartition("\n## 結論\n")[2].strip().splitlines()


def product_argv(argv: list[str]) -> list[str]:
    """記録の argv の本文フラグの値で、製品の `build_argv` を呼び直した argv。"""
    model = argv[argv.index("--model") + 1]
    system_prompt = argv[argv.index("--system-prompt") + 1]
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    return list(build_argv(model, system_prompt, schema))


def check_provenance(rec: dict[str, Any]) -> None:
    assert rec["collector"] == COLLECTOR and (ROOT / COLLECTOR).is_file()
    assert Path(rec["home"]).name == ".quiz-sift-claude-home", rec["home"]
    assert rec["env_keys"] == ["HOME", "LANG", "PATH", "TZ"]
    assert rec["metered_vars_in_parent_env"] == []
    assert rec["prompt"].startswith("SYNTHETIC HARDENING PROBE")
    assert "No study material is included." in rec["prompt"]
    # 中身検査(起動前検査)を通さない直接起動。
    assert rec["launch"] == "subprocess.run(direct, no preflight)"


def is_connection_refused(run: dict[str, Any]) -> bool:
    return bool(CONNECTION_REFUSED.search(f"{run['stderr']}\n{run['result_text'] or ''}"))


def test_c024_final_argv_init_tools_is_structured_output_only() -> None:
    rec = record()
    check_provenance(rec)
    run = rec["init_tools_run"]
    argv = run["argv"]
    assert argv == product_argv(argv), "記録の argv が製品の build_argv の出力と違う"
    # 拒否リスト(`--settings` による deny 列挙)は無く、空の許可リストがある(D-208)。
    assert "--settings" not in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert run["exit_code"] == 0 and run["result_is_error"] is False, run
    assert run["init_tools"] == ["StructuredOutput"], run["init_tools"]
    assert run["structured_output_returned"] is True
    assert "init.tools: 一致({StructuredOutput})" in conclusion()


def test_c023_sentinel_fails_without_flag_and_passes_with_it() -> None:
    rec = record()
    check_provenance(rec)
    sentinel = rec["sentinel"]
    assert sentinel["path"] == "$HOME/.claude/settings.json"
    url = sentinel["env"]["ANTHROPIC_BASE_URL"]
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+", url), url

    final = rec["sentinel_runs"]["final_argv"]
    negative = rec["sentinel_runs"]["without_settings_block"]
    assert final["argv"] == product_argv(final["argv"]), "最終 argv が製品の build_argv の出力と違う"
    assert final["argv"] == rec["init_tools_run"]["argv"]
    # 陰性対照: 最終 argv からユーザ設定の遮断(フラグとその値)だけを外した argv。
    i = final["argv"].index(SETTINGS_BLOCK_FLAG)
    assert negative["argv"] == final["argv"][:i] + final["argv"][i + 2 :]

    # 遮断なしは番兵のアドレスへの接続の失敗で落ちる。
    assert negative["exit_code"] != 0, negative
    assert negative["structured_output_returned"] is False
    assert is_connection_refused(negative), (negative["stderr"], negative["result_text"])
    assert negative["failure_cause"] == "connection_refused"

    # 最終 argv では番兵を読まずに成功する。
    assert final["exit_code"] == 0 and final["result_is_error"] is False, final
    assert final["structured_output_returned"] is True
    assert not is_connection_refused(final)
    assert final["failure_cause"] is None
    assert "番兵: 遮断なしで connection_refused、最終 argv で 成功" in conclusion()


def test_minimal_home_is_restored_byte_for_byte() -> None:
    rec = record()
    sentinel = rec["sentinel"]
    original = sentinel["sha256_original"]
    assert sentinel["sha256_after_restore"] == original
    assert sentinel["sha256_with_sentinel"] != original
    # 戻った中身は実測前の最小ホームの設定で、その sha256 は記録した元のバイト列のもの。
    assert sentinel["restored_content"] == MINIMAL_HOME_SETTINGS
    expected = (json.dumps(MINIMAL_HOME_SETTINGS, indent=2) + "\n").encode()
    assert hashlib.sha256(expected).hexdigest() == original
    assert sentinel["credentials_copied"] is False
    text = record_text()
    found = [mark for mark in CREDENTIAL_MARKS if mark in text]
    assert not found, f"資格情報らしき文字列が記録に現れる: {found}"
    assert "後始末: 元のバイト列に戻った" in conclusion()
