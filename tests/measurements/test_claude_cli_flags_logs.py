"""子 CLI のフラグの実測 第1段の記録の検査(T-054 / C-023・C-024 NFR-8)。

サンドボックスには `claude` が無いので、証拠は `tests/measurements/collect_claude_cli.py flags` がホストで書いた
`docs/test/measurements/claude-cli-flags.md` で、このテストはその「## 記録」と「## 結論」を機械判定する。
記録が無ければ赤になる。
"""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
RECORD = ROOT / "docs" / "test" / "measurements" / "claude-cli-flags.md"
COLLECTOR = "tests/measurements/collect_claude_cli.py"

# 許可リストの候補フラグと値。権限の自動承認のフラグは候補に数えない。
ALLOWLIST_FLAGS = ("--tools",)
ALLOWLIST_VALUES = ("", "StructuredOutput")
AUTO_APPROVE_FLAGS = ("--allowedTools", "--allowed-tools")

SETTINGS_LINE = re.compile(r"ユーザ設定の遮断: (?:あり\((--[\w-]+)\)|なし)")
ALLOWLIST_LINE = re.compile(r"許可リスト: (?:一致あり\((--[\w-]+) (\"[^\"]*\")\)|一致なし)")


def record_text() -> str:
    return RECORD.read_text(encoding="utf-8")


def flags_record() -> dict[str, Any]:
    match = re.search(r"## 記録\n\n```yaml\n(.*?)\n```", record_text(), re.S)
    assert match, "claude-cli-flags.md に「## 記録」の yaml ブロックが無い"
    loaded = yaml.safe_load(match.group(1))
    assert isinstance(loaded, dict)
    return loaded


def conclusion_lines() -> list[str]:
    text = record_text()
    _, sep, tail = text.rpartition("\n## 結論\n")
    assert sep, "claude-cli-flags.md に「## 結論」が無い"
    assert "\n## " not in tail, "「## 結論」が末尾の節でない"
    return [line for line in tail.splitlines() if line.strip()]


def test_flags_record_lists_version_and_candidate_flags() -> None:
    record = flags_record()
    assert record["collector"] == COLLECTOR
    assert "def flags(" in (ROOT / COLLECTOR).read_text(encoding="utf-8")
    assert re.fullmatch(r"\d+\.\d+\.\d+ \(Claude Code\)", record["claude_version"]), record["claude_version"]
    assert re.fullmatch(r"[0-9a-f]{64}", record["help_sha256"])
    assert Path(record["home"]).name == ".quiz-sift-claude-home", record["home"]
    assert record["env_keys"] == ["HOME", "LANG", "PATH", "TZ"]
    assert record["metered_vars_in_parent_env"] == []
    assert record["prompt"].startswith("SYNTHETIC FLAG PROBE")
    for key in ("settings_flags", "tool_flags"):
        names = record[key]
        assert isinstance(names, list) and names, key
        assert all(re.fullmatch(r"--[\w-]+", n) for n in names), names


def test_flags_record_has_init_tools_per_allowlist_candidate() -> None:
    record = flags_record()
    runs = record["allowlist_runs"]
    expected = [(f, v) for f in ALLOWLIST_FLAGS if f in record["tool_flags"] for v in ALLOWLIST_VALUES]
    assert expected, "--help に許可リストの候補フラグが1つも無い"
    assert [(r["flag"], r["value"]) for r in runs] == expected
    for run in runs:
        argv = run["argv"]
        assert run["flag"] not in AUTO_APPROVE_FLAGS
        assert argv[0] == "claude" and argv[-2:] == [run["flag"], run["value"]], argv
        assert "--settings" not in argv, "拒否リストが argv に残っている"
        assert not set(AUTO_APPROVE_FLAGS) & set(argv), argv
        assert "--json-schema" in argv
        assert isinstance(run["exit_code"], int)
        assert run["init_tools"] is None or isinstance(run["init_tools"], list), run["init_tools"]
        assert isinstance(run["structured_output_returned"], bool)
        assert run["structured_output_returned"] == (
            isinstance(run["structured_output"], dict) and run["result_is_error"] is False
        ), run


def test_flags_record_states_the_chosen_flags() -> None:
    record = flags_record()
    lines = conclusion_lines()
    assert len(lines) == 2, lines
    settings = SETTINGS_LINE.fullmatch(lines[0])
    allowlist = ALLOWLIST_LINE.fullmatch(lines[1])
    assert settings, lines[0]
    assert allowlist, lines[1]

    # 遮断ありと書くなら、そのフラグは --help に現れた設定のフラグであること。
    if settings.group(1):
        assert settings.group(1) in record["settings_flags"]

    # 一致ありは、拒否リストを外した argv で init.tools が {StructuredOutput} と一致し構造化出力が返った候補。
    matching = [
        (r["flag"], r["value"])
        for r in record["allowlist_runs"]
        if r["init_tools"] is not None
        and set(r["init_tools"]) == {"StructuredOutput"}
        and r["structured_output_returned"]
    ]
    if allowlist.group(1):
        assert (allowlist.group(1), yaml.safe_load(allowlist.group(2))) in matching, (lines[1], matching)
    else:
        assert not matching, matching
