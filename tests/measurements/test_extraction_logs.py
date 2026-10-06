"""Udemy からの実取得とゴールデンセット実測のログの検査(T-019 / C-002 / R-2)。

実教材は NFR-2 によりコミットできず、サンドボックスからも読めない(D-048)。そのため証拠は
**ホストで取った実測ログ**で、このテストはログの中身を機械判定する。取得もこのファイルから行う:

- `PYTHONPATH=src python tests/measurements/test_extraction_logs.py fetch --out-root <リポジトリ>`
  ログイン済みの Chrome(CDP)で結果ページを1回だけ取得し、生 HTML・問題 JSONL・根拠レポートを
  `<リポジトリ>/out/udemy/` に書き、`docs/test/measurements/extraction-run.md` を書く。取得そのものは
  子プロセス(`fetch-run`)で走らせ、その argv・標準出力・標準エラー・終了コードを逐語で記録する。
- `... golden-candidates --out-root <リポジトリ>` は保存済みの生 HTML から、問題ごとの受験者の選択・
  抽出した正解・根拠の要素種別を出す(本文は出さない)。ゴールデンセットを選ぶための一覧で、再取得はしない。
- `... golden --out-root <リポジトリ> --label p001-q003=B --label p001-q010=A,C ... --label-source <経路>` は
  正解の集合ラベル(選択肢記号 A〜F をカンマで並べたもの)とその読み取り経路を受け取り、保存済みの生 HTML から
  抽出し直した正解の集合と突き合わせて `docs/test/measurements/golden-set-nofm.md` を書く。ラベルは外から
  渡すもので、このファイルは抽出結果から埋めない。誰が何を見てラベルを読んだかは実行ごとに違うので、定型文に
  せず `--label-source` を逐語で残す。
- `... rerun --out-root <リポジトリ>` は保存済みの生 HTML をオフラインで抽出し直し、問題 JSONL と根拠レポートを
  `out/udemy/` に書き直して、件数だけを `docs/test/measurements/extraction-rerun.md` に書く。
- `... deck-source --out-root <リポジトリ> --decision not-needed|refetched [--reason <理由>]` は、フル処理に使う
  デッキ(保存済みの生 HTML から抽出したもの)の件数と期待件数との差を `docs/test/measurements/extraction-refetch.md`
  に書く。

どのログも本文を持たない(問題 ID・選択肢記号・件数・要素パスだけ)。

判定:

- 実取得: 抽出ログに規定の見出しがあり、終了コード 0、逐語の標準出力の `extracted=` が 100〜200。
- 前のサイクルのゴールデンセット(`golden-set.md`): 10件、受験者の選択と人手ラベルが異なる行が3件以上、
  行から数え直した一致数が 10/10 で、記録された一致率もそれと同じ。全行の `selector_kind` が同一。
- N-of-M を足したゴールデンセット(`golden-set-nofm.md`): 正解の集合の一致が全行、正解が2件以上の行が3件以上、
  受験者の選択と正解の集合が異なる行が3件以上。
- 再抽出の記録: 件数の表だけを持ち、全問数 = 抽出 + 抽出の失敗、(N, M) の組ごとの件数の和 = 抽出、
  理由ごとの件数の和 = 抽出の失敗。
- デッキの出どころの記録: デッキの全問数と (N, M) の組ごとの件数、期待件数との差(不足)とその理由。

人が書く記録(再抽出・N-of-M のゴールデンセット・デッキの出どころ)は、コミットされるまで判定を skip する。
"""

import argparse
import hashlib
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from quiz_sift.extract.udemy import FetchedPage

ROOT = Path(__file__).resolve().parent.parent.parent
EXTRACTION_LOG = ROOT / "docs" / "test" / "measurements" / "extraction-run.md"
GOLDEN_LOG = ROOT / "docs" / "test" / "measurements" / "golden-set.md"
GOLDEN_NOFM_LOG = ROOT / "docs" / "test" / "measurements" / "golden-set-nofm.md"
RERUN_LOG = ROOT / "docs" / "test" / "measurements" / "extraction-rerun.md"
REFETCH_LOG = ROOT / "docs" / "test" / "measurements" / "extraction-refetch.md"

DEFAULT_URL = "https://www.udemy.com/course/awsgenerative-ai-developer-professional-aip-c01-j/learn/quiz/7600618/result/1802860553"
"""取得対象の演習テストの結果ページ。T-019 の承認が覆うのはこの1ページ(100〜200問)の取得まで。"""
DECK_DIR = Path("out") / "udemy" / "aip-c01"
RAW_DIR = DECK_DIR / "raw"
RAW_PAGE = RAW_DIR / "page-001.html"
QUESTIONS = DECK_DIR / "questions.jsonl"
REPORT = DECK_DIR / "extract-report.json"

EXTRACTION_HEADINGS = ("## 実行条件", "## argv", "## 終了コード", "## 標準出力", "## 標準エラー")
GOLDEN_HEADINGS = ("## 実行条件", "## 一致率", "## ゴールデンセット")
MIN_QUESTIONS, MAX_QUESTIONS = 100, 200
GOLDEN_SIZE = 10
MIN_USER_WRONG = 3
MIN_MULTI_ANSWER = 3
"""N-of-M を足したゴールデンセットが持つべき、正解が2件以上の行の数(C-002)。"""
LABELS = frozenset("ABCDEF")
NO_SELECTION = "-"
"""受験者が何も選ばなかった問題の「受験者の選択」欄。"""
RERUN_HEADINGS = ("## 実行条件", "## 件数", "## (N, M) の組ごとの件数", "## 抽出の失敗の理由ごとの件数")
REFETCH_HEADINGS = (
    "## 実行条件",
    "## 判断",
    "## 件数",
    "## (N, M) の組ごとの件数",
    "## 期待件数との差",
    "## 不足の理由",
)
EXPECTED_TOTAL = 190
EXPECTED_PAIRS = {(2, 5): 13, (3, 6): 2}
"""フル処理に使うデッキの期待件数(T-059): 全 190 問、5択2正解 13 問、6択3正解 2 問。"""
NOT_NEEDED = "取り直し不要"
REFETCHED = "取り直した"


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


# ---- ゴールデンセット ----


def parse_labels(cell: str) -> frozenset[str]:
    """`A,C` を選択肢記号の集合にする。空・重複・A〜F 以外は AssertionError。"""
    labels = [label.strip() for label in cell.split(",")]
    assert all(labels) and set(labels) <= LABELS, f"選択肢記号が A〜F の並びでない: {cell!r}"
    assert len(set(labels)) == len(labels), f"選択肢記号が重複している: {cell!r}"
    return frozenset(labels)


def format_labels(labels: frozenset[str]) -> str:
    return ",".join(sorted(labels)) if labels else NO_SELECTION


@dataclass(frozen=True)
class GoldenRow:
    question_id: str
    user_choice: frozenset[str]
    """受験者が選んだ選択肢。何も選んでいなければ空。"""
    human_labels: frozenset[str]
    extracted_labels: frozenset[str]
    selector_kind: str

    @property
    def agrees(self) -> bool:
        return self.human_labels == self.extracted_labels


def render_golden_log(
    rows: list[GoldenRow], raw: list[tuple[str, str]], argv: list[str], commit: str, label_source: str
) -> str:
    """`raw` は生 HTML の (パス, digest) の列。"""
    agreed = sum(row.agrees for row in rows)
    table = "\n".join(
        f"| {row.question_id} | {format_labels(row.user_choice)} | {format_labels(row.human_labels)} "
        f"| {format_labels(row.extracted_labels)} | {row.selector_kind} | {'一致' if row.agrees else '不一致'} |"
        for row in rows
    )
    rate = f"{agreed}/{len(rows)}"
    return f"""# 実測ログ: ゴールデンセットの正解の集合の一致率

R-2(C-002)の「人手で確認した N-of-M を3件以上加えたゴールデンセット全体で、正解の集合の一致率が 100% である」
ことの実測(D-048)。表の「人手ラベル」列は外から渡した正解の集合で、誰が何を見て読んだかは実行条件の
「ラベルの出所」に記す。抽出ラベルは `extract.udemy.extract` が保存済みの生 HTML から同定した正解の集合。
選択肢記号はカンマで区切り、`{NO_SELECTION}` は受験者が何も選ばなかったことを表す。
問題 ID の `pPPP-qNNN` は PPP 枚目の結果ページの NNN 問目。本文は載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `{" ".join(argv)}` |
| 実行日時 | {datetime.now().astimezone().isoformat(timespec="seconds")} |
| コミット | `{commit}` |
| 生 HTML | {_raw_cell(raw)} |
| ラベルの出所 | {label_source} |

## 一致率

`{rate}`

## ゴールデンセット

| 問題 ID | 受験者の選択 | 人手ラベル | 抽出ラベル | selector_kind | 判定 |
|---|---|---|---|---|---|
{table}
"""


def _raw_cell(raw: list[tuple[str, str]]) -> str:
    return ", ".join(f"`{path}` (`{digest}`)" for path, digest in raw)


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
        choice = frozenset() if cells[1] == NO_SELECTION else parse_labels(cells[1])
        rows.append(GoldenRow(cells[0], choice, parse_labels(cells[2]), parse_labels(cells[3]), cells[4]))
    return rows


def _check_golden_rows(text: str, rows: list[GoldenRow]) -> None:
    """どちらのゴールデンセットにも共通の判定: ID が一意、受験者の誤答が3件以上、記録された一致率が行と合い 100%。"""
    assert rows, "ゴールデンセットに行が無い"
    assert len({row.question_id for row in rows}) == len(rows), "問題 ID が重複している"
    user_wrong = sum(row.user_choice != row.human_labels for row in rows)
    assert user_wrong >= MIN_USER_WRONG, f"受験者の選択と正解が異なる問題が {MIN_USER_WRONG} 件未満: {user_wrong}"
    agreed = sum(row.agrees for row in rows)
    assert _section(text, "## 一致率").strip() == f"`{agreed}/{len(rows)}`", "記録された一致率が行と合わない"
    assert agreed == len(rows), f"一致率が 100% でない: {agreed}/{len(rows)}"


def check_golden_agreement(text: str) -> None:
    """前のサイクルのゴールデンセット(4択・正解1件の10件)。"""
    rows = recorded_golden_rows(text)
    assert len(rows) == GOLDEN_SIZE, f"ゴールデンセットが {GOLDEN_SIZE} 件でない: {len(rows)} 件"
    for row in rows:
        assert len(row.human_labels) == len(row.extracted_labels) == 1, f"正解が1件でない: {row}"
        assert row.human_labels | row.extracted_labels <= frozenset("ABCD"), f"ラベルが A〜D でない: {row}"
    _check_golden_rows(text, rows)


def check_golden_nofm(text: str) -> None:
    """N-of-M を足したゴールデンセット。正解の集合の一致で判定し、正解が2件以上の行が3件以上あること。"""
    rows = recorded_golden_rows(text)
    multi = sum(len(row.human_labels) >= 2 for row in rows)
    assert multi >= MIN_MULTI_ANSWER, f"正解が2件以上の行が {MIN_MULTI_ANSWER} 件未満: {multi}"
    _check_golden_rows(text, rows)


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
    """前のサイクルの A-2: ゴールデンセット10件(受験者の誤答3件以上)の一致率が 10/10 と記録されている。"""
    check_golden_agreement(_golden_text())


def test_golden_set_selector_kind_is_uniform() -> None:
    """前のサイクルの A-3: ゴールデンセットの全行に selector_kind があり、その集合の要素数が 1。"""
    check_golden_selector_kind(_golden_text())


def test_golden_log_uses_the_same_page_as_the_extraction_run() -> None:
    """ゴールデンセットの抽出ラベルが、A-1 で取得したのと同じ生 HTML から出ていること。"""
    golden = _table_value(_section(_golden_text(), "## 実行条件"), "生 HTML")
    stdout = _code_block(_section(EXTRACTION_LOG.read_text(encoding="utf-8"), "## 標準出力"))
    digest = re.search(r"\(`(sha256:[0-9a-f]{64})`\)$", golden)
    assert digest is not None, f"生 HTML の digest が無い: {golden}"
    assert f"raw_digest={digest.group(1)}" in stdout.splitlines(), "取得ログと別の生 HTML から抽出している"


def _row(question_id: str, user: str, human: str, extracted: str, kind: str = "correct-mark") -> GoldenRow:
    def labels(cell: str) -> frozenset[str]:
        return frozenset(cell.split(",")) if cell else frozenset()

    return GoldenRow(question_id, labels(user), labels(human), labels(extracted), kind)


def _good_rows() -> list[GoldenRow]:
    wrong = [_row(f"p001-q{n:03d}", "A", "B", "B") for n in range(1, 4)]
    right = [_row(f"p001-q{n:03d}", "C", "C", "C") for n in range(4, 11)]
    return wrong + right


def _good_nofm_rows() -> list[GoldenRow]:
    multi = [
        _row("p001-q011", "A", "A,C", "A,C"),
        _row("p001-q012", "B,D", "B,D", "B,D"),
        _row("p001-q013", "", "A,C,F", "A,C,F"),
    ]
    return _good_rows() + multi


RAW = [(RAW_PAGE.as_posix(), "sha256:" + "0" * 64)]


def _synthetic_golden_log(rows: list[GoldenRow]) -> str:
    return render_golden_log(rows, RAW, ["golden"], "0123456789ab", "合成データ")


def test_golden_checks_accept_a_good_log() -> None:
    text = _synthetic_golden_log(_good_rows())
    check_golden_agreement(text)
    check_golden_selector_kind(text)
    check_golden_nofm(_synthetic_golden_log(_good_nofm_rows()))


@pytest.mark.parametrize("breaks", ["disagree", "few-wrong", "nine", "stated-rate"])
def test_golden_agreement_check_goes_red(breaks: str) -> None:
    """前のサイクルの A-2 の判定が、不一致1件・受験者の誤答が3件未満・9件・行と合わない一致率で赤になること。"""
    rows = _good_rows()
    if breaks == "disagree":
        rows[0] = _row("p001-q001", "A", "B", "A")
    elif breaks == "few-wrong":
        rows[0] = _row("p001-q001", "B", "B", "B")
    elif breaks == "nine":
        rows = rows[:9]
    text = _synthetic_golden_log(rows)
    if breaks == "stated-rate":
        text = text.replace("## 一致率\n\n`10/10`", "## 一致率\n\n`9/10`")
    with pytest.raises(AssertionError):
        check_golden_agreement(text)


@pytest.mark.parametrize("breaks", ["two-kinds", "missing"])
def test_golden_selector_kind_check_goes_red(breaks: str) -> None:
    """前のサイクルの A-3 の判定が、selector_kind が2種類・記録の欠けた行で赤になること。"""
    rows = _good_rows()
    kind = "user-selected" if breaks == "two-kinds" else ""
    rows[5] = _row(rows[5].question_id, "C", "C", "C", kind)
    with pytest.raises(AssertionError):
        check_golden_selector_kind(_synthetic_golden_log(rows))


def test_golden_check_uses_answer_sets_and_requires_three_multi_answer_rows() -> None:
    """A-4: 集合ラベルを受け、正解の集合の一致で判定し、正解が2件以上の行が3件以上あることを判定に含める。"""
    rows = _good_nofm_rows()
    text = _synthetic_golden_log(rows)
    assert "| p001-q011 | A | A,C | A,C | correct-mark | 一致 |" in text
    assert "| p001-q013 | - | A,C,F | A,C,F | correct-mark | 一致 |" in text
    check_golden_nofm(text)
    # 集合として比べる: 並び順の違いは一致。
    check_golden_nofm(text.replace("| p001-q013 | - | A,C,F | A,C,F |", "| p001-q013 | - | F,A,C | A,C,F |"))

    # 集合の一部だけ一致する行は不一致(N = 1 として素通りしない)。
    partial = [*rows[:-1], _row("p001-q013", "", "A,C,F", "A,C")]
    with pytest.raises(AssertionError, match="一致率"):
        check_golden_nofm(_synthetic_golden_log(partial))
    # 正解が2件以上の行が2件しかない。
    with pytest.raises(AssertionError, match="2件以上の行"):
        check_golden_nofm(_synthetic_golden_log(rows[:-1]))
    # A〜F 以外の記号・重複した記号はラベルとして読まない。
    for broken in ("A,G", "A,A", "A,"):
        with pytest.raises(AssertionError, match="選択肢記号"):
            check_golden_nofm(text.replace("| p001-q011 | A | A,C |", f"| p001-q011 | A | {broken} |"))
    # 記録先は golden-set-nofm.md。
    assert GOLDEN_NOFM_LOG.relative_to(ROOT).as_posix() == "docs/test/measurements/golden-set-nofm.md"


# ---- 再抽出の記録 ----


@dataclass(frozen=True)
class RerunCounts:
    """再抽出の件数。本文は持たない。"""

    question_blocks: int
    extracted: int
    extraction_failures: int
    fetch_failures: int
    user_selected: int
    """推定経路(受験者の選択を正解とした)を使った件数。"""
    pairs: dict[tuple[int, int], int]
    """(N, M) の組 → 件数。"""
    reasons: dict[str, int]
    """抽出の失敗の理由(`ExtractField` の値)→ 件数。"""


_COUNT_ROWS = (
    ("全問数", "question_blocks"),
    ("抽出", "extracted"),
    ("抽出の失敗", "extraction_failures"),
    ("取得の失敗", "fetch_failures"),
    ("推定経路", "user_selected"),
)


def rerun_counts(pages: "Sequence[FetchedPage]") -> RerunCounts:
    """ページの列を抽出し直して件数を数える。"""
    from collections import Counter

    from quiz_sift.extract.evidence import SelectorKind
    from quiz_sift.extract.failures import ExtractField
    from quiz_sift.extract.udemy import extract

    result = extract(pages)
    report = result.report
    pairs = Counter((len(q.answer_labels), len(q.options)) for q in result.questions)
    reasons = Counter(failure.missing_field.value for failure in report.extraction_failures)
    return RerunCounts(
        question_blocks=report.question_blocks,
        extracted=report.extracted,
        extraction_failures=len(report.extraction_failures),
        fetch_failures=len(report.fetch_failures),
        user_selected=sum(e.selector_kind is SelectorKind.USER_SELECTED for e in result.evidences),
        pairs=dict(sorted(pairs.items())),
        reasons={field.value: reasons[field.value] for field in ExtractField},
    )


def _conditions_table(argv: list[str], commit: str, raw: list[tuple[str, str]]) -> str:
    return f"""## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `{" ".join(argv)}` |
| 実行日時 | {datetime.now().astimezone().isoformat(timespec="seconds")} |
| コミット | `{commit}` |
| 生 HTML | {_raw_cell(raw)} |
"""


def _pairs_table(pairs: dict[tuple[int, int], int]) -> str:
    rows = "".join(f"| {n} | {m} | {count} |\n" for (n, m), count in sorted(pairs.items()))
    return f"| N | M | 件数 |\n|---|---|---|\n{rows}"


def render_rerun_log(counts: RerunCounts, raw: list[tuple[str, str]], argv: list[str], commit: str) -> str:
    totals = "".join(f"| {label} | {getattr(counts, name)} |\n" for label, name in _COUNT_ROWS)
    reasons = "".join(f"| {reason} | {count} |\n" for reason, count in counts.reasons.items())
    return f"""# 実測ログ: 保存済みの生 HTML の再抽出

R-2(C-002)の「保存済みの生 HTML の再抽出の報告に全問数と (N, M) の組ごとの件数が現れる」ことの記録。
`tests/measurements/test_extraction_logs.py rerun` が保存済みの生 HTML をオフラインで抽出し直し、件数だけを
書いた。全問数は問題ブロックの数で、抽出 + 抽出の失敗に等しい。推定経路は、正解マークが無く単一選択の問題で
受験者の選択を正解とした件数。問題文と選択肢の本文は載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

{_conditions_table(argv, commit, raw)}
## 件数

| 項目 | 件数 |
|---|---|
{totals}
## (N, M) の組ごとの件数

{_pairs_table(counts.pairs)}
## 抽出の失敗の理由ごとの件数

| 理由 | 件数 |
|---|---|
{reasons}"""


def _table_rows(section: str, columns: int) -> list[list[str]]:
    """節の表の本体の行。表の外に文字があれば AssertionError(件数の表だけを持つこと)。"""
    lines = [line.strip() for line in section.strip().splitlines() if line.strip()]
    assert all(line.startswith("|") and line.endswith("|") for line in lines), f"表の外に文字がある: {lines}"
    cells = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
    assert all(len(row) == columns for row in cells), f"列の数が {columns} でない行がある: {cells}"
    assert len(cells) >= 2 and set("".join(cells[1])) <= {"-"}, "表の見出し行が無い"
    return cells[2:]


def _count(cell: str) -> int:
    assert re.fullmatch(r"\d+", cell), f"件数が数値でない: {cell!r}"
    return int(cell)


def _pairs_from(section: str) -> dict[tuple[int, int], int]:
    from quiz_sift.domain.question import OPTION_COUNTS

    pairs: dict[tuple[int, int], int] = {}
    for n_cell, m_cell, count in _table_rows(section, 3):
        pair = (_count(n_cell), _count(m_cell))
        assert pair[1] in OPTION_COUNTS and 1 <= pair[0] < pair[1], f"範囲外の (N, M): {pair}"
        assert pair not in pairs, f"(N, M) の行が重複している: {pair}"
        pairs[pair] = _count(count)
    return pairs


def check_rerun_record(text: str) -> RerunCounts:
    """再抽出の記録を読み、件数の整合を確かめる。読めないか合わなければ AssertionError。"""
    from quiz_sift.extract.failures import ExtractField

    for heading in RERUN_HEADINGS:
        _section(text, heading)
    _check_conditions(text)
    totals = dict(_table_rows(_section(text, "## 件数"), 2))
    assert set(totals) == {label for label, _ in _COUNT_ROWS}, f"件数の項目が規定と違う: {sorted(totals)}"
    values = {name: _count(totals[label]) for label, name in _COUNT_ROWS}
    pairs = _pairs_from(_section(text, "## (N, M) の組ごとの件数"))
    reasons = {
        reason: _count(count) for reason, count in _table_rows(_section(text, "## 抽出の失敗の理由ごとの件数"), 2)
    }
    assert set(reasons) == {field.value for field in ExtractField}, f"失敗の理由が規定と違う: {sorted(reasons)}"
    counts = RerunCounts(**values, pairs=pairs, reasons=reasons)
    assert counts.question_blocks > 0, "全問数が 0"
    assert counts.extracted + counts.extraction_failures == counts.question_blocks, "全問数 ≠ 抽出 + 抽出の失敗"
    assert sum(pairs.values()) == counts.extracted, "(N, M) の組ごとの件数の和が抽出の件数と合わない"
    assert sum(reasons.values()) == counts.extraction_failures, "理由ごとの件数の和が抽出の失敗の件数と合わない"
    assert counts.user_selected <= counts.extracted, "推定経路の件数が抽出の件数を超える"
    return counts


def _reasons(**counts: int) -> dict[str, int]:
    """抽出の失敗の理由(`ExtractField` の値)ごとの件数。指定しない理由は 0。"""
    from quiz_sift.extract.failures import ExtractField

    assert set(counts) <= {field.value for field in ExtractField}
    return {field.value: counts.get(field.value, 0) for field in ExtractField}


def _synthetic_option(text: str, *, correct: bool, selected: bool, multi: bool) -> str:
    state = "answer-correct--PLOEU" if correct else "answer-skipped--1NDPn"
    if multi:
        icon = "#icon-checkbox-filled" if selected else "#icon-checkbox-empty"
    else:
        icon = "#icon-success" if selected else "#icon-radio-empty"
    return (
        f'<div class="answer-result-pane--{state}" data-purpose="answer">'
        '<div data-purpose="answer-body"><span data-purpose="answer-result-body-selection-icon"><svg>'
        f'<use xlink:href="{icon}"></use></svg></span><p>{text}</p></div></div>'
    )


def _synthetic_page(*blocks: tuple[int, tuple[int, ...], tuple[int, ...], bool]) -> str:
    """(選択肢の件数, 正解マークの位置, 受験者の選択の位置, 複数選択か) の並びから合成の結果ページを作る。"""
    html = []
    for number, (options, correct, selected, multi) in enumerate(blocks, start=1):
        body = "".join(
            _synthetic_option(
                f"合成本文 選択肢 {number}-{i}", correct=i in correct, selected=i in selected, multi=multi
            )
            for i in range(options)
        )
        html.append(
            '<div class="result-pane--question-result-pane--sIcOh">'
            f'<div id="question-prompt"><p>合成本文 問題文 {number}</p></div>{body}</div>'
        )
    return f"<html><body><main>{''.join(html)}</main></body></html>"


def _synthetic_out_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """合成の生 HTML を持つ out/ を作る。記録のコミット ID は固定値にする — 品質ゲートのサンドボックスに git は無い。"""
    monkeypatch.setattr(sys.modules[__name__], "_commit", lambda: "0123456789ab")
    (tmp_path / ".gitignore").write_text("out/\n", encoding="utf-8")
    raw = tmp_path / RAW_DIR
    raw.mkdir(parents=True)
    (raw / "page-001.html").write_text(
        _synthetic_page(
            (4, (1,), (1,), False),
            (5, (0, 3), (0,), True),
            (6, (1, 2, 5), (), True),
            (4, (), (2,), False),  # 正解マーク無し・単一選択 → 推定経路
            (3, (0,), (), False),  # 選択肢3件 → options の失敗
        ),
        encoding="utf-8",
    )
    (raw / "page-002.html").write_text(
        _synthetic_page(
            (4, (), (1,), True),  # 正解マーク無し・複数選択 → answer の失敗
            (5, (4,), (4,), False),
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_rerun_record_holds_counts_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A-5: rerun が保存済みの生 HTML を抽出し直し、件数だけを記録する。本文は書かない。"""
    out_root = _synthetic_out_root(tmp_path, monkeypatch)
    record = tmp_path / "extraction-rerun.md"
    monkeypatch.setattr(sys.modules[__name__], "RERUN_LOG", record)
    from quiz_sift.adapters import browser_cdp

    def no_fetch(*args: object, **kwargs: object) -> None:
        raise AssertionError("rerun がブラウザから取得した")

    monkeypatch.setattr(browser_cdp, "fetch", no_fetch)

    assert _main(["rerun", "--out-root", str(out_root)]) == 0

    text = record.read_text(encoding="utf-8")
    assert "合成本文" not in text
    counts = check_rerun_record(text)
    assert (counts.question_blocks, counts.extracted, counts.extraction_failures, counts.fetch_failures) == (7, 5, 2, 0)
    assert counts.pairs == {(1, 4): 2, (1, 5): 1, (2, 5): 1, (3, 6): 1}
    assert counts.user_selected == 1
    assert counts.reasons == _reasons(options=1, answer=1)
    assert "| 全問数 | 7 |" in text
    assert "| 2 | 5 | 1 |" in text
    # 抽出し直した問題は ingest の入力として out/ に書き直される。
    written = (out_root / QUESTIONS).read_text(encoding="utf-8").splitlines()
    assert len(written) == 5
    assert RERUN_LOG == record  # 記録先の差し替えが効いていること(既定は docs/test/measurements/extraction-rerun.md)


def test_golden_and_deck_source_commands_write_judgeable_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """golden は集合ラベルを受けて golden-set-nofm.md を、deck-source は extraction-refetch.md を書く。

    どちらも本文は書かない。
    """
    out_root = _synthetic_out_root(tmp_path, monkeypatch)
    golden, refetch = tmp_path / "golden-set-nofm.md", tmp_path / "extraction-refetch.md"
    monkeypatch.setattr(sys.modules[__name__], "GOLDEN_NOFM_LOG", golden)
    monkeypatch.setattr(sys.modules[__name__], "REFETCH_LOG", refetch)
    labels = ["p001-q001=B", "p001-q002=A,D", "p001-q003=B,C,F", "p001-q004=C", "p002-q002=E"]
    argv = ["golden", "--out-root", str(out_root), "--label-source", "合成ページの正解マーク"]
    assert _main([*argv, *(f"--label={label}" for label in labels)]) == 0

    text = golden.read_text(encoding="utf-8")
    assert "合成本文" not in text
    rows = {row.question_id: row for row in recorded_golden_rows(text)}
    assert rows["p001-q002"] == GoldenRow("p001-q002", frozenset("A"), frozenset("AD"), frozenset("AD"), "correct-mark")
    assert rows["p001-q004"].selector_kind == "user-selected"
    assert rows["p002-q002"].extracted_labels == frozenset("E")
    assert all(row.agrees for row in rows.values())
    # 抽出の失敗だった問題・読めない記号はラベルとして受けない。
    assert _main([*argv, "--label=p002-q001=B"]) == 2
    assert _main([*argv, "--label=p001-q001=G"]) == 2

    reason = "合成ページは5問しかない"
    assert _main(["deck-source", "--out-root", str(out_root), "--decision", "refetched", "--reason", reason]) == 0
    record = refetch.read_text(encoding="utf-8")
    check_refetch_record(record)
    assert "| デッキの全問数 | 5 |" in record and reason in record


# ---- デッキの出どころの記録 ----


def _shortfalls(total: int, pairs: dict[tuple[int, int], int]) -> list[tuple[str, int, int]]:
    """(項目, 期待, 実際) の列。"""
    return [("全問数", EXPECTED_TOTAL, total)] + [
        (f"({n}, {m})", expected, pairs.get((n, m), 0)) for (n, m), expected in EXPECTED_PAIRS.items()
    ]


def render_refetch_log(
    total: int,
    pairs: dict[tuple[int, int], int],
    decision: str,
    reason: str,
    raw: list[tuple[str, str]],
    argv: list[str],
    commit: str,
) -> str:
    """`decision` は `NOT_NEEDED` か `REFETCHED`。`reason` は不足の理由(不足が無ければ `-`)。"""
    if decision == NOT_NEEDED:
        judgement = f"{NOT_NEEDED}。再抽出の記録 `docs/test/measurements/extraction-rerun.md` が期待件数に届いている。"
    else:
        judgement = f"{REFETCHED}。再抽出が期待件数に届かなかったので、Udemy から結果ページを取り直して抽出し直した。"
    diff = "".join(
        f"| {item} | {expected} | {actual} | {max(0, expected - actual)} |\n"
        for item, expected, actual in _shortfalls(total, pairs)
    )
    return f"""# 実測ログ: フル処理に使うデッキの出どころ

R-2(C-002)の T-059: フル処理に使うデッキの全問数と (N, M) の組ごとの件数、期待件数に届かなかった件数と
その理由の記録。デッキは保存済みの生 HTML から抽出した問題で、全問数は抽出できた問題の数。本文は載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

{_conditions_table(argv, commit, raw)}
## 判断

{judgement}

## 件数

| 項目 | 件数 |
|---|---|
| デッキの全問数 | {total} |

## (N, M) の組ごとの件数

{_pairs_table(pairs)}
## 期待件数との差

| 項目 | 期待 | 実際 | 不足 |
|---|---|---|---|
{diff}
## 不足の理由

{reason}
"""


def check_refetch_record(text: str) -> None:
    """デッキの出どころの記録を読み、件数と不足の整合を確かめる。"""
    for heading in REFETCH_HEADINGS:
        _section(text, heading)
    _check_conditions(text)
    judgement = _section(text, "## 判断").strip()
    assert judgement.startswith((NOT_NEEDED, REFETCHED)), f"判断が {NOT_NEEDED} / {REFETCHED} で始まらない"
    totals = dict(_table_rows(_section(text, "## 件数"), 2))
    assert set(totals) == {"デッキの全問数"}, f"件数の項目が規定と違う: {sorted(totals)}"
    total = _count(totals["デッキの全問数"])
    pairs = _pairs_from(_section(text, "## (N, M) の組ごとの件数"))
    assert sum(pairs.values()) == total, "(N, M) の組ごとの件数の和がデッキの全問数と合わない"
    recorded = _table_rows(_section(text, "## 期待件数との差"), 4)
    expected = _shortfalls(total, pairs)
    assert [row[0] for row in recorded] == [item for item, _, _ in expected], "期待件数との差の項目が規定と違う"
    shortfall = 0
    for (item, want, actual), row in zip(expected, recorded, strict=True):
        assert [_count(cell) for cell in row[1:]] == [want, actual, max(0, want - actual)], (
            f"{item} の差が件数と合わない"
        )
        shortfall += max(0, want - actual)
    reason = _section(text, "## 不足の理由").strip()
    if shortfall:
        assert reason and reason != NO_SELECTION, "期待件数に届いていないのに不足の理由が無い"
        assert not judgement.startswith(NOT_NEEDED), "期待件数に届いていないのに取り直し不要としている"
    else:
        assert reason == NO_SELECTION, f"不足が無いのに不足の理由が `{NO_SELECTION}` でない"
    if judgement.startswith(NOT_NEEDED):
        assert "extraction-rerun.md" in judgement, "取り直し不要の判断に再抽出の記録への参照が無い"


# ---- 人が書く記録の判定(T-058 / T-059) ----


def _committed(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"記録がまだコミットされていない: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def test_rerun_record_is_well_formed() -> None:
    """T-058 A-2: 再抽出の記録が形の検査を通り、全問数と (N, M) の組ごとの件数が数値である。"""
    check_rerun_record(_committed(RERUN_LOG))


def test_golden_set_nofm_matches_every_answer_set() -> None:
    """T-058 A-4: 正解の集合の一致率 100%、N-of-M 3件以上、受験者の選択と正解が異なる問題3件以上。"""
    check_golden_nofm(_committed(GOLDEN_NOFM_LOG))


def test_deck_source_record_states_counts_and_shortfall() -> None:
    """T-059 A-2: デッキの全問数と (N, M) の組ごとの件数、期待件数に届かなかった件数と理由が書かれている。"""
    check_refetch_record(_committed(REFETCH_LOG))


_FULL_PAIRS = {(1, 4): 175, (2, 5): 13, (3, 6): 2}
_GOOD_RERUN = RerunCounts(190, 190, 0, 0, 0, _FULL_PAIRS, _reasons())


def _without_user_wrong(text: str, question_ids: tuple[str, ...]) -> str:
    """`question_ids` の行の受験者の選択を正解に揃え、受験者の誤答を減らす。"""
    for question_id in question_ids:
        text = text.replace(f"| {question_id} | A | B |", f"| {question_id} | B | B |")
    return text


def _defective_records() -> list[tuple[str, str]]:
    """(判定の名前, 欠けた記録) の列。"""
    rerun = render_rerun_log(_GOOD_RERUN, RAW, ["rerun"], "0123456789ab")
    golden = _synthetic_golden_log(_good_nofm_rows())
    full = render_refetch_log(190, _FULL_PAIRS, NOT_NEEDED, NO_SELECTION, RAW, ["deck-source"], "0123456789ab")
    short_pairs = {(1, 4): 175, (2, 5): 11, (3, 6): 2}
    short = render_refetch_log(
        188, short_pairs, REFETCHED, "2問は再取得でも複数選択の印が無い", RAW, ["x"], "0123456789ab"
    )
    return [
        ("rerun", rerun.replace("| 全問数 | 190 |", "| 全問数 | 191 |")),
        ("rerun", rerun.replace("| 2 | 5 | 13 |", "| 2 | 5 | 十三 |")),
        ("rerun", rerun.replace("| 3 | 6 | 2 |\n", "")),
        ("rerun", rerun.replace("| 1 | 4 | 175 |", "| 4 | 4 | 175 |")),
        ("rerun", rerun.replace("## (N, M) の組ごとの件数", "## 組ごとの件数")),
        ("rerun", rerun.replace("| answer | 0 |\n", "")),
        ("rerun", rerun.replace("| 抽出 | 190 |\n", "| 抽出 | 190 |\n合成本文 問題文 1\n")),
        ("golden", golden.replace("| p001-q013 | - | A,C,F | A,C,F |", "| p001-q013 | - | A,C,F | A,C |")),
        ("golden", golden.replace("| p001-q013 | - | A,C,F | A,C,F |", "| p001-q013 | - | A | A |")),
        ("golden", _without_user_wrong(golden, ("p001-q001", "p001-q002", "p001-q003"))),
        ("golden", golden.replace("`13/13`", "`12/13`")),
        ("refetch", full.replace("| デッキの全問数 | 190 |", "| デッキの全問数 | 189 |")),
        ("refetch", full.replace("| (2, 5) | 13 | 13 | 0 |", "| (2, 5) | 13 | 12 | 1 |")),
        ("refetch", full.replace("`docs/test/measurements/extraction-rerun.md`", "前回の記録")),
        ("refetch", full.replace("## 不足の理由\n\n-", "## 不足の理由\n\n理由なし")),
        ("refetch", short.replace("## 不足の理由\n\n2問は再取得でも複数選択の印が無い", "## 不足の理由\n\n-")),
        ("refetch", short.replace("| (2, 5) | 13 | 11 | 2 |", "| (2, 5) | 13 | 11 | 0 |")),
        ("refetch", short.replace(f"{REFETCHED}。", f"{NOT_NEEDED}。extraction-rerun.md。")),
    ]


_JUDGES = {"rerun": check_rerun_record, "golden": check_golden_nofm, "refetch": check_refetch_record}


def test_person_record_judges_go_red_on_synthetic_defects() -> None:
    """A-6: 人が書く記録の判定が、合成の正しい記録で緑、欠けた記録で赤になる。"""
    check_rerun_record(render_rerun_log(_GOOD_RERUN, RAW, ["rerun"], "0123456789ab"))
    check_golden_nofm(_synthetic_golden_log(_good_nofm_rows()))
    check_refetch_record(
        render_refetch_log(190, _FULL_PAIRS, NOT_NEEDED, NO_SELECTION, RAW, ["deck-source"], "0123456789ab")
    )
    check_refetch_record(
        render_refetch_log(
            188,
            {(1, 4): 175, (2, 5): 11, (3, 6): 2},
            REFETCHED,
            "2問は再取得でも複数選択の印が無い",
            RAW,
            ["x"],
            "0" * 12,
        )
    )
    for judge, record in _defective_records():
        with pytest.raises(AssertionError):
            _JUDGES[judge](record)


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


def _saved_pages(out_root: Path) -> tuple[list["FetchedPage"], list[tuple[str, str]]]:
    """保存済みの生 HTML(ページ順)と、その (パス, digest) の列。"""
    from quiz_sift.extract.udemy import FetchedPage

    paths = sorted((out_root / RAW_DIR).glob("page-*.html"))
    assert paths, f"保存済みの生 HTML が無い: {out_root / RAW_DIR}"
    pages = [FetchedPage(path.relative_to(out_root).as_posix(), path.read_text(encoding="utf-8")) for path in paths]
    raw = [(page.url, _digest(path.read_bytes())) for page, path in zip(pages, paths, strict=True)]
    return pages, raw


def _saved_questions(pages: "Sequence[FetchedPage]") -> dict[str, tuple[frozenset[str], frozenset[str], str]]:
    """抽出できた問題 ID → (受験者の選択, 抽出した正解の集合, selector_kind)。"""
    from quiz_sift.extract.html_tree import parse_html
    from quiz_sift.extract.udemy import OPTION, OPTION_LABELS, QUESTION_BLOCK_CLASS_PREFIX, extract, is_user_selected

    choices: dict[str, frozenset[str]] = {}
    for page_number, page in enumerate(pages, start=1):
        blocks = parse_html(page.html).find_by_class_prefix(QUESTION_BLOCK_CLASS_PREFIX)
        for number, block in enumerate(blocks, start=1):
            options = block.find_all(*OPTION)
            selected = (
                label for label, option in zip(OPTION_LABELS, options, strict=False) if is_user_selected(option)
            )
            choices[f"p{page_number:03d}-q{number:03d}"] = frozenset(selected)
    return {
        question.local_id: (
            choices[question.local_id],
            frozenset(question.answer_labels),
            question.evidence.selector_kind.value,
        )
        for question in extract(pages).questions
    }


def _parse_label_args(items: list[str]) -> dict[str, frozenset[str]] | str:
    """`p001-q010=A,C` の列を読む。読めなければ理由の文字列。"""
    labels: dict[str, frozenset[str]] = {}
    for item in items:
        question_id, sep, cell = item.partition("=")
        if not sep or not re.fullmatch(r"p\d{3}-q\d{3}", question_id) or question_id in labels:
            return f"--label は p001-qNNN=<A-F をカンマ区切り> の形で、問題 ID を重複させない: {item!r}"
        try:
            labels[question_id] = parse_labels(cell)
        except AssertionError as error:
            return str(error)
    return labels or "--label を1件以上渡す"


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tests/measurements/test_extraction_logs.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "fetch-run", "golden-candidates", "golden", "rerun", "deck-source"):
        sub = commands.add_parser(name)
        sub.add_argument("--out-root", type=Path, required=True, help="out/ を持つリポジトリのルート")
        if name.startswith("fetch"):
            sub.add_argument("--url", default=DEFAULT_URL)
        if name == "golden":
            sub.add_argument("--label", action="append", default=[], help="正解の集合ラベル。p001-q010=A,C")
            sub.add_argument("--label-source", required=True, help="ラベルを誰が何を見て読んだか(ログに逐語で残す)")
        if name == "deck-source":
            sub.add_argument("--decision", choices=("not-needed", "refetched"), required=True)
            sub.add_argument("--reason", default=NO_SELECTION, help="期待件数に届かなかった理由")
    args = parser.parse_args(argv)
    out_root: Path = args.out_root.resolve()
    command = ["PYTHONPATH=src", "python", "tests/measurements/test_extraction_logs.py", *argv]

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

    pages, raw = _saved_pages(out_root)
    if args.command == "rerun":
        from quiz_sift.extract.udemy import extract
        from quiz_sift.extract.writer import write_questions, write_report

        result = extract(pages)
        write_questions(out_root / QUESTIONS, result.questions, out_root)
        write_report(out_root / REPORT, result, out_root)
        RERUN_LOG.write_text(render_rerun_log(rerun_counts(pages), raw, command, _commit()), encoding="utf-8")
        return 0
    if args.command == "deck-source":
        counts = rerun_counts(pages)
        decision = NOT_NEEDED if args.decision == "not-needed" else REFETCHED
        REFETCH_LOG.write_text(
            render_refetch_log(counts.extracted, counts.pairs, decision, args.reason, raw, command, _commit()),
            encoding="utf-8",
        )
        return 0

    questions = _saved_questions(pages)
    if args.command == "golden-candidates":
        for question_id, (choice, extracted, kind) in questions.items():
            wrong = "  (受験者の誤答)" if choice != extracted else ""
            print(f"{question_id} user={format_labels(choice)} extracted={format_labels(extracted)} {kind}{wrong}")
        return 0
    labels = _parse_label_args(args.label)
    if isinstance(labels, str):
        print(labels, file=sys.stderr)
        return 2
    unknown = sorted(set(labels) - set(questions))
    if unknown:
        print(f"抽出できた問題に無い ID: {unknown}", file=sys.stderr)
        return 2
    rows = [
        GoldenRow(qid, questions[qid][0], label, questions[qid][1], questions[qid][2]) for qid, label in labels.items()
    ]
    GOLDEN_NOFM_LOG.write_text(render_golden_log(rows, raw, command, _commit(), args.label_source), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
