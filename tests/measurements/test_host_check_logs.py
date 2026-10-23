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

**C-012(R-12 / ADR-012 / ADR-021)**: ブラウザからテストを実施し結果を閲覧した E2E の記録
`docs/test/measurements/ui-e2e-nofm.md` を読む。E2E は5択2正解の問題を含み、その問題で選択が
N−1 件・N+1 件のときは送信できず、ちょうど N 件で送信できたことを確かめる。
node と Next.js はサンドボックスイメージに無いので、E2E はホストで1回走らせる(`run_ui_e2e`)。記録は
`PYTHONPATH=src python tests/measurements/test_host_check_logs.py ui-e2e --node-modules <npm ci した node_modules>`
で作り直す。ブラウザは Playwright が置いた chromium を、既存の `adapters/browser_cdp.CdpSession`
で CDP 越しに操作する(Playwright の Node パッケージを依存に足さないため)。スクリーンショットは
`docs/test/measurements/ui-e2e-nofm/` に書いてコミットする(D-080)。gitignore 対象の `out/` に置くと、
追跡ファイルだけのチェックアウトで走る worktree では実在せず、判定の緑が走る木に依存する。
スクリーンショットが追跡対象になるので、E2E は使い捨ての作業ルートに置いた架空のデッキに対してだけ
走らせる(D-081)。リポジトリの `data/`・`out/` は読まないので、実教材の本文が画像に写る経路は無い。
テストは次のどれかで赤になる:

- 記録された終了コードが 0 でない、または手順のどれかが ok でない(本体)。
- 記録された `web/` の digest が今の `web/` と違う(UI を変えたのに測り直していない記録)。
- 記録の手順・スクリーンショットの並びが、このファイルが宣言する E2E と違う(古い記録)。
- 記録されたスクリーンショットのパスが実在しない、PNG でない、記録した digest と中身が違う。
"""

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from quiz_sift.adapters.browser_cdp import CdpError, CdpSession
from quiz_sift.safety.gitignore_guard import is_ignored, parse_gitignore
from quiz_sift.store.location import store_path

ROOT = Path(__file__).resolve().parent.parent.parent
GITIGNORE_LOG = ROOT / "docs" / "test" / "measurements" / "gitignore-equivalence.md"
UI_E2E_LOG = ROOT / "docs" / "test" / "measurements" / "ui-e2e-nofm.md"
SCREENSHOT_DIR = Path("docs") / "test" / "measurements" / "ui-e2e-nofm"

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
    ("docs/test/measurements/ui-e2e/01-decks.png", False),
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


E2E_DECK = "e2e-deck"
#: 架空の問題(NFR-2 の対象外)。値は(選択肢ラベル, 正解の集合)。q5 は5択2正解(N-of-M)。
E2E_QUESTIONS: dict[str, tuple[str, frozenset[str]]] = {
    "q1": ("ABCD", frozenset("A")),
    "q2": ("ABCD", frozenset("B")),
    "q3": ("ABCD", frozenset("C")),
    "q4": ("ABCD", frozenset("D")),
    "q5": ("ABCDE", frozenset("BD")),
}
E2E_NOFM = "q5"
#: 5択2正解の問題で送信を試す選択。N−1 件・N+1 件は送れず、N 件(正解)で送れる。
E2E_NOFM_UNDER = ("B",)
E2E_NOFM_OVER = ("B", "D", "E")
E2E_STEM_PREFIX = "架空の問題 "
E2E_SUBMIT = "回答する"

#: 事前は全問を先頭から N 個の選択肢で答えて 1/5、事後は全問正解で 5/5 になる。レポートにはその差が出る。
E2E_STEPS = (
    "デッキ一覧を開く",
    "デッキを開いて事前テストを始める",
    "事前テストの全問に先頭から N 個の選択肢で答える",
    "デッキへ戻って事後テストを始める",
    "事後テストの全問に正解で答える",
    "5択2正解の問題は「2 個選択」とチェックボックスで出題され、1件・3件では送れず2件で送れた(2度押しても送信は1回)",
    "事前・事後を選んでレポートを開く",
    "回答がブラウザ内ストレージでなくサーバ側のストアにある",
)
E2E_SCREENSHOTS = (
    "01-decks.png",
    "02-graded.png",
    "03-session-done.png",
    "04-nofm-under.png",
    "05-nofm-over.png",
    "06-nofm-graded.png",
    "07-report.png",
)
E2E_REPORT_TEXTS = ("20.0% (1/5)", "100.0% (5/5)", "+80.0pt")

UI_E2E_HEADINGS = ("## 実行条件", "## 終了コード", "## 手順", "## スクリーンショット")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
OK = "ok"
FAILED = "failed"
NOT_RUN = "not-run"


class E2eFailure(Exception):
    """E2E の手順が期待どおりに進まなかった。"""


@dataclass(frozen=True, slots=True)
class E2eStep:
    name: str
    result: str
    detail: str


@dataclass(frozen=True, slots=True)
class E2eRun:
    exit_code: int
    steps: tuple[E2eStep, ...]
    screenshots: tuple[Path, ...]
    browser: str
    next_build: str
    web_digest: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


WEB_DIGEST_ROW = "web/ の digest"


def _web_digest(root: Path) -> str:
    """`root/web` のうち `web/.gitignore` で無視されないファイルの、相対パスと中身の digest。

    E2E はこの木の `web/` を写して建てるので、記録をこの値で今の UI に結びつける。
    `node_modules/`・`.next/`・`*.tsbuildinfo` のような手元の生成物は UI の版ではないので含めない。
    """
    web = root / "web"
    rules = parse_gitignore((web / ".gitignore").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    pending = [web]
    files = []
    while pending:
        for path in sorted(pending.pop().iterdir()):
            relative = path.relative_to(web).as_posix()
            if is_ignored(relative, rules, is_dir=path.is_dir()):
                continue
            if path.is_dir():
                pending.append(path)
            else:
                files.append((relative, path))
    for relative, path in sorted(files):
        digest.update(relative.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return f"sha256:{digest.hexdigest()}"


def _wait_http(url: str, timeout: float = 60.0) -> bytes:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body: bytes = response.read()
                return body
        except OSError:
            if time.monotonic() > deadline:
                raise E2eFailure(f"{url} が {timeout:.0f} 秒以内に応答しない") from None
            time.sleep(0.3)


@contextmanager
def _spawn(argv: list[str], *, cwd: Path, env: dict[str, str], log: Path) -> Iterator[None]:
    with log.open("wb") as out:
        proc = subprocess.Popen(argv, cwd=cwd, env={**os.environ, **env}, stdout=out, stderr=subprocess.STDOUT)
        try:
            yield
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def _write_e2e_root(root: Path) -> None:
    """`data/` に架空のデッキ1つを持つ作業ルート(tests/server/conftest.py の write_root と同じ形)。"""
    (root / ".gitignore").write_text("data/\nout/\n", encoding="utf-8")
    deck_dir = root / "data" / E2E_DECK
    deck_dir.mkdir(parents=True)
    (deck_dir / "deck.yaml").write_text(f"deck_id: {E2E_DECK}\ntitle: 架空デッキ\nlanguage: ja\n", encoding="utf-8")
    rows = [
        {
            "id": qid,
            "stem": f"{E2E_STEM_PREFIX}{qid}",
            "options": [{"id": label, "text": f"選択肢 {label}"} for label in labels],
            "answers": sorted(answers),
            "explanation": f"解説 {qid}",
        }
        for qid, (labels, answers) in E2E_QUESTIONS.items()
    ]
    (deck_dir / "q.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")


class _Page:
    """1タブを DOM の問い合わせとクリックで操作する。"""

    def __init__(self, session: CdpSession, origin: str) -> None:
        self._session = session
        self.origin = origin

    def goto(self, path: str) -> None:
        self._session.call("Page.navigate", {"url": self.origin + path})

    def eval(self, expression: str) -> object:
        return self._session.evaluate(expression)

    def wait(self, expression: str, what: str, timeout: float = 30.0) -> object:
        deadline = time.monotonic() + timeout
        while True:
            value = self.eval(expression)
            if value:
                return value
            if time.monotonic() > deadline:
                raise E2eFailure(f"{what}が {timeout:.0f} 秒以内に現れない(画面: {self.text()[:200]!r})")
            time.sleep(0.2)

    def text(self) -> str:
        return str(self.eval("document.body ? document.body.innerText : ''"))

    def click(self, tag: str, label: str) -> None:
        """`label` を表示している `tag` 要素が現れるのを待ってクリックする。"""
        found = (
            f"[...document.querySelectorAll({json.dumps(tag)})]"
            f".find((el) => el.textContent.trim() === {json.dumps(label)} && !el.disabled)"
        )
        self.wait(
            f"(() => {{ const el = {found}; if (!el) return false; el.click(); return true; }})()", f"「{label}」"
        )

    def set_checked(self, labels: tuple[str, ...]) -> None:
        """チェックボックスの選択を `labels`(選択肢ラベル)の集合にする。React が拾うようにクリックで切り替える。"""
        wanted = json.dumps([f"選択肢 {label}" for label in labels])
        script = (
            f"(() => {{ const want = new Set({wanted});"
            " for (const l of document.querySelectorAll('label')) {"
            " const box = l.querySelector('input[type=checkbox]');"
            " if (box && box.checked !== want.has(l.textContent.trim())) { box.click(); }"
            " } return true; })()"
        )
        self.eval(script)
        checked = (
            "[...document.querySelectorAll('label')]"
            ".filter((l) => l.querySelector('input[type=checkbox]:checked')).map((l) => l.textContent.trim()).sort()"
        )
        expected = json.dumps(sorted(f"選択肢 {label}" for label in labels), separators=(",", ":"), ensure_ascii=False)
        self.wait(f"JSON.stringify({checked}) === {json.dumps(expected)}", f"選択 {list(labels)}")

    def submit_enabled(self) -> bool:
        """「回答する」ボタンが押せる状態か。ボタンが無ければ E2eFailure。"""
        state = self.eval(
            f"(() => {{ const b = [...document.querySelectorAll('button')]"
            f".find((el) => el.textContent.trim() === {json.dumps(E2E_SUBMIT)});"
            " return b ? (b.disabled ? 'disabled' : 'enabled') : 'missing'; })()"
        )
        if state == "missing":
            raise E2eFailure(f"「{E2E_SUBMIT}」ボタンが無い")
        return state == "enabled"

    def double_submit(self) -> None:
        """「回答する」を再描画を挟まずに2度 click() する(応答待ちの間の2度押し)。"""
        self.eval(
            f"(() => {{ const b = [...document.querySelectorAll('button')]"
            f".find((el) => el.textContent.trim() === {json.dumps(E2E_SUBMIT)});"
            " b.click(); b.click(); return true; })()"
        )

    def force_submit(self) -> None:
        """押せない状態の「回答する」も DOM から click() する。押せないなら何も送られないはず。"""
        self.eval(
            f"[...document.querySelectorAll('button')].find((el) => el.textContent.trim() === {json.dumps(E2E_SUBMIT)})"
            "?.click()"
        )

    def path(self) -> str:
        return str(self.eval("location.pathname"))

    def screenshot(self, target: Path) -> Path:
        data = self._session.call("Page.captureScreenshot", {"format": "png"}).get("data")
        if not isinstance(data, str):
            raise E2eFailure("Page.captureScreenshot が画像を返さない")
        target.write_bytes(base64.b64decode(data))
        return target


def _checkboxes(page: _Page) -> int:
    return int(str(page.eval("document.querySelectorAll('input[type=checkbox]').length")))


def _try_refused(page: _Page, labels: tuple[str, ...], shot: Path, answered: Callable[[], int]) -> str:
    """`labels` を選んだ状態で送信できない(ボタンが押せず、押しても記録が増えない)ことを確かめる。"""
    before = answered()
    page.set_checked(labels)
    if page.submit_enabled():
        raise E2eFailure(f"{len(labels)} 件の選択で「{E2E_SUBMIT}」が押せる")
    page.screenshot(shot)
    page.force_submit()
    time.sleep(1.0)
    if page.eval("document.querySelector('[role=status]') !== null") or answered() != before:
        raise E2eFailure(f"{len(labels)} 件の選択で回答が送られた")
    return f"{len(labels)} 件: 送信不可"


def _answer_all(
    page: _Page,
    choose: Callable[[str], frozenset[str]],
    graded_shot: Path | None,
    nofm: tuple[Path, Path, Path, Callable[[], int]] | None = None,
) -> tuple[list[str], str]:
    """セッション画面で全問に答え、各問の採点表示(正解 / 不正解)を問題の順に返す。

    正解1件の問題は選択肢のボタンを1クリックで送り、チェックボックスが無いことを確かめる。
    複数正解の問題は「N 個選択」の表示とチェックボックスを確かめ、N 件を選んで「回答する」で送る。
    `nofm`(N−1 件・N+1 件・N 件のスクリーンショットと回答数の読み出し)を渡すと、5択2正解の問題で
    N−1 件・N+1 件では送れないことを先に確かめ、その経過を2つ目の戻り値で返す。
    """
    verdicts = []
    nofm_detail = ""
    for _ in E2E_QUESTIONS:
        stem = str(page.wait("document.querySelector('h2')?.textContent", "問題文"))
        qid = stem.removeprefix(E2E_STEM_PREFIX)
        if qid not in E2E_QUESTIONS:
            raise E2eFailure(f"知らない問題が出た: {stem!r}")
        labels, answers = E2E_QUESTIONS[qid]
        chosen = choose(qid)
        if len(answers) == 1:
            page.wait("document.querySelector('ol button') !== null", "選択肢のボタン")
            if _checkboxes(page) != 0:
                raise E2eFailure(f"正解1件の {qid} にチェックボックスがある")
            (label,) = chosen
            page.click("button", f"選択肢 {label}")
        else:
            page.wait(f"document.body?.innerText.includes('{len(answers)} 個選択')", f"「{len(answers)} 個選択」の表示")
            if _checkboxes(page) != len(labels):
                raise E2eFailure(f"{qid} のチェックボックスが {_checkboxes(page)} 個(選択肢は {len(labels)} 個)")
            if nofm is not None and qid == E2E_NOFM:
                under, over, graded, answered = nofm
                refused = [_try_refused(page, E2E_NOFM_UNDER, under, answered)]
                refused.append(_try_refused(page, E2E_NOFM_OVER, over, answered))
                page.set_checked(tuple(sorted(chosen)))
                if not page.submit_enabled():
                    raise E2eFailure(f"{len(chosen)} 件の選択で「{E2E_SUBMIT}」が押せない")
                before = answered()
                page.double_submit()
                page.wait("document.querySelector('[role=status]') !== null", "採点結果")
                time.sleep(1.0)
                alert = page.eval("document.querySelector('[role=alert]')?.textContent ?? ''")
                if alert or answered() != before + 1:
                    raise E2eFailure(f"2度押しで回答数が {answered() - before} 件増えた(表示: {alert!r})")
                page.screenshot(graded)
                nofm_detail = f"「{len(answers)} 個選択」・チェックボックス {len(labels)} 個、" + "、".join(
                    [*refused, f"{len(chosen)} 件: 2度押しで送信して記録は1件"]
                )
            else:
                page.set_checked(tuple(sorted(chosen)))
                page.click("button", E2E_SUBMIT)
        verdict = str(page.wait("document.querySelector('[role=status]')?.textContent", "採点結果"))
        expected = "正解" if chosen == answers else "不正解"
        if verdict != expected:
            raise E2eFailure(f"{qid} の採点表示が {verdict!r}(期待は {expected!r})")
        verdicts.append(f"{qid}:{verdict}")
        if graded_shot is not None:
            page.screenshot(graded_shot)
            graded_shot = None
        page.click("button", "次へ")
        page.wait(
            f"(() => {{ const h = document.querySelector('h2');"
            f" return !document.querySelector('[role=status]') && (!h || h.textContent !== {json.dumps(stem)}); }})()",
            "次の問題",
        )
    page.wait("document.body?.innerText.includes('すべて回答済み')", "全問回答済みの表示")
    return verdicts, nofm_detail


def _start_session(page: _Page) -> str:
    page.click("button", "テストを始める")
    path = str(page.wait("location.pathname.includes('/sessions/') && location.pathname", "セッション画面"))
    return path.rsplit("/", 1)[1]


def _select_sessions(page: _Page, pre: str, post: str) -> None:
    """React が制御する select に値を入れる。value を直に書くだけでは React が変更を拾わない。"""
    page.wait("document.querySelectorAll('select option').length >= 6", "セッションの選択肢")
    script = (
        "(() => { const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;"
        " const [pre, post] = document.querySelectorAll('select');"
        f" for (const [el, v] of [[pre, {json.dumps(pre)}], [post, {json.dumps(post)}]]) {{"
        " set.call(el, v); el.dispatchEvent(new Event('change', { bubbles: true })); } return true; })()"
    )
    page.eval(script)


def _find_chrome() -> Path | None:
    candidates = sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
    return candidates[-1] if candidates else None


def _build_web(node_modules: Path, target: Path, api: str) -> str:
    """この木の `web/` を `target` に写し、`api` へ転送する設定で `next build` して BUILD_ID を返す。

    `next.config.mjs` の rewrites は `next build` の時点で routes-manifest に焼き込まれ、
    `next start` 時の `QUIZ_SIFT_API` は効かない。既定の 8765 は `rein ui` と衝突しうるので、
    E2E ごとに空きポートの API へ向けて建て直す。Turbopack はプロジェクト外を指す
    node_modules のシンボリックリンクを拒むので、node_modules も実体で写す。
    """
    shutil.copytree(ROOT / "web", target, ignore=shutil.ignore_patterns("node_modules", ".next"))
    shutil.copytree(node_modules, target / "node_modules", symlinks=True)
    done = subprocess.run(
        ["node", str(target / "node_modules" / "next" / "dist" / "bin" / "next"), "build"],
        cwd=target,
        env={**os.environ, "QUIZ_SIFT_API": api},
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise E2eFailure(f"next build が {done.returncode} で終わった: {done.stdout[-500:]}{done.stderr[-500:]}")
    return (target / ".next" / "BUILD_ID").read_text(encoding="utf-8").strip()


def run_ui_e2e(node_modules: Path, chrome: Path, shots: Path) -> E2eRun:
    """API・Next.js・chromium を立て、ブラウザからテストの実施とレポートの閲覧を通しで行う。

    どこかの手順で止まったら、その手順を failed、残りを not-run として終了コード 1 を返す。
    """
    shots.mkdir(parents=True, exist_ok=True)
    for stale in shots.glob("*.png"):
        stale.unlink()
    steps: list[E2eStep] = []
    taken: list[Path] = []
    browser = next_build = "-"
    web_digest = _web_digest(ROOT)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "root"
        root.mkdir()
        _write_e2e_root(root)
        api_port, web_port, cdp_port = _free_port(), _free_port(), _free_port()
        api = f"http://127.0.0.1:{api_port}"
        pending = list(E2E_STEPS)
        web = work / "web"
        try:
            next_build = _build_web(node_modules, web, api)
            with (
                _spawn(
                    [sys.executable, "-m", "quiz_sift.server", "--root", str(root), "--port", str(api_port)],
                    cwd=root,
                    env={"PYTHONPATH": str(ROOT / "src")},
                    log=work / "api.log",
                ),
                _spawn(
                    ["node", str(web / "node_modules" / "next" / "dist" / "bin" / "next"), "start"]
                    + ["--port", str(web_port), "--hostname", "127.0.0.1"],
                    cwd=web,
                    env={"QUIZ_SIFT_API": api},
                    log=work / "next.log",
                ),
                _spawn(
                    [str(chrome), "--headless=new", f"--remote-debugging-port={cdp_port}"]
                    + [f"--user-data-dir={work / 'chrome'}", "--no-first-run", "--no-default-browser-check"]
                    + ["--window-size=1280,900", "about:blank"],
                    cwd=work,
                    env={},
                    log=work / "chrome.log",
                ),
            ):
                _wait_http(f"{api}/api/decks")
                _wait_http(f"http://127.0.0.1:{web_port}/")
                cdp = f"http://127.0.0.1:{cdp_port}"
                browser = str(json.loads(_wait_http(f"{cdp}/json/version"))["Browser"])
                request = urllib.request.Request(f"{cdp}/json/new?about:blank", method="PUT")
                with urllib.request.urlopen(request, timeout=10) as response:
                    ws_url = str(json.loads(response.read())["webSocketDebuggerUrl"])
                with CdpSession(ws_url, timeout=60) as session:
                    page = _Page(session, f"http://127.0.0.1:{web_port}")

                    def step(detail: str) -> None:
                        steps.append(E2eStep(pending.pop(0), OK, detail))

                    page.goto("/")
                    page.wait("document.body?.innerText.includes('架空デッキ')", "デッキ一覧")
                    taken.append(page.screenshot(shots / E2E_SCREENSHOTS[0]))
                    step("一覧に「架空デッキ」が出た")

                    page.click("a", "架空デッキ")
                    page.wait(f"document.body?.innerText.includes('{len(E2E_QUESTIONS)} 問')", "デッキ画面")
                    pre = _start_session(page)
                    step(f"セッション {pre} の画面へ遷移した")

                    graded = shots / E2E_SCREENSHOTS[1]
                    verdicts, _ = _answer_all(
                        page, lambda qid: frozenset(E2E_QUESTIONS[qid][0][: len(E2E_QUESTIONS[qid][1])]), graded
                    )
                    taken.append(graded)
                    taken.append(page.screenshot(shots / E2E_SCREENSHOTS[2]))
                    step("採点表示 " + ", ".join(verdicts))

                    page.click("a", "デッキへ戻る")
                    page.wait("location.pathname.endsWith('/decks/" + E2E_DECK + "')", "デッキ画面")
                    post = _start_session(page)
                    step(f"セッション {post} の画面へ遷移した")

                    def answered_in_post() -> int:
                        listed = json.loads(_wait_http(f"{api}/api/decks/{E2E_DECK}/sessions"))
                        return sum(int(s["answered"]) for s in listed if s["session_id"] == post)

                    nofm_shots = (shots / E2E_SCREENSHOTS[3], shots / E2E_SCREENSHOTS[4], shots / E2E_SCREENSHOTS[5])
                    verdicts, nofm_detail = _answer_all(
                        page, lambda qid: E2E_QUESTIONS[qid][1], None, (*nofm_shots, answered_in_post)
                    )
                    taken.extend(nofm_shots)
                    step("採点表示 " + ", ".join(verdicts))
                    step(nofm_detail)

                    page.click("a", "デッキへ戻る")
                    _select_sessions(page, pre, post)
                    page.click("a", "レポートを見る")
                    page.wait("document.querySelector('table') !== null", "レポートの表")
                    missing = [t for t in E2E_REPORT_TEXTS if t not in page.text()]
                    if missing:
                        raise E2eFailure(f"レポートに {missing} が無い(画面: {page.text()[:300]!r})")
                    taken.append(page.screenshot(shots / E2E_SCREENSHOTS[6]))
                    step("レポートに " + ", ".join(E2E_REPORT_TEXTS) + " が出た")

                    in_browser = page.eval("localStorage.length + sessionStorage.length")
                    if in_browser != 0:
                        raise E2eFailure(f"ブラウザ内ストレージに {in_browser} 件ある")
                    answered = {
                        str(s["session_id"]): int(s["answered"])
                        for s in json.loads(_wait_http(f"{api}/api/decks/{E2E_DECK}/sessions"))
                    }
                    if answered != {pre: len(E2E_QUESTIONS), post: len(E2E_QUESTIONS)}:
                        raise E2eFailure(f"API が返す回答数が {answered}")
                    if not store_path(root).is_file():
                        raise E2eFailure(f"学習記録ストア {store_path(root).relative_to(root)} が無い")
                    step(
                        f"localStorage + sessionStorage = 0 件、API の回答数 {answered}、"
                        f"ストア {store_path(root).relative_to(root)} が存在"
                    )
        except (E2eFailure, CdpError, OSError, ValueError, KeyError) as failure:
            steps.append(E2eStep(pending.pop(0), FAILED, f"{type(failure).__name__}: {failure}"))
            steps.extend(E2eStep(name, NOT_RUN, "-") for name in pending)
    exit_code = 0 if all(s.result == OK for s in steps) and len(steps) == len(E2E_STEPS) else 1
    return E2eRun(exit_code, tuple(steps), tuple(taken), browser, next_build, web_digest)


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def render_ui_e2e_log(run: E2eRun, node_modules: Path, argv: list[str], commit: str, root: Path = ROOT) -> str:
    next_version = json.loads((node_modules / "next" / "package.json").read_text(encoding="utf-8"))["version"]
    step_rows = "\n".join(
        f"| {i} | {s.name} | {s.result} | {_cell(s.detail)} |" for i, s in enumerate(run.steps, start=1)
    )
    shot_rows = "\n".join(
        f"| `{p.relative_to(root).as_posix()}` | `sha256:{hashlib.sha256(p.read_bytes()).hexdigest()}` |"
        for p in run.screenshots
    )
    return f"""# 実測ログ: ブラウザからのテスト実施と結果閲覧(E2E)

R-12(C-012)の「ローカル起動した Web UI からテストを実施し、結果を閲覧でき、N-of-M の問題は
ちょうど N 件のときだけ送れる」ことを、実際のブラウザで通しで操作して確かめた記録(ADR-012)。
node と Next.js はサンドボックスイメージに無いので、ホストで1回走らせた。
`tests/measurements/test_host_check_logs.py` がこの記録を機械判定する。

ローカル API(`python -m quiz_sift.server`)は使い捨ての作業ルートに空きポートで立て、架空の5問の
デッキ(NFR-2 の対象外。q5 は5択2正解)を出題させた。5択2正解の問題では、選択が1件・3件のとき
「回答する」が押せず、押しても記録が増えないこと、2件で送れたことを確かめた(ADR-021)。
Next.js はこの木の `web/` を一時ディレクトリへ写し、
`QUIZ_SIFT_API` をその API に向けて `next build` してから `next start` で立てた(rewrites は
ビルド時に固定されるため)。
ブラウザは Playwright が置いた chromium をヘッドレスで起動し、`adapters/browser_cdp.CdpSession`
で CDP 越しにクリックした。スクリーンショットは `{SCREENSHOT_DIR.as_posix()}/` にコミットしてある(D-080)。
画像が追跡対象になるので、E2E は使い捨ての作業ルートに置いた架空のデッキに対してだけ走らせる(D-081)。
リポジトリの `data/`・`out/` は読まない。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `{" ".join(argv)}` |
| 実行日時 | {datetime.now().astimezone().isoformat(timespec="seconds")} |
| コミット | `{commit}` |
| ブラウザ | {run.browser} |
| Next.js | {next_version}(この木の `web/` を写して `next build`、BUILD_ID `{run.next_build}`) |
| {WEB_DIGEST_ROW} | `{run.web_digest}` |
| 実行環境 | ホスト({platform.system()} {platform.release()}) |

## 終了コード

`{run.exit_code}`

## 手順

| # | 手順 | 結果 | 確認したこと |
|---|---|---|---|
{step_rows}

## スクリーンショット

| パス | digest |
|---|---|
{shot_rows}
"""


def _table_body(section: str) -> list[list[str]]:
    rows = [line for line in section.splitlines() if line.startswith("|")]
    return [[c.strip().strip("`") for c in line.strip().strip("|").split("|")] for line in rows[2:]]


def check_ui_e2e_log(text: str, root: Path) -> None:
    """記録が終了コード 0・全手順 ok で、スクリーンショットが実在して記録と一致すること。"""
    for heading in UI_E2E_HEADINGS:
        assert heading in text, f"規定の見出しが無い: {heading}"
    conditions = {row[0]: row[1] for row in _table_body(_section(text, "## 実行条件"))}
    assert conditions.get(WEB_DIGEST_ROW) == _web_digest(root), "記録後に web/ が変わっている — 測り直すこと"
    exit_code = _section(text, "## 終了コード").strip().strip("`")
    assert exit_code == "0", f"記録された E2E の終了コードが 0 でない: {exit_code}"

    steps = _table_body(_section(text, "## 手順"))
    assert [row[1] for row in steps] == list(E2E_STEPS), "記録の手順が宣言した E2E と違う — 測り直すこと"
    failed = [row[1] for row in steps if row[2] != OK]
    assert not failed, f"ok でない手順がある: {failed}"

    shots = _table_body(_section(text, "## スクリーンショット"))
    names = [Path(row[0]).name for row in shots]
    assert names == list(E2E_SCREENSHOTS), f"記録のスクリーンショットが宣言と違う: {names}"
    for relative, digest in shots:
        path = root / relative
        assert path.is_file(), f"スクリーンショットのパスが実在しない: {relative}"
        data = path.read_bytes()
        assert data.startswith(PNG_MAGIC), f"PNG ではない: {relative}"
        assert digest == f"sha256:{hashlib.sha256(data).hexdigest()}", f"記録後に差し替わっている: {relative}"


def test_ui_e2e_nofm_record_shows_exactly_n_submission() -> None:
    """記録された E2E(5択2正解で N−1・N+1 件は送れず N 件で送れた手順を含む)の終了コードが 0 で、
    全手順が ok、スクリーンショットのパスが実在して記録と一致すること。"""
    assert UI_E2E_LOG.is_file(), f"実測ログが無い: {UI_E2E_LOG.relative_to(ROOT)}"
    check_ui_e2e_log(UI_E2E_LOG.read_text(encoding="utf-8"), ROOT)


def _good_e2e_log(root: Path) -> str:
    (root / "web" / "app").mkdir(parents=True)
    (root / "web" / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (root / "web" / "app" / "page.tsx").write_text("export default 1;\n", encoding="utf-8")
    shots_dir = root / SCREENSHOT_DIR
    shots_dir.mkdir(parents=True)
    shots = []
    for name in E2E_SCREENSHOTS:
        (shots_dir / name).write_bytes(PNG_MAGIC + name.encode())
        shots.append(shots_dir / name)
    run = E2eRun(0, tuple(E2eStep(name, OK, "-") for name in E2E_STEPS), tuple(shots), "-", "-", _web_digest(root))
    node_modules = root / "node_modules"
    (node_modules / "next").mkdir(parents=True)
    (node_modules / "next" / "package.json").write_text('{"version": "0"}', encoding="utf-8")
    return render_ui_e2e_log(run, node_modules, ["e2e"], "-", root)


@pytest.mark.parametrize(
    "breaks", ["exit", "failed-step", "missing-step", "missing-shot", "replaced-shot", "changed-web"]
)
def test_ui_e2e_check_goes_red(tmp_path: Path, breaks: str) -> None:
    """E2E の記録の判定が、非0終了・失敗した / 欠けた手順・実在しない / 差し替わったスクリーンショット・
    記録後に変わった web/ で赤になる。"""
    text = _good_e2e_log(tmp_path)
    check_ui_e2e_log(text, tmp_path)
    shots = tmp_path / SCREENSHOT_DIR
    if breaks == "exit":
        text = text.replace("\n`0`\n", "\n`1`\n")
    elif breaks == "failed-step":
        text = text.replace(f" | {OK} |", f" | {FAILED} |", 1)
    elif breaks == "missing-step":
        text = text.replace(f"| 1 | {E2E_STEPS[0]} | {OK} | - |\n", "")
    elif breaks == "changed-web":
        (tmp_path / "web" / "app" / "page.tsx").write_text("export default 2;\n", encoding="utf-8")
    elif breaks == "missing-shot":
        (shots / E2E_SCREENSHOTS[0]).unlink()
    else:
        (shots / E2E_SCREENSHOTS[1]).write_bytes(PNG_MAGIC)
    with pytest.raises(AssertionError):
        check_ui_e2e_log(text, tmp_path)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tests/measurements/test_host_check_logs.py")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("gitignore", help="gitignore 等価性を測り直す(A-1)")
    e2e = commands.add_parser("ui-e2e", help="ブラウザの E2E を走らせて記録する(C-012)")
    e2e.add_argument(
        "--node-modules", type=Path, default=ROOT / "web" / "node_modules", help="web/ で npm ci した node_modules"
    )
    e2e.add_argument("--chrome", type=Path, default=_find_chrome(), help="chromium の実行ファイル")
    args = parser.parse_args(argv)
    if args.command == "gitignore":
        git = _git()
        if git is None:
            print("git が見つからない", file=sys.stderr)
            return 2
        GITIGNORE_LOG.write_text(render_gitignore_log(git), encoding="utf-8")
        return 0
    node_modules: Path = args.node_modules.resolve()
    if args.chrome is None or not (node_modules / "next").is_dir():
        print("chromium と、npm ci 済みの --node-modules が要る", file=sys.stderr)
        return 2
    run = run_ui_e2e(node_modules, args.chrome, ROOT / SCREENSHOT_DIR)
    command = ["PYTHONPATH=src", "python", "tests/measurements/test_host_check_logs.py", *argv]
    commit = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    UI_E2E_LOG.write_text(render_ui_e2e_log(run, node_modules, command, commit.stdout.strip()), encoding="utf-8")
    return run.exit_code


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
