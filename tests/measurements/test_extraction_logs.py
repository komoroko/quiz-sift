"""Udemy からの実取得とゴールデンセット実測のログの検査(T-019 / C-002 / R-2)。

実教材は NFR-2 によりコミットできず、サンドボックスからも読めない(D-048)。そのため証拠は
**ホストで取った実測ログ**で、このテストはログの中身を機械判定する。取得もこのファイルから行う:

- `PYTHONPATH=src python tests/measurements/test_extraction_logs.py fetch --out-root <リポジトリ>`
  ログイン済みの Chrome(CDP)で結果ページを1回だけ取得し、生 HTML・問題 JSONL・根拠レポートを
  `<リポジトリ>/out/udemy/` に書き、`docs/test/measurements/extraction-run.md` を書く。取得そのものは
  子プロセス(`fetch-run`)で走らせ、その argv・標準出力・標準エラー・終了コードを逐語で記録する。
- `... golden-candidates --out-root <リポジトリ>` は保存済みの生 HTML から、問題ごとの受験者の選択・
  抽出した正解・根拠の要素種別を出す(本文は出さない)。ゴールデンセットを選ぶための一覧で、再取得はしない。
- `... golden --out-root <リポジトリ> --label p001-q003=B ... --label-source <経路>` は正解ラベル10件と
  その読み取り経路を受け取り、保存済みの生 HTML から抽出し直した正解と突き合わせて
  `docs/test/measurements/golden-set.md` を書く。ラベルは外から渡すもので、このファイルは抽出結果から
  埋めない。誰が何を見てラベルを読んだかは実行ごとに違うので、定型文にせず `--label-source` を逐語で残す。

どちらのログも本文を持たない(問題 ID・選択肢記号・件数・要素パスだけ)。

判定(各受入基準に1本):

- A-1: 抽出ログに規定の見出しがあり、終了コード 0、逐語の標準出力の `extracted=` が 100〜200。
- A-2: ゴールデンセットが10件、受験者の選択と人手ラベルが異なる行が3件以上、行から数え直した
  一致数が 10/10 で、記録された一致率もそれと同じ。
- A-3: ゴールデンセットの全行に `selector_kind` があり、その集合の要素数が 1。
"""

import argparse
import hashlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
EXTRACTION_LOG = ROOT / "docs" / "test" / "measurements" / "extraction-run.md"
GOLDEN_LOG = ROOT / "docs" / "test" / "measurements" / "golden-set.md"

DEFAULT_URL = "https://www.udemy.com/course/awsgenerative-ai-developer-professional-aip-c01-j/learn/quiz/7600618/result/1802860553"
"""取得対象の演習テストの結果ページ。T-019 の承認が覆うのはこの1ページ(100〜200問)の取得まで。"""
DECK_DIR = Path("out") / "udemy" / "aip-c01"
RAW_PAGE = DECK_DIR / "raw" / "page-001.html"
QUESTIONS = DECK_DIR / "questions.jsonl"
REPORT = DECK_DIR / "extract-report.json"

EXTRACTION_HEADINGS = ("## 実行条件", "## argv", "## 終了コード", "## 標準出力", "## 標準エラー")
GOLDEN_HEADINGS = ("## 実行条件", "## 一致率", "## ゴールデンセット")
MIN_QUESTIONS, MAX_QUESTIONS = 100, 200
GOLDEN_SIZE = 10
MIN_USER_WRONG = 3
LABELS = frozenset("ABCD")
NO_SELECTION = "-"
"""受験者が何も選ばなかった問題の「受験者の選択」欄。"""


def _section(text: str, heading: str) -> str:
    """`heading` の直後から次の `## ` 見出しまでを返す。"""
    assert heading in text, f"規定の見出しが無い: {heading}"
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def _code_block(section: str) -> str:
    """節の中の最初のコードブロックの中身。"""
    match = re.search(r"^```text\n(.*?)^```$", section, re.MULTILINE | re.DOTALL)
    assert match is not None, "コードブロックが無い"
    return match.group(1)


def _table_value(section: str, key: str) -> str:
    match = re.search(rf"^\| {re.escape(key)} \| (.+?) \|$", section, re.MULTILINE)
    assert match is not None, f"実行条件に {key} が無い"
    return match.group(1).strip().strip("`")


def _check_conditions(text: str) -> None:
    conditions = _section(text, "## 実行条件")
    datetime.fromisoformat(_table_value(conditions, "実行日時"))
    assert re.fullmatch(r"[0-9a-f]{7,40}", _table_value(conditions, "コミット")), "コミット ID が無い"


# ---- A-1: 抽出ログ ----


@dataclass(frozen=True)
class FetchRun:
    """取得を走らせた子プロセス1回。"""

    argv: list[str]
    started_at: str
    exit_code: int
    stdout: str
    stderr: str


def render_extraction_log(run: FetchRun, commit: str) -> str:
    argv = "\n".join(run.argv)
    return f"""# 実測ログ: Udemy からの実取得

R-2(C-002)の「Udemy のページからヘッドレスブラウザで抽出すると、成功件数と失敗箇所が報告される」
ことを、実物の結果ページに対して1回走らせた記録(受入基準 A-1)。取得はログイン済みの Chrome に
CDP で接続して行った(ADR-007、`adapters/browser_cdp.fetch`)。Udemy へのアクセスは戻せないので、
`rein approve T-019` の承認のもとで1回だけ実行した。

取得は `tests/measurements/test_extraction_logs.py fetch` が子プロセス(`fetch-run`)として走らせ、
その argv・標準出力・標準エラー・終了コードを加工せずに転記している。生 HTML・問題 JSONL・根拠レポートは
gitignore 対象の `out/udemy/` にあり、ここには本文を載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| 実行日時 | {run.started_at} |
| コミット | `{commit}` |
| 対象 | 演習テストの結果ページ1枚 |

## argv

```text
{argv}
```

## 終了コード

`{run.exit_code}`

## 標準出力

```text
{run.stdout}```

## 標準エラー

```text
{run.stderr}```
"""


def recorded_extracted_count(text: str) -> int:
    """抽出ログから、逐語の標準出力に記録された取得件数を読む。ログとして読めなければ AssertionError。"""
    for heading in EXTRACTION_HEADINGS:
        _section(text, heading)
    _check_conditions(text)
    assert _code_block(_section(text, "## argv")).strip(), "argv が空"
    assert _section(text, "## 終了コード").strip() == "`0`", "取得が終了コード 0 で終わっていない"
    stdout = _code_block(_section(text, "## 標準出力"))
    counts = re.findall(r"^extracted=(\d+)$", stdout, re.MULTILINE)
    assert len(counts) == 1, f"標準出力に extracted= がちょうど1行ない: {counts}"
    return int(counts[0])


def check_extraction_log(text: str) -> None:
    count = recorded_extracted_count(text)
    assert MIN_QUESTIONS <= count <= MAX_QUESTIONS, f"取得件数が {MIN_QUESTIONS}〜{MAX_QUESTIONS} に入らない: {count}"


def test_extraction_run_records_100_to_200_questions() -> None:
    """A-1: 実取得のログが規定の見出しで残り、記録された取得件数が 100〜200。"""
    assert EXTRACTION_LOG.is_file(), f"実測ログが無い: {EXTRACTION_LOG.relative_to(ROOT)}"
    check_extraction_log(EXTRACTION_LOG.read_text(encoding="utf-8"))


def _synthetic_extraction_log(count: int, exit_code: int = 0) -> str:
    stdout = f"question_blocks={count}\nextracted={count}\nfailed=0\n"
    run = FetchRun(["python", "fetch-run"], "2026-09-24T00:00:00+09:00", exit_code, stdout, "")
    return render_extraction_log(run, "0123456789ab")


@pytest.mark.parametrize("count", [MIN_QUESTIONS, MAX_QUESTIONS])
def test_extraction_check_accepts_the_bounds(count: int) -> None:
    check_extraction_log(_synthetic_extraction_log(count))


@pytest.mark.parametrize("breaks", ["too-few", "too-many", "exit", "no-count", "no-heading"])
def test_extraction_check_goes_red(breaks: str) -> None:
    """A-1 の判定が、範囲外の件数・非0終了・件数の欠落・見出しの欠落で赤になること。"""
    text = {
        "too-few": _synthetic_extraction_log(MIN_QUESTIONS - 1),
        "too-many": _synthetic_extraction_log(MAX_QUESTIONS + 1),
        "exit": _synthetic_extraction_log(150, exit_code=1),
        "no-count": _synthetic_extraction_log(150).replace("extracted=150\n", ""),
        "no-heading": _synthetic_extraction_log(150).replace("## 標準エラー", "## stderr"),
    }[breaks]
    with pytest.raises(AssertionError):
        check_extraction_log(text)


# ---- A-2 / A-3: ゴールデンセット ----


@dataclass(frozen=True)
class GoldenRow:
    question_id: str
    user_choice: str
    human_label: str
    extracted_label: str
    selector_kind: str

    @property
    def agrees(self) -> bool:
        return self.human_label == self.extracted_label


def render_golden_log(rows: list[GoldenRow], raw_digest: str, argv: list[str], commit: str, label_source: str) -> str:
    agreed = sum(row.agrees for row in rows)
    table = "\n".join(
        f"| {row.question_id} | {row.user_choice} | {row.human_label} | {row.extracted_label} "
        f"| {row.selector_kind} | {'一致' if row.agrees else '不一致'} |"
        for row in rows
    )
    rate = f"{agreed}/{len(rows)}" if rows else "0/0"
    return f"""# 実測ログ: ゴールデンセットの正解ラベル一致率

R-2(C-002)の「ゴールデンセット10件に対する正解ラベル一致率が 100% で、正解判定の根拠の要素種別が
全問で同一である」ことの実測(受入基準 A-2 / A-3、D-048)。表の「人手ラベル」列は外から渡した正解ラベルで、
誰が何を見て読んだかは実行条件の「ラベルの出所」に記す。抽出ラベルは `extract.udemy.extract` が保存済みの
生 HTML から同定したもの。
問題 ID の `p001-qNNN` は結果ページの NNN 問目。本文は載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `{" ".join(argv)}` |
| 実行日時 | {datetime.now().astimezone().isoformat(timespec="seconds")} |
| コミット | `{commit}` |
| 生 HTML | `{RAW_PAGE.as_posix()}` (`{raw_digest}`) |
| ラベルの出所 | {label_source} |

## 一致率

`{rate}`

## ゴールデンセット

| 問題 ID | 受験者の選択 | 人手ラベル | 抽出ラベル | selector_kind | 判定 |
|---|---|---|---|---|---|
{table}
"""


def recorded_golden_rows(text: str) -> list[GoldenRow]:
    """ゴールデンセットのログから行を読む。ログとして読めなければ AssertionError。"""
    for heading in GOLDEN_HEADINGS:
        _section(text, heading)
    _check_conditions(text)
    assert _table_value(_section(text, "## 実行条件"), "ラベルの出所"), "ラベルの出所が空"
    rows = []
    for line in _section(text, "## ゴールデンセット").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 6 or not re.fullmatch(r"p\d{3}-q\d{3}", cells[0]):
            continue
        rows.append(GoldenRow(*cells[:5]))
    return rows


def check_golden_agreement(text: str) -> None:
    rows = recorded_golden_rows(text)
    assert len(rows) == GOLDEN_SIZE, f"ゴールデンセットが {GOLDEN_SIZE} 件でない: {len(rows)} 件"
    assert len({row.question_id for row in rows}) == GOLDEN_SIZE, "問題 ID が重複している"
    for row in rows:
        assert {row.human_label, row.extracted_label} <= LABELS, f"ラベルが A〜D でない: {row}"
        assert row.user_choice in LABELS | {NO_SELECTION}, f"受験者の選択が読めない: {row}"
    user_wrong = sum(row.user_choice != row.human_label for row in rows)
    assert user_wrong >= MIN_USER_WRONG, f"受験者の選択と正解が異なる問題が {MIN_USER_WRONG} 件未満: {user_wrong}"
    agreed = sum(row.agrees for row in rows)
    assert _section(text, "## 一致率").strip() == f"`{agreed}/{len(rows)}`", "記録された一致率が行と合わない"
    assert agreed == GOLDEN_SIZE, f"一致率が 100% でない: {agreed}/{GOLDEN_SIZE}"


def check_golden_selector_kind(text: str) -> None:
    from quiz_sift.extract.evidence import SelectorKind

    rows = recorded_golden_rows(text)
    assert len(rows) == GOLDEN_SIZE, f"ゴールデンセットが {GOLDEN_SIZE} 件でない: {len(rows)} 件"
    known = {kind.value for kind in SelectorKind}
    kinds = [row.selector_kind for row in rows]
    assert all(kind in known for kind in kinds), f"selector_kind の記録が欠けているか読めない: {kinds}"
    assert len(set(kinds)) == 1, f"根拠の要素種別が全問で同一でない: {sorted(set(kinds))}"


def _golden_text() -> str:
    assert GOLDEN_LOG.is_file(), f"実測ログが無い: {GOLDEN_LOG.relative_to(ROOT)}"
    return GOLDEN_LOG.read_text(encoding="utf-8")


def test_golden_set_records_full_agreement() -> None:
    """A-2: ゴールデンセット10件(受験者の誤答3件以上)の一致率が 10/10 と記録されている。"""
    check_golden_agreement(_golden_text())


def test_golden_set_selector_kind_is_uniform() -> None:
    """A-3: ゴールデンセットの全行に selector_kind があり、その集合の要素数が 1。"""
    check_golden_selector_kind(_golden_text())


def test_golden_log_uses_the_same_page_as_the_extraction_run() -> None:
    """ゴールデンセットの抽出ラベルが、A-1 で取得したのと同じ生 HTML から出ていること。"""
    golden = _table_value(_section(_golden_text(), "## 実行条件"), "生 HTML")
    stdout = _code_block(_section(EXTRACTION_LOG.read_text(encoding="utf-8"), "## 標準出力"))
    digest = re.search(r"\(`(sha256:[0-9a-f]{64})`\)$", golden)
    assert digest is not None, f"生 HTML の digest が無い: {golden}"
    assert f"raw_digest={digest.group(1)}" in stdout.splitlines(), "取得ログと別の生 HTML から抽出している"


def _good_rows() -> list[GoldenRow]:
    wrong = [GoldenRow(f"p001-q{n:03d}", "A", "B", "B", "correct-mark") for n in range(1, 4)]
    right = [GoldenRow(f"p001-q{n:03d}", "C", "C", "C", "correct-mark") for n in range(4, 11)]
    return wrong + right


def _synthetic_golden_log(rows: list[GoldenRow]) -> str:
    return render_golden_log(rows, "sha256:" + "0" * 64, ["golden"], "0123456789ab", "合成データ")


def test_golden_checks_accept_a_good_log() -> None:
    text = _synthetic_golden_log(_good_rows())
    check_golden_agreement(text)
    check_golden_selector_kind(text)


@pytest.mark.parametrize("breaks", ["disagree", "few-wrong", "nine", "stated-rate"])
def test_golden_agreement_check_goes_red(breaks: str) -> None:
    """A-2 の判定が、不一致1件・受験者の誤答が3件未満・9件・行と合わない一致率で赤になること。"""
    rows = _good_rows()
    if breaks == "disagree":
        rows[0] = GoldenRow("p001-q001", "A", "B", "A", "correct-mark")
    elif breaks == "few-wrong":
        rows[0] = GoldenRow("p001-q001", "B", "B", "B", "correct-mark")
    elif breaks == "nine":
        rows = rows[:9]
    text = _synthetic_golden_log(rows)
    if breaks == "stated-rate":
        text = text.replace("## 一致率\n\n`10/10`", "## 一致率\n\n`9/10`")
    with pytest.raises(AssertionError):
        check_golden_agreement(text)


@pytest.mark.parametrize("breaks", ["two-kinds", "missing"])
def test_golden_selector_kind_check_goes_red(breaks: str) -> None:
    """A-3 の判定が、selector_kind が2種類・記録の欠けた行で赤になること。"""
    rows = _good_rows()
    kind = "user-selected" if breaks == "two-kinds" else ""
    rows[5] = GoldenRow(rows[5].question_id, "C", "C", "C", kind)
    with pytest.raises(AssertionError):
        check_golden_selector_kind(_synthetic_golden_log(rows))


# ---- ホストでの実行 ----


def _commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _fetch_run(out_root: Path, url: str) -> int:
    """1回だけ取得し、生 HTML・問題・根拠レポートを書いて、件数と失敗箇所を出す。本文は出さない。"""
    from quiz_sift.adapters.browser_cdp import fetch
    from quiz_sift.extract.failures import FetchFailure
    from quiz_sift.extract.udemy import QUESTION_BLOCK_SELECTOR, extract
    from quiz_sift.extract.writer import write_questions, write_report
    from quiz_sift.safety.gitignore_guard import ensure_ignored

    print(f"url={url}")
    began = time.monotonic()
    page = fetch(url, ready_selector=QUESTION_BLOCK_SELECTOR, timeout=90.0)
    print(f"fetch_seconds={time.monotonic() - began:.1f}")
    if isinstance(page, FetchFailure):
        print(f"fetch_failure={page.reason.value}")
        return 1
    raw = page.html.encode("utf-8")
    raw_path = ensure_ignored(out_root / RAW_PAGE, out_root)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw)
    print(f"raw_digest={_digest(raw)}")
    result = extract([page])
    report = result.report
    print(f"question_blocks={report.question_blocks}")
    print(f"extracted={report.extracted}")
    print(f"failed={report.failed}")
    print(f"with_explanation={report.with_explanation}")
    print(f"without_explanation={report.without_explanation}")
    print(f"selector_kinds={','.join(sorted({e.selector_kind.value for e in result.evidences}))}")
    for failure in report.extraction_failures:
        print(f"extraction_failure={failure.question_index} {failure.missing_field.value} {failure.element_path}")
    for fetch_failure in report.fetch_failures:
        print(f"fetch_failure={fetch_failure.reason.value}")
    write_questions(out_root / QUESTIONS, result.questions, out_root)
    write_report(out_root / REPORT, result, out_root)
    print(f"wrote={QUESTIONS.as_posix()},{REPORT.as_posix()}")
    return 0


def _saved_questions(out_root: Path) -> tuple[bytes, dict[str, tuple[str, str, str]]]:
    """保存済みの生 HTML から、問題 ID → (受験者の選択, 抽出ラベル, selector_kind)。"""
    from quiz_sift.extract.html_tree import parse_html
    from quiz_sift.extract.udemy import (
        OPTION,
        OPTION_LABELS,
        QUESTION_BLOCK_CLASS_PREFIX,
        FetchedPage,
        _is_user_selected,
        extract,
    )

    raw = (out_root / RAW_PAGE).read_bytes()
    html = raw.decode("utf-8")
    blocks = parse_html(html).find_by_class_prefix(QUESTION_BLOCK_CLASS_PREFIX)
    choices = {}
    for number, block in enumerate(blocks, start=1):
        options = block.find_all(*OPTION)
        if len(options) != len(OPTION_LABELS):
            continue  # 抽出の失敗(複数選択問題)。ゴールデンセットには入らない。
        selected = [OPTION_LABELS[i] for i, option in enumerate(options) if _is_user_selected(option)]
        choices[f"p001-q{number:03d}"] = "".join(selected) or NO_SELECTION
    return raw, {
        question.local_id: (choices[question.local_id], question.answer_label, question.evidence.selector_kind.value)
        for question in extract([FetchedPage(RAW_PAGE.as_posix(), html)]).questions
    }


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tests/measurements/test_extraction_logs.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "fetch-run", "golden-candidates", "golden"):
        sub = commands.add_parser(name)
        sub.add_argument("--out-root", type=Path, required=True, help="out/ を持つリポジトリのルート")
        if name.startswith("fetch"):
            sub.add_argument("--url", default=DEFAULT_URL)
        if name == "golden":
            sub.add_argument("--label", action="append", default=[], help="正解ラベル。p001-q003=B")
            sub.add_argument("--label-source", required=True, help="ラベルを誰が何を見て読んだか(ログに逐語で残す)")
    args = parser.parse_args(argv)
    out_root: Path = args.out_root.resolve()

    if args.command == "fetch-run":
        return _fetch_run(out_root, args.url)
    if args.command == "fetch":
        child = [sys.executable, str(Path(__file__).resolve()), "fetch-run", "--out-root", str(out_root)]
        child += ["--url", args.url]
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        done = subprocess.run(child, capture_output=True, text=True)
        run = FetchRun(child, started_at, done.returncode, done.stdout, done.stderr)
        EXTRACTION_LOG.write_text(render_extraction_log(run, _commit()), encoding="utf-8")
        return done.returncode

    raw, questions = _saved_questions(out_root)
    if args.command == "golden-candidates":
        for question_id, (choice, label, kind) in questions.items():
            print(
                f"{question_id} user={choice} extracted={label} {kind}{'  (受験者の誤答)' if choice != label else ''}"
            )
        return 0
    labels = dict(item.split("=", 1) for item in args.label)
    if len(labels) != GOLDEN_SIZE or not set(labels.values()) <= LABELS:
        print(f"--label p001-qNNN=<A-D> を {GOLDEN_SIZE} 件渡す", file=sys.stderr)
        return 2
    unknown = sorted(set(labels) - set(questions))
    if unknown:
        print(f"抽出できた問題に無い ID: {unknown}", file=sys.stderr)
        return 2
    rows = []
    for question_id, label in labels.items():
        choice, extracted, kind = questions[question_id]
        rows.append(GoldenRow(question_id, choice, label, extracted, kind))
    command = ["PYTHONPATH=src", "python", "tests/measurements/test_extraction_logs.py", *argv]
    GOLDEN_LOG.write_text(
        render_golden_log(rows, _digest(raw), command, _commit(), args.label_source), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
