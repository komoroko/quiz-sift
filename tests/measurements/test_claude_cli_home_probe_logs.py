"""番兵による CLAUDE.md 系の読み込み 0 件のホスト実測の検査(T-041 / C-020 NFR-8 (e)5)。

サンドボックスには `claude` が無いので、証拠は `tests/measurements/collect_claude_cli.py home-probe` がホストで
書いた `docs/test/measurements/claude-cli-home-probe.md` の「## 記録」で、このテストはその中身を機械判定する。

判定(A-6):

1. 採取経路は collect_claude_cli.py にあり、4つの場所に実行ごとの乱数の合言葉を置いた。
2. `--safe-mode` 付きの設計 argv で返った合言葉が 0 件。
3. positive control: 最終 argv からユーザ設定の遮断(`--setting-sources` とその値)と `--safe-mode` を外した
   argv で4つとも返る(無いと「0 件」は番兵が効いていないだけでも成立する。T-060 A-7)。
4. 後始末の後に番兵が残らず、最小ホームの `.claude/CLAUDE.md` と `rules/` が無い。作業ディレクトリの親は
   採取スクリプトが作った専用のディレクトリで、`/tmp` 直下ではない(D-151)。

設計 argv の形は契約フィクスチャ(`test_claude_cli_logs.py` が設計 (e) と突き合わせる)の argv と、
`--json-schema` と `--system-prompt` の値を除いて一致することで確かめる。
"""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
PROBE = ROOT / "docs" / "test" / "measurements" / "claude-cli-home-probe.md"
SUCCESS_META = ROOT / "tests" / "fixtures" / "claude_cli" / "success.meta.yaml"
COLLECTOR = "tests/measurements/collect_claude_cli.py"

LOCATIONS = [
    "$HOME/.claude/CLAUDE.md",
    "$HOME/.claude/rules/probe.md",
    "<parent>/CLAUDE.md",
    "<parent>/.claude/CLAUDE.md",
]
CODEWORD = re.compile(r"QSPROBE-[0-9A-F]{8}")
PER_CALL_VALUES = ("--json-schema", "--system-prompt")
SETTINGS_BLOCK_FLAG = "--setting-sources"


def probe_record() -> dict[str, Any]:
    text = PROBE.read_text(encoding="utf-8")
    match = re.search(r"## 記録\n\n```yaml\n(.*?)\n```", text, re.S)
    assert match, "claude-cli-home-probe.md に「## 記録」の yaml ブロックが無い"
    loaded = yaml.safe_load(match.group(1))
    assert isinstance(loaded, dict)
    return loaded


def without_per_call_values(argv: list[str]) -> list[str]:
    masked = list(argv)
    for flag in PER_CALL_VALUES:
        masked[masked.index(flag) + 1] = "<per-call>"
    return masked


def test_sentinels_are_read_only_without_safe_mode() -> None:
    record = probe_record()

    # (1) 採取経路と番兵。
    assert record["collector"] == COLLECTOR
    assert (ROOT / COLLECTOR).is_file()
    assert "rules/probe.md" in (ROOT / COLLECTOR).read_text(encoding="utf-8")
    assert Path(record["home"]).name == ".quiz-sift-claude-home", record["home"]
    assert record["env_keys"] == ["HOME", "LANG", "PATH", "TZ"]
    assert record["metered_vars_in_parent_env"] == []
    assert record["prompt"].startswith("SYNTHETIC HOME PROBE")
    sentinels = record["sentinels"]
    assert [s["location"] for s in sentinels] == LOCATIONS
    codewords = {s["codeword"] for s in sentinels}
    assert len(codewords) == len(LOCATIONS), sentinels
    assert all(CODEWORD.fullmatch(c) for c in codewords), sentinels

    # 設計 argv(--safe-mode あり)と、それからユーザ設定の遮断と --safe-mode を外した argv。
    safe = record["runs"]["safe_mode"]
    plain = record["runs"]["without_safe_mode"]
    contract_argv = yaml.safe_load(SUCCESS_META.read_text(encoding="utf-8"))["argv"]
    assert "--safe-mode" in safe["argv"]
    assert without_per_call_values(safe["argv"]) == without_per_call_values(contract_argv)
    i = safe["argv"].index(SETTINGS_BLOCK_FLAG)
    unblocked = safe["argv"][:i] + safe["argv"][i + 2 :]
    assert plain["argv"] == [t for t in unblocked if t != "--safe-mode"]
    assert safe["exit_code"] == 0 and plain["exit_code"] == 0

    # (2) --safe-mode ありで返った合言葉は 0 件。
    leaked = [c for c in safe["returned"] if CODEWORD.search(c)]
    assert not leaked, f"--safe-mode でも番兵が読まれた: {leaked}"
    assert safe["memory_paths"] is None

    # (3) positive control: ユーザ設定の遮断と --safe-mode なしで4つとも返る。
    missing = sorted(codewords - {m.group() for c in plain["returned"] for m in CODEWORD.finditer(c)})
    assert not missing, f"--safe-mode なしで返らなかった番兵: {missing}"

    # (4) 後始末と、作業ディレクトリの親の置き場所。
    cleanup = record["cleanup"]
    assert cleanup["sentinels_remaining"] == []
    assert cleanup["home_claude_md_exists"] is False
    assert cleanup["home_rules_exists"] is False
    assert cleanup["parent_dir_exists"] is False
    # 置き換えたファイルがあれば、元のバイト列の sha256 に戻っていること。
    for replaced in cleanup["replaced_files"]:
        assert replaced["sha256_before"] == replaced["sha256_after"], replaced
    parent = Path(record["parent_dir"])
    assert record["parent_created_by_collector"] is True
    assert parent.is_absolute() and parent != Path("/tmp") and parent.parent != Path("/tmp"), parent
