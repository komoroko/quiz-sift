"""実測ログ `docs/test/measurements/pre-commit.md` についての検査(A-4)。

pre-commit はフックを GitHub から clone するため、`--network none` の品質ゲートでは
原理的に走らない(D-031)。そのため NFR-5 (c) の証拠は**ホストで取った実測ログ**であり、
このテストが見ているのはそのログが「規定の見出しを備え、終了コード 0 を記録し、
.pre-commit-config.yaml が現に宣言しているフックを全部通したログである」ことになる。

ログの再実行はできないので、ここで防げるのは**古いログ・作り話のログ**であって、
「pre-commit が今この瞬間に緑である」ことではない。前者はフックを1本足して測り直さない、
表だけ書き換えて生ログを直さない、といった形で現れる。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "docs" / "test" / "measurements" / "pre-commit.md"

#: A-4 の「規定の見出し」。1つでも欠ければログとして読めない。
REQUIRED_HEADINGS = ("## 実行条件", "## 終了コード", "## 出力", "## 実行されたフック")

#: pre-commit の1行の結果表示。名前はドットで右端まで埋められる。
RESULT_LINE = re.compile(r"^(?P<name>.*?)\.{3,}(?P<result>Passed|Failed|Skipped|No files to check.*)$")


def _log_text() -> str:
    assert LOG.is_file(), f"実測ログが無い: {LOG.relative_to(ROOT)}"
    return LOG.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """`heading` の直後から次の `## ` 見出しまでを返す。"""
    assert heading in text, f"規定の見出しが無い: {heading}"
    body = text.split(heading, 1)[1]
    return body.split("\n## ", 1)[0]


def _table_rows(section: str) -> list[list[str]]:
    """Markdown の表の本体行を、セルのリストとして返す(ヘッダと区切り行は落とす)。"""
    rows: list[list[str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
        if all(set(c) <= {"-", ":"} and c for c in cells):  # 区切り行
            continue
        rows.append(cells)
    return rows[1:] if rows else []


def _configured_commit_stage_hook_ids() -> set[str]:
    """`pre-commit run --all-files` が既定で走らせるフック(= pre-commit ステージ)の id。

    `stages:` を明示したフックは別ステージに属する。`.pre-commit-config.yaml` の mypy は
    `stages: [pre-push]` なので `--all-files` の既定実行には現れない — それをログに
    要求すると、正しいログが赤になる。
    """
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    ids: set[str] = set()
    for repo in config["repos"]:
        for hook in repo["hooks"]:
            stages = hook.get("stages")
            if stages is None or "pre-commit" in stages:
                ids.add(hook["id"])
    return ids


def test_pre_commit_log_records_a_zero_exit() -> None:
    """A-4: 規定の見出しを備え、記録された終了コードが 0 であること。

    失敗ログを貼っても「記録した」ことにはならない。0 以外が記録されていれば赤。
    """
    text = _log_text()
    for heading in REQUIRED_HEADINGS:
        assert heading in text, f"規定の見出しが無い: {heading}"

    recorded = _section(text, "## 終了コード").strip()
    assert recorded.isdigit(), f"終了コードが数値として読めない: {recorded!r}"
    assert int(recorded) == 0, f"記録された終了コードが 0 でない: {recorded}"


def test_the_log_records_the_command_the_criterion_names() -> None:
    """記録されたコマンドが `pre-commit run --all-files` であること。

    別のコマンド(例: `pre-commit run ruff`)の緑を貼っても NFR-5 (c) の証拠にならない。
    """
    conditions = {row[0]: row[1] for row in _table_rows(_section(_log_text(), "## 実行条件")) if len(row) >= 2}
    assert conditions.get("コマンド") == "pre-commit run --all-files", (
        f"実行条件に記録されたコマンドが違う: {conditions.get('コマンド')!r}"
    )
    assert re.fullmatch(r"[0-9a-f]{7,40}", conditions.get("コミット", "")), (
        f"コミット ID が記録されていない: {conditions.get('コミット')!r}"
    )


def test_the_log_covers_every_hook_the_config_declares() -> None:
    """表に並ぶ hook id が、設定が宣言する pre-commit ステージのフックと一致すること。

    これが無いと、フックを1本足して測り直さなかった古いログが緑のまま通る。
    CI とゲートの `<step>` 集合を比べているのと同じ理由で、**部分集合ではなく一致**を見る。
    """
    rows = _table_rows(_section(_log_text(), "## 実行されたフック"))
    assert rows, "実行されたフックの表が空"
    logged = {row[0] for row in rows}
    configured = _configured_commit_stage_hook_ids()
    assert logged == configured, (
        f"ログと設定でフックが食い違う: ログのみ={sorted(logged - configured)} / 設定のみ={sorted(configured - logged)}"
    )
    failed = [row[0] for row in rows if row[2] != "Passed"]
    assert not failed, f"Passed でないフックが記録されている: {failed}"


def test_the_hook_table_matches_the_raw_output() -> None:
    """表の各行が、生ログの同じ結果行に裏付けられていること。

    表は人が書く。生ログを貼り替えずに表だけ Failed → Passed に直せてしまうと、
    上の2つのテストは表だけを見て緑になる。両者を突き合わせて、その編集を赤にする。
    """
    text = _log_text()
    raw = text.split("## 出力", 1)[1].split("```", 2)[1]
    results = {m.group("name").strip(): m.group("result") for m in map(RESULT_LINE.match, raw.splitlines()) if m}
    assert results, "出力ブロックから pre-commit の結果行を1つも読めていない"

    rows = _table_rows(_section(text, "## 実行されたフック"))
    for hook_id, display_name, result in ((r[0], r[1], r[2]) for r in rows):
        assert display_name in results, f"{hook_id} の表示名 {display_name!r} が生ログに無い"
        assert results[display_name] == result, f"{hook_id}: 表は {result} だが生ログは {results[display_name]}"
    assert len(results) == len(rows), (
        f"生ログの結果行 {len(results)} 本に対し表は {len(rows)} 行 — どちらかが欠けている"
    )


def test_the_recorded_commit_exists_in_this_repository() -> None:
    """記録されたコミット ID がこのリポジトリに実在すること。

    実測ログが指す木が辿れないなら、その緑が何に対して取られたのかを誰も確かめられない。
    git が無い環境(サンドボックスは worktree だけをマウントする)では確かめる手段が
    無いので飛ばす — 飛ばすのは手段が無いときだけで、あるなら必ず確かめる。
    """
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git が無いのでコミット ID を確かめられない")

    conditions = {row[0]: row[1] for row in _table_rows(_section(_log_text(), "## 実行条件")) if len(row) >= 2}
    commit = conditions["コミット"]
    found = subprocess.run(
        [git, "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert found.returncode == 0, f"記録されたコミットがこのリポジトリに無い: {commit}"
