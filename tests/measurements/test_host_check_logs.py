"""ホストで取る artifact 実測ログの検査(T-021)。

**A-1(C-014 / NFR-2 / AR-16)**: 書き出し先ガードの自前 `.gitignore` 評価器と、本物の
`git check-ignore -v` の判定を代表パス集合で突き合わせた記録
`docs/test/measurements/gitignore-equivalence.md` を読む。

突き合わせ自体もこのファイルに置いてある(`measure_gitignore_equivalence`)。記録は
`PYTHONPATH=src python tests/measurements/test_host_check_logs.py gitignore` で作り直す。
記録と今の木を結びつけるため、テストは次のどれが崩れても赤になる:

- 記録のどれか1行で git と自前評価器の判定が食い違っている(A-1 の本体)。
- 記録のパス集合・`.gitignore` の内容が、このファイルが宣言する代表集合・今の `.gitignore` と違う
  (代表集合を足した、`.gitignore` を書き換えた、のに測り直していない古い記録)。
- 記録された自前評価器の判定が、今の `gitignore_guard` を同じ入力で走らせた結果と違う
  (評価器を直したのに測り直していない記録、表だけ書き換えた記録)。
- git が使える環境では、記録された git の判定が今 git に聞き直した結果と違う(作り話の記録)。
"""

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from quiz_sift.safety.gitignore_guard import is_ignored, parse_gitignore

ROOT = Path(__file__).resolve().parent.parent.parent
GITIGNORE_LOG = ROOT / "docs" / "test" / "measurements" / "gitignore-equivalence.md"

#: リポジトリの `.gitignore` に対する代表パス集合。書き出し先(data/・out/)の深い階層、
#: 無視されないはずの追跡対象、拡張子パターン、ディレクトリ形式の規則に同名のファイルが
#: 当たる場合を含める。`True` はディレクトリ。
REPO_PATHS: tuple[tuple[str, bool], ...] = (
    ("data", True),
    ("data/deckA/questions.jsonl", False),
    ("data/deckA/raw/page-001.html", False),
    ("out", True),
    ("out/deckA/recall/abc123.json", False),
    ("out/deckA/audio/001_intro.mp3", False),
    ("out/deckA/report.html", False),
    ("out/learning.sqlite3", False),
    ("src/quiz_sift/out.py", False),
    ("src/quiz_sift/data/__init__.py", False),
    ("src/quiz_sift/__pycache__/cli.cpython-312.pyc", False),
    ("src/quiz_sift/cli.pyc", False),
    ("src/quiz_sift/cli.py", False),
    ("tests/fixtures/data/synthetic.jsonl", False),
    ("docs/test/measurements/gitignore-equivalence.md", False),
    ("web/app/page.tsx", False),
    ("web/lib/data", False),
    ("web/out", False),
    ("site/index.html", False),
    (".venv/bin/python", False),
    (".rein/work/T-021.json", False),
    (".rein/plan.yaml", False),
    (".worktrees/T-021/data/x.jsonl", False),
    (".mypy_cache/3.12/cache.db", False),
    ("README.md", False),
)

#: 自前評価器が扱う規則を一通り踏む合成 `.gitignore`。リポジトリの `.gitignore` は
#: ディレクトリ形式と拡張子しか使っていないので、それだけでは否定・`**`・文字クラス・
#: エスケープの等価性が測れない。
SYNTHETIC_GITIGNORE = """\
# comment
/build
logs/
*.tmp
!keep.tmp
docs/**/draft.md
**/cache
vendor/**
file?.txt
report[0-9].csv
note[!a].md
\\#hash
\\!bang
two\\ words
secret/
!secret/allowed.txt
a/**/b
"""

SYNTHETIC_PATHS: tuple[tuple[str, bool], ...] = (
    ("build", False),
    ("sub/build", False),
    ("logs", True),
    ("sub/logs/app.log", False),
    ("other/logs", False),
    ("a.tmp", False),
    ("sub/b.tmp", False),
    ("keep.tmp", False),
    ("sub/keep.tmp", False),
    ("docs/draft.md", False),
    ("docs/x/y/draft.md", False),
    ("sub/docs/draft.md", False),
    ("cache/f", False),
    ("deep/er/cache/f", False),
    ("vendor", True),
    ("vendor/a/b.js", False),
    ("file1.txt", False),
    ("file10.txt", False),
    ("report5.csv", False),
    ("reportx.csv", False),
    ("notea.md", False),
    ("noteb.md", False),
    ("#hash", False),
    ("!bang", False),
    ("two words", False),
    ("two", False),
    ("secret/allowed.txt", False),
    ("secret/other.txt", False),
    ("a/b", False),
    ("a/x/y/b", False),
    ("plain.txt", False),
)

REPO_CASE = "## 突き合わせ: リポジトリの .gitignore"
SYNTHETIC_CASE = "## 突き合わせ: 合成 .gitignore"
IGNORED = "ignored"
NOT_IGNORED = "not-ignored"
MATCH = "一致"
MISMATCH = "不一致"


@dataclass(frozen=True, slots=True)
class Verdict:
    """1パス分の突き合わせ結果。"""

    path: str
    is_dir: bool
    git_ignored: bool
    git_source: str
    evaluator_ignored: bool


def _git() -> str | None:
    return shutil.which("git")


def _git_check_ignore(git: str, gitignore: str, paths: tuple[tuple[str, bool], ...]) -> dict[str, tuple[bool, str]]:
    """使い捨てのリポジトリに `gitignore` を置き、パスを実体化して `git check-ignore -v -n` に聞く。

    リポジトリ自身の `.git/info/exclude`・追跡状態・利用者の `core.excludesFile` が判定に
    混ざらないように、一時リポジトリ・`--no-index`・`core.excludesFile=/dev/null` で走らせる。
    自前評価器が読むのもリポジトリ直下の `.gitignore` だけなので、これで入力が揃う。
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        subprocess.run([git, "init", "-q", str(repo)], check=True)
        (repo / ".gitignore").write_text(gitignore, encoding="utf-8")
        for path, is_dir in paths:
            target = repo / path
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
        done = subprocess.run(
            [git, "-c", "core.excludesFile=/dev/null", "check-ignore", "-v", "-n", "-z", "--no-index", "--stdin"],
            cwd=repo,
            input="".join(f"{path}\0" for path, _ in paths),
            capture_output=True,
            text=True,
        )
    assert done.returncode in (0, 1), f"git check-ignore が失敗した: {done.stderr}"
    fields = done.stdout.split("\0")[:-1]
    assert len(fields) == 4 * len(paths), f"git check-ignore の出力を読めない: {done.stdout!r}"
    verdicts: dict[str, tuple[bool, str]] = {}
    for i in range(0, len(fields), 4):
        source, line, pattern, path = fields[i : i + 4]
        # 否定パターンにマッチした行も -v では表示される。その場合は無視されない。
        ignored = bool(pattern) and not pattern.startswith("!")
        verdicts[path] = (ignored, f"{source}:{line}:{pattern}" if pattern else "")
    return verdicts


def measure_gitignore_equivalence(git: str, gitignore: str, paths: tuple[tuple[str, bool], ...]) -> list[Verdict]:
    """`paths` の各々について、git と自前評価器の判定を並べる。"""
    from_git = _git_check_ignore(git, gitignore, paths)
    rules = parse_gitignore(gitignore)
    return [
        Verdict(
            path=path,
            is_dir=is_dir,
            git_ignored=from_git[path][0],
            git_source=from_git[path][1],
            evaluator_ignored=is_ignored(path, rules, is_dir=is_dir),
        )
        for path, is_dir in paths
    ]


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _label(ignored: bool) -> str:
    return IGNORED if ignored else NOT_IGNORED


def _render_table(verdicts: list[Verdict]) -> str:
    lines = [
        "| パス | 種別 | git | git の根拠 | 自前評価器 | 判定 |",
        "|---|---|---|---|---|---|",
    ]
    for v in verdicts:
        assert "|" not in v.path + v.git_source, f"表に書けない文字を含む: {v}"
        lines.append(
            f"| `{json.dumps(v.path, ensure_ascii=False)}` | {'dir' if v.is_dir else 'file'} "
            f"| {_label(v.git_ignored)} | `{v.git_source or '-'}` | {_label(v.evaluator_ignored)} "
            f"| {MATCH if v.git_ignored == v.evaluator_ignored else MISMATCH} |"
        )
    return "\n".join(lines)


def render_gitignore_log(git: str) -> str:
    """実測して、記録の Markdown を返す。"""
    repo_gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    repo_rows = measure_gitignore_equivalence(git, repo_gitignore, REPO_PATHS)
    synthetic_rows = measure_gitignore_equivalence(git, SYNTHETIC_GITIGNORE, SYNTHETIC_PATHS)
    commit = subprocess.run([git, "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    version = subprocess.run([git, "--version"], capture_output=True, text=True).stdout.strip()
    mismatches = sum(v.git_ignored != v.evaluator_ignored for v in repo_rows + synthetic_rows)
    return f"""# 実測ログ: .gitignore 評価器と git check-ignore の等価性

NFR-2(C-014)の書き出し先ガード `safety/gitignore_guard.py` は `.gitignore` を自前で評価する。
その評価器が本物の git と同じ判定を返すことを、ホストの `git check-ignore -v` と代表パス集合で
突き合わせて確かめた記録(AR-16、受入基準 A-1)。`tests/measurements/test_host_check_logs.py`
がこの記録を機械判定する。

突き合わせは使い捨てのリポジトリで行う。`.gitignore` をそこへ写し、各パスをファイルまたは
ディレクトリとして実体化してから、`--no-index` と `core.excludesFile=/dev/null` を付けて
git に聞く。リポジトリ自身の `.git/info/exclude`・追跡状態・利用者設定が判定に混ざらない
ようにするためで、自前評価器が読むのもリポジトリ直下の `.gitignore` だけなので入力が揃う。

`.gitignore`・代表集合・評価器のどれかを変えたら測り直すこと。古いままの記録はテストが赤にする。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `PYTHONPATH=src python tests/measurements/test_host_check_logs.py gitignore` |
| 実行日時 | {datetime.now().astimezone().isoformat(timespec="seconds")} |
| コミット | `{commit.stdout.strip()}` |
| git | {version} |
| 実行環境 | ホスト({platform.system()} {platform.release()}) |

## 結果

判定の不一致: {mismatches} 件 / {len(repo_rows) + len(synthetic_rows)} 件

{REPO_CASE}

`.gitignore` の digest: `{_digest(repo_gitignore)}`

{_render_table(repo_rows)}

{SYNTHETIC_CASE}

リポジトリの `.gitignore` はディレクトリ形式と拡張子の規則しか使っていない。否定・`**`・
文字クラス・エスケープの扱いを測るため、評価器が扱う規則を一通り踏む合成の
`.gitignore` でも突き合わせた。表のパスは JSON 文字列で書いてある。

```gitignore
{SYNTHETIC_GITIGNORE}```

{_render_table(synthetic_rows)}
"""


def _section(text: str, heading: str) -> str:
    assert heading in text, f"見出しが無い: {heading}"
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def _recorded_rows(section: str) -> list[tuple[str, bool, bool, bool, str]]:
    """表の本体行を (パス, ディレクトリか, git の判定, 評価器の判定, 判定列) で返す。"""
    rows = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 6, f"表の行を読めない: {line}"
        path, kind, git, _source, evaluator, judged = cells
        assert kind in ("dir", "file"), f"種別を読めない: {line}"
        assert {git, evaluator} <= {IGNORED, NOT_IGNORED}, f"判定を読めない: {line}"
        rows.append((json.loads(path.strip("`")), kind == "dir", git == IGNORED, evaluator == IGNORED, judged))
    return rows


def test_gitignore_equivalence_is_total() -> None:
    """A-1: 代表パス集合の全件で、記録された git と自前評価器の判定が一致していること。"""
    assert GITIGNORE_LOG.is_file(), f"実測ログが無い: {GITIGNORE_LOG.relative_to(ROOT)}"
    text = GITIGNORE_LOG.read_text(encoding="utf-8")
    repo_gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    repo_section = _section(text, REPO_CASE)
    assert f"`{_digest(repo_gitignore)}`" in repo_section, "記録後に .gitignore が変わっている — 測り直すこと"
    synthetic_section = _section(text, SYNTHETIC_CASE)
    recorded_gitignore = synthetic_section.split("```gitignore\n", 1)[1].split("```", 1)[0]
    assert recorded_gitignore == SYNTHETIC_GITIGNORE, "記録後に合成 .gitignore が変わっている — 測り直すこと"

    for gitignore, paths, section in (
        (repo_gitignore, REPO_PATHS, repo_section),
        (SYNTHETIC_GITIGNORE, SYNTHETIC_PATHS, synthetic_section),
    ):
        rows = _recorded_rows(section)
        assert [(p, d) for p, d, *_ in rows] == list(paths), "記録のパス集合が代表集合と違う — 測り直すこと"

        mismatched = [p for p, _, git, evaluator, _ in rows if git != evaluator]
        assert not mismatched, f"git と自前評価器の判定が食い違う: {mismatched}"
        assert all(judged == MATCH for *_, judged in rows), "判定列に一致でない行がある"

        rules = parse_gitignore(gitignore)
        stale = [p for p, d, _, evaluator, _ in rows if is_ignored(p, rules, is_dir=d) != evaluator]
        assert not stale, f"記録された自前評価器の判定が今の評価器と違う: {stale}"

        git = _git()
        if git is None:
            continue  # 品質ゲートのサンドボックスに git が無ければ、記録された git の判定を信じる
        live = {v.path: v.git_ignored for v in measure_gitignore_equivalence(git, gitignore, paths)}
        fabricated = [p for p, _, recorded, _, _ in rows if live[p] != recorded]
        assert not fabricated, f"記録された git の判定が、今 git に聞いた結果と違う: {fabricated}"


@pytest.mark.parametrize(
    ("gitignore", "path", "is_dir", "ignored"),
    [
        ("data/\n", "data/deckA/q.jsonl", False, True),
        ("*.tmp\n!keep.tmp\n", "sub/keep.tmp", False, False),
        ("secret/\n!secret/a.txt\n", "secret/a.txt", False, True),
        ("logs/\n", "logs", False, False),
    ],
)
def test_the_git_side_of_the_harness_reads_verdicts(gitignore: str, path: str, is_dir: bool, ignored: bool) -> None:
    """突き合わせの git 側が、否定行・親ディレクトリ・ディレクトリ形式を正しく読み取ること。

    `-v -n` は否定行にマッチしたパスも根拠付きで出す。そこを「無視された」と読むと、
    git と評価器が揃って間違えたときに一致として記録される。
    """
    git = _git()
    if git is None:
        pytest.skip("git が無いので突き合わせの git 側を確かめられない")
    assert _git_check_ignore(git, gitignore, ((path, is_dir),))[path][0] is ignored


def _main(argv: list[str]) -> int:
    git = _git()
    if argv != ["gitignore"] or git is None:
        print("usage: PYTHONPATH=src python tests/measurements/test_host_check_logs.py gitignore", file=sys.stderr)
        return 2
    GITIGNORE_LOG.write_text(render_gitignore_log(git), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
