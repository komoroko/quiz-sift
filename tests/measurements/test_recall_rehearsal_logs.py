"""合成デッキを実 CLI で recall 段まで通したリハーサルの記録の検査(T-068 / C-026、NFR-7 合成リハーサル)。

実 CLI はサンドボックスに無い(ADR-013)ので、証拠は**ホストで取った実測**で、このテストは記録した md の中身を
機械判定する。記録もこのファイルから作る:

- `python tests/measurements/test_recall_rehearsal_logs.py rehearse --out-root <作業ルート>` は、空の作業ルートに
  `.gitignore` と合成デッキ(`tests/fixtures/decks/synthetic-nofm/`)を写し、製品の CLI(`python -m quiz_sift.cli`)
  で `cluster --fresh-run` を走らせ、exit 0 なら同じ列に `recall --resume <run_id>` を足す。各実行の argv・実行時刻・
  コミット ID・終了コード・run_id・標準出力(成果物のパス)を `<作業ルート>/out/synthetic-nofm/t068/journal.jsonl`
  へ追記する。標準エラーは同じディレクトリのファイルに置き、render は読まない。
- `... render --out-root <作業ルート>` は journal・`meter.jsonl`・recall の成果物の `report` から
  `docs/test/measurements/recall-rehearsal.md` を書く。LLM は呼ばない。開始行と終了行は加工せずに写す。
  レート枠の推定に使う前サイクルの列は `docs/test/measurements/full-run.md` の終了行から読む。

レート枠の使用率は utilization の差分から外挿しない(小数2桁に量子化されるので、10〜20 問の差分は 0 か過大に
なる。D-218)。呼び出し数とトークン数を 190 問に外挿し、前サイクルの列の「utilization の増分 / トークン数」を
掛けて推定する。utilization の生の値も併記する。

判定(各受入基準に1本):

- A-2: 記録のデッキのダイジェストがフィクスチャと一致し、cluster と recall の実行が同じ列で、recall の実行が
  exit 0 で終わり、想起素材の検査の件数 `CHECKS` がすべて 0 で、素材が全問ぶんあり、含意判定が `consistent`。
- A-3: 全問の再生成回数(0〜`MAX_REGENERATIONS`)と、呼び出し数・トークン数・190 問への外挿・前サイクルの
  列の比から推定した枠の使用率・utilization の生の値があり、どれも記録した終了行から計算し直した値と一致する。
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
MEASUREMENTS = ROOT / "docs" / "test" / "measurements"
REHEARSAL_LOG = MEASUREMENTS / "recall-rehearsal.md"
FULL_LOG = MEASUREMENTS / "full-run.md"
FIXTURE = ROOT / "tests" / "fixtures" / "decks" / "synthetic-nofm"

DECK = "synthetic-nofm"
METER_LOG = "meter.jsonl"
JOURNAL_DIR = "t068"
JOURNAL = "journal.jsonl"
STAGES = ("cluster", "recall")

CHECKS = (
    "ungrounded_keys",
    "missing_options",
    "duplicate_keys",
    "generic_keys",
    "verbatim",
    "empty",
    "answer_mismatch",
)
"""想起素材の検査の件数(設計 R-4 節「検査」)。製品のコードから読まず、設計の一覧をここへ写す。"""
MAX_REGENERATIONS = 2
"""1問の再生成の上限(設計 R-4 節: 生成は `1 + MAX_REGENERATIONS` = 3 回まで)。"""
TARGET_QUESTIONS = 190
"""外挿先の問題数(フル処理のデッキ)。"""
PREVIOUS_RUN = "591e7bb9ae6145d284a0ec993371c59c"
"""前サイクルでクラスタ段を完走し recall 段まで進んだ列(full-run.md、D-164)。"""
WINDOWS = ("five_hour", "seven_day")
TOKEN_BUCKETS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")

_RUN_ID_LINE = re.compile(r"^run_id: ([0-9a-f]+)$", re.MULTILINE)
_END_LINE = re.compile(r'^\{"record": "end".*$', re.MULTILINE)


# ---- 共通の計算 ----


def fixture_digest(directory: Path = FIXTURE) -> str:
    """デッキのファイル名とバイト列から作るダイジェスト。記録がこのフィクスチャから取られたことを示す。"""
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.iterdir() if p.is_file()):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return "sha256:" + digest.hexdigest()


def fixture_questions(directory: Path = FIXTURE) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for path in sorted(directory.glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def total_tokens(tokens: Mapping[str, int]) -> int:
    """終了行の `tokens` の4区分の和。前サイクルの比もこの和で取る。"""
    return sum(tokens[bucket] for bucket in TOKEN_BUCKETS)


def usage_per_token(ends: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """実行ごとの utilization の増分(開始時と終了時の枠が同じもの)の和を、それらの実行のトークン数の和で割る。"""
    ratios = {}
    for window in WINDOWS:
        rises = [
            (end["quota_at_end"][window]["utilization"] - end["quota_at_start"][window]["utilization"], end)
            for end in ends
            if end["quota_at_start"] is not None
            and end["quota_at_end"] is not None
            and end["quota_at_start"][window]["resetsAt"] == end["quota_at_end"][window]["resetsAt"]
        ]
        assert rises, f"{window} の増分を取れる実行が無い"
        ratios[window] = sum(rise for rise, _ in rises) / sum(total_tokens(end["tokens"]) for _, end in rises)
    return ratios


def previous_ends(full_log: str) -> list[dict[str, Any]]:
    """前サイクルの列 `PREVIOUS_RUN` の終了行(full-run.md に写したもの)。"""
    ends = [json.loads(m.group(0)) for m in _END_LINE.finditer(full_log)]
    found = [end for end in ends if end["run_id"] == PREVIOUS_RUN]
    assert found, f"full-run.md に列 {PREVIOUS_RUN} の終了行が無い"
    return found


def usage_estimate(ends: Sequence[Mapping[str, Any]], questions: int, ratios: Mapping[str, float]) -> dict[str, Any]:
    """リハーサルの呼び出し数・トークン数と、その 190 問への外挿、外挿したトークン数から推定した枠の使用率。"""
    calls = sum(end["llm_calls"] for end in ends)
    invocations = sum(end["provider_invocations"] for end in ends)
    tokens = sum(total_tokens(end["tokens"]) for end in ends)
    scale = TARGET_QUESTIONS / questions
    return {
        "calls": calls,
        "invocations": invocations,
        "tokens": tokens,
        "calls_190": calls * scale,
        "invocations_190": invocations * scale,
        "tokens_190": tokens * scale,
        **{f"estimated_{window}": ratios[window] * tokens * scale for window in WINDOWS},
    }


# ---- ホストでの実行(`python tests/measurements/test_recall_rehearsal_logs.py ...`) ----


def _journal_dir(out_root: Path) -> Path:
    return out_root / "out" / DECK / JOURNAL_DIR


def _read_journal(out_root: Path) -> list[dict[str, Any]]:
    path = _journal_dir(out_root) / JOURNAL
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _cli_version() -> str:
    return subprocess.run(["claude", "--version"], check=True, capture_output=True, text=True).stdout.strip()


def _exec(out_root: Path, number: int, stage_args: Sequence[str]) -> dict[str, Any]:
    """製品の CLI を1回走らせ、journal へ追記して、その行を返す。"""
    directory = _journal_dir(out_root)
    stage, *rest = stage_args
    argv = [sys.executable, "-m", "quiz_sift.cli", "--root", str(out_root), stage, DECK, *rest]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    stdout_path = directory / f"{number:03d}.stdout"
    stderr_path = directory / f"{number:03d}.stderr"
    started_at = datetime.now().astimezone().isoformat()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        exit_code = subprocess.run(argv, env=env, stdout=stdout, stderr=stderr, check=False).returncode
    run_ids = _RUN_ID_LINE.findall(stderr_path.read_text(encoding="utf-8", errors="backslashreplace"))
    entry = {
        "entry": "execution",
        "argv": argv,
        "started_at": started_at,
        "commit": _commit(),
        "exit_code": exit_code,
        "run_id": run_ids[0] if run_ids else None,
        "stdout": stdout_path.read_text(encoding="utf-8").strip(),
        "stderr": stderr_path.name,
    }
    with (directory / JOURNAL).open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"{stage}: exit_code={exit_code} run_id={entry['run_id']} stderr={stderr_path}", flush=True)
    return entry


def _rehearse(args: argparse.Namespace) -> int:
    """空の作業ルートに合成デッキを写し、cluster を新しい列で、続けて recall を同じ列で走らせる。"""
    out_root: Path = args.out_root.resolve()
    if out_root.exists() and any(out_root.iterdir()):
        raise SystemExit(f"作業ルートが空でない(使い捨ての作業ルートを渡す): {out_root}")
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / ".gitignore").write_text("data/\nout/\n", encoding="utf-8")
    shutil.copytree(FIXTURE, out_root / "data" / DECK)
    directory = _journal_dir(out_root)
    directory.mkdir(parents=True)
    (directory / "conditions.json").write_text(
        json.dumps({"cli_version": _cli_version(), "deck_digest": fixture_digest(out_root / "data" / DECK)}),
        encoding="utf-8",
    )
    cluster = _exec(out_root, 1, ["cluster", "--fresh-run"])
    if cluster["exit_code"] != 0 or cluster["run_id"] is None:
        return int(cluster["exit_code"]) or 1
    recall = _exec(out_root, 2, ["recall", "--resume", cluster["run_id"]])
    return int(recall["exit_code"])


# ---- 記録の書き出し(`render`) ----


def _meter_lines(out_root: Path) -> list[tuple[str, dict[str, Any]]]:
    lines = []
    for raw in (out_root / "out" / DECK / METER_LOG).read_text(encoding="utf-8").splitlines():
        lines.append((raw, json.loads(raw)))
    return lines


def _paired(
    entries: Sequence[Mapping[str, Any]], meter: Sequence[tuple[str, dict[str, Any]]]
) -> list[tuple[Mapping[str, Any], str, str]]:
    """journal の実行を、その実行が書いた開始行・終了行と対にする。作業ルートは使い捨てなので順に1対1。"""
    starts = [(raw, line) for raw, line in meter if line["record"] == "start"]
    assert len(starts) == len(entries), f"開始行 {len(starts)} 行と実行 {len(entries)} 件が対にならない"
    out = []
    for entry, (start_raw, start) in zip(entries, starts, strict=True):
        assert start["run_id"] == entry["run_id"], "開始行の run_id が実行と違う"
        assert start["stage"] == _stage_of(entry), "開始行の段が実行と違う"
        ends = [raw for raw, line in meter if line["record"] == "end" and line["execution_id"] == start["execution_id"]]
        assert len(ends) == 1, f"実行 {start['execution_id']} の終了行が {len(ends)} 行ある"
        out.append((entry, start_raw, ends[0]))
    return out


def _stage_of(entry: Mapping[str, Any]) -> str:
    argv: list[str] = entry["argv"]
    return argv[argv.index("--root") + 2]


def _execution_block(number: int, entry: Mapping[str, Any], start: str, end: str) -> str:
    argv = "\n".join(entry["argv"])
    return f"""## 実行 {number}({_stage_of(entry)})

| 項目 | 値 |
|---|---|
| 実行時刻 | {entry["started_at"]} |
| コミット | `{entry["commit"]}` |
| 終了コード | `{entry["exit_code"]}` |
| run_id | `{entry["run_id"]}` |

argv:

```text
{argv}
```

開始行:

```json
{start}
```

終了行:

```json
{end}
```
"""


def _fmt(value: float) -> str:
    return f"{value:.6g}"


def render_log(
    conditions: Mapping[str, str],
    questions: Sequence[Mapping[str, Any]],
    executions: Sequence[tuple[Mapping[str, Any], str, str]],
    recall: Mapping[str, Any],
    full_log: str,
) -> str:
    report = recall["report"]
    ends = [json.loads(end) for _, _, end in executions]
    prior = previous_ends(full_log)
    ratios = usage_per_token(prior)
    usage = usage_estimate(ends, len(questions), ratios)
    claims = sum(len(m["answers"]) + 1 for m in recall["materials"])
    multi = sum(len(q["answers"]) > 1 for q in questions)
    check_rows = "\n".join(f"| `{name}` | {report[name]} |" for name in CHECKS)
    regen_rows = "\n".join(f"| `{q}` | {n} |" for q, n in report["regenerations"].items())
    raw_rows = "\n".join(
        f"| {i} | `{end['execution_id']}` | {window} | {end['quota_at_start'][window]['utilization']} "
        f"| {end['quota_at_end'][window]['utilization']} | {end['quota_at_end'][window]['resetsAt']} |"
        for i, end in enumerate(ends, start=1)
        for window in WINDOWS
    )
    prior_tokens = sum(total_tokens(end["tokens"]) for end in prior)
    ratio_rows = "\n".join(f"| {window} | {_fmt(ratios[window])} |" for window in WINDOWS)
    blocks = "\n".join(_execution_block(i, e, s, end) for i, (e, s, end) in enumerate(executions, start=1))
    return f"""# 実測ログ: 合成デッキの recall 段までのリハーサル

NFR-7 の合成リハーサル(C-026)。合成デッキ `tests/fixtures/decks/synthetic-nofm/` を使い捨ての作業ルートに写し、
ホストの実 CLI で `cluster --fresh-run` を走らせ、同じ列に `recall --resume` を足した記録である。

この md は `tests/measurements/test_recall_rehearsal_logs.py render` が journal・`meter.jsonl`・recall の成果物の
`report` から作った。LLM は呼んでいない。開始行と終了行は meter.jsonl の行を加工せずに写している。合成デッキの
文は教材ではないが、想起素材の本文は写さず、件数と ID だけを書く。

## 実行条件

| 項目 | 値 |
|---|---|
| デッキ | `{DECK}` |
| デッキのダイジェスト | `{conditions["deck_digest"]}` |
| 問題数 | {len(questions)} |
| 複数正解の問題数 | {multi} |
| CLI | `{conditions["cli_version"]}` |
| 実行数 | {len(executions)} |

## 想起素材の検査

recall の成果物の `report` の件数。成果物は、検査と含意判定をすべて通ったときだけ書かれる
(`recall.generate.generate_recall_set`)。

| 検査 | 件数 |
|---|---|
{check_rows}

## 含意判定

| 項目 | 値 |
|---|---|
| 素材の件数 | {len(recall["materials"])} |
| 判定した主張の件数 | {claims} |
| 判定 | `consistent` |

主張は、素材ごとの答えの要素と識別ポイント1件である。判定が `consistent` でなければ `EntailmentRejected` で
止まり、成果物は書かれない。

## 問題ごとの再生成回数

| 問題 | 再生成回数 |
|---|---|
{regen_rows}

## 呼び出し数とトークン数

トークン数は終了行の `tokens` の4区分(input・cache_creation_input・cache_read_input・output)の和。190 問への
外挿は、cluster と recall の2実行の和を問題数の比 {TARGET_QUESTIONS}/{len(questions)} で伸ばした値である。
cluster 段の分類は全問を1度に見るので問題数に比例しないが、ここでは比例として扱う。

| 項目 | リハーサル | {TARGET_QUESTIONS} 問への外挿 |
|---|---|---|
| 呼び出し数 | {usage["calls"]} | {usage["calls_190"]:.2f} |
| プロバイダ呼び出し数 | {usage["invocations"]} | {usage["invocations_190"]:.2f} |
| トークン数 | {usage["tokens"]} | {usage["tokens_190"]:.2f} |

## レート枠

utilization の生の値(終了行の `quota_at_start`・`quota_at_end`)。小数2桁に量子化されているので、差分を外挿には
使わない(D-218)。

| 実行 | execution_id | 枠 | 開始時 | 終了時 | resetsAt |
|---|---|---|---|---|---|
{raw_rows}

前サイクルの列 `{PREVIOUS_RUN}`(full-run.md の終了行 {len(prior)} 行、トークン数 {prior_tokens})で、
実行ごとの utilization の増分の和をトークン数の和で割った比:

| 枠 | utilization / トークン |
|---|---|
{ratio_rows}

{TARGET_QUESTIONS} 問へ外挿したトークン数にこの比を掛けた、cluster から recall までの枠の使用率の推定:

| 枠 | 推定使用率 |
|---|---|
| five_hour | {_fmt(usage["estimated_five_hour"])} |
| seven_day | {_fmt(usage["estimated_seven_day"])} |

{blocks}"""


def _render(args: argparse.Namespace) -> int:
    out_root: Path = args.out_root.resolve()
    directory = _journal_dir(out_root)
    conditions = json.loads((directory / "conditions.json").read_text(encoding="utf-8"))
    entries = [e for e in _read_journal(out_root) if e["run_id"] is not None]
    executions = _paired(entries, _meter_lines(out_root))
    recall_entry = next(e for e in entries if _stage_of(e) == "recall" and e["exit_code"] == 0)
    recall = json.loads(Path(recall_entry["stdout"]).read_bytes())
    REHEARSAL_LOG.write_text(
        render_log(
            conditions,
            fixture_questions(out_root / "data" / DECK),
            executions,
            recall,
            FULL_LOG.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    return 0


# ---- 記録の読み取り ----


def _sections(text: str, prefix: str) -> list[tuple[str, str]]:
    out = []
    for chunk in text.split("\n## ")[1:]:
        heading, _, body = chunk.partition("\n")
        if heading.startswith(prefix):
            out.append((heading, body))
    return out


def _section(text: str, heading: str) -> str:
    found = [body for title, body in _sections(text, heading) if title == heading]
    assert len(found) == 1, f"見出し {heading} の節がちょうど1つない"
    return found[0]


def _blocks(body: str, lang: str) -> list[str]:
    return re.findall(rf"^```{lang}\n(.*?)\n```$", body, re.MULTILINE | re.DOTALL)


def _rows(body: str) -> list[list[str]]:
    """表の行(見出しと区切りを除く)をセルの列で返す。"""
    rows = []
    for line in body.splitlines():
        if not line.startswith("|") or re.fullmatch(r"\|(-+\|)+", line):
            continue
        rows.append([cell.strip().strip("`") for cell in line.strip().strip("|").split("|")])
    return rows[1:] if rows else rows


def _table(body: str) -> dict[str, list[str]]:
    return {row[0]: row[1:] for row in _rows(body)}


def _number(value: str) -> float:
    assert re.fullmatch(r"-?\d+(\.\d+)?(e[+-]?\d+)?", value), f"数値でない: {value!r}"
    return float(value)


def _close(recorded: str, expected: float) -> bool:
    """記録は有効数字6桁で書くので相対誤差で比べる。枠の比は 1e-8 の桁なので絶対誤差では比べられない。"""
    return math.isclose(_number(recorded), expected, rel_tol=1e-5)


def executions_of(text: str) -> list[dict[str, Any]]:
    executions = []
    for heading, body in _sections(text, "実行 "):
        table = _table(body)
        lines = _blocks(body, "json")
        assert len(lines) == 2, f"{heading}: 開始行と終了行が揃っていない"
        start, end = json.loads(lines[0]), json.loads(lines[1])
        assert start["record"] == "start" and end["record"] == "end", f"{heading}: 開始行・終了行の record が違う"
        assert start["execution_id"] == end["execution_id"], f"{heading}: execution_id が違う"
        assert start["run_id"] == end["run_id"] == table["run_id"][0], f"{heading}: run_id が違う"
        exit_code = int(table["終了コード"][0])
        assert end["stop"]["exit_code"] == exit_code, f"{heading}: 終了行の終了コードが実行と違う"
        executions.append({"heading": heading, "exit_code": exit_code, "start": start, "end": end})
    assert executions, "実行の節が無い"
    return executions


# ---- 判定 ----


def check_recall_passed(text: str) -> None:
    """A-2: 合成デッキの cluster → recall が同じ列で走り、recall が exit 0、検査がすべて 0、含意判定が通った。"""
    conditions = _table(_section(text, "実行条件"))
    assert conditions["デッキのダイジェスト"][0] == fixture_digest(), "記録のデッキがフィクスチャと違う"
    questions = fixture_questions()
    assert int(conditions["問題数"][0]) == len(questions), "問題数がフィクスチャと違う"
    executions = executions_of(text)
    assert [e["start"]["stage"] for e in executions] == list(STAGES), "実行が cluster → recall の順でない"
    assert executions[0]["start"]["cache_mode"] == "fresh", "cluster が新しい列で走っていない"
    assert executions[1]["start"]["cache_mode"] == "resume", "recall が同じ列への追加になっていない"
    assert len({e["start"]["run_id"] for e in executions}) == 1, "cluster と recall の列が違う"
    for execution in executions:
        assert execution["exit_code"] == 0, f"{execution['heading']} が exit 0 で終わっていない"
        assert execution["end"]["stop"]["exit_reason"] is None, f"{execution['heading']} が例外で終わった"
        assert execution["start"]["metered_env_names"] == [], f"{execution['heading']} に metered 認証変数がある"
    checks = _table(_section(text, "想起素材の検査"))
    assert set(checks) == set(CHECKS), f"検査の一覧が設計と違う: {sorted(set(checks) ^ set(CHECKS))}"
    for name, (count,) in checks.items():
        assert int(count) == 0, f"検査 {name} の件数が 0 でない: {count}"
    entailment = _table(_section(text, "含意判定"))
    assert int(entailment["素材の件数"][0]) == len(questions), "素材が全問ぶん無い"
    minimum = sum(len(q["answers"]) + 1 for q in questions)
    assert int(entailment["判定した主張の件数"][0]) == minimum, "判定した主張が答えの要素と識別ポイントの数と違う"
    assert entailment["判定"][0] == "consistent", "含意判定が consistent でない"


def check_usage_recorded(text: str) -> None:
    """A-3: 再生成回数、呼び出し数・トークン数とその外挿、前サイクルの比からの推定、utilization の生の値。"""
    questions = fixture_questions()
    regenerations = _table(_section(text, "問題ごとの再生成回数"))
    assert set(regenerations) == {q["id"] for q in questions}, "再生成回数が全問ぶん無い"
    for qid, (count,) in regenerations.items():
        assert 0 <= int(count) <= MAX_REGENERATIONS, f"問題 {qid} の再生成回数が範囲外: {count}"
    ends = [e["end"] for e in executions_of(text)]
    ratios = usage_per_token(previous_ends(FULL_LOG.read_text(encoding="utf-8")))
    usage = usage_estimate(ends, len(questions), ratios)
    counts = _table(_section(text, "呼び出し数とトークン数"))
    for label, key in (("呼び出し数", "calls"), ("プロバイダ呼び出し数", "invocations"), ("トークン数", "tokens")):
        recorded, extrapolated = counts[label]
        assert int(recorded) == usage[key], f"{label} が終了行の和と違う"
        assert _close(extrapolated, usage[f"{key}_190"]), f"{label} の外挿が計算し直した値と違う"
    quota = _section(text, "レート枠")
    tables = quota.split("\n\n")
    raw = [row for row in _rows(quota) if len(row) == 6]
    expected_raw = [
        [str(end["quota_at_start"][w]["utilization"]), str(end["quota_at_end"][w]["utilization"])]
        for end in ends
        for w in WINDOWS
    ]
    assert [row[3:5] for row in raw] == expected_raw, "utilization の生の値が終了行と違う"
    ratio_table = next(_table(t) for t in tables if "utilization / トークン" in t)
    estimate_table = next(_table(t) for t in tables if "推定使用率" in t)
    for window in WINDOWS:
        assert _close(ratio_table[window][0], ratios[window]), f"{window} の比が前サイクルの列から計算した値と違う"
        assert _close(estimate_table[window][0], usage[f"estimated_{window}"]), f"{window} の推定が違う"


# ---- フィクスチャの判定 ----


def test_synthetic_deck_has_ja_en_and_multi_answer_questions() -> None:
    """A-1 の形: 10〜20 問、日本語と英語の問題文、複数正解の問題、全件が取り込みを通る。"""
    from quiz_sift.domain.ids import DeckId
    from quiz_sift.ingest.deck import ingest_deck

    questions = fixture_questions()
    assert 10 <= len(questions) <= 20
    japanese = [q for q in questions if re.search(r"[぀-ヿ]", q["stem"])]
    assert japanese and len(japanese) < len(questions), "日本語と英語の問題文が両方ない"
    assert any(len(q["answers"]) > 1 for q in questions), "複数正解の問題が無い"
    ingested = ingest_deck(FIXTURE.parent, DeckId(DECK))
    assert ingested.report.accepted == len(questions) and not ingested.report.rejected


# ---- 実測ログに対する判定 ----


def _read() -> str:
    assert REHEARSAL_LOG.is_file(), f"実測ログが無い: {REHEARSAL_LOG.relative_to(ROOT)}"
    return REHEARSAL_LOG.read_text(encoding="utf-8")


def test_c026_recall_stage_passed_on_the_synthetic_deck() -> None:
    """A-2"""
    check_recall_passed(_read())


def test_c026_record_has_regenerations_and_extrapolated_usage() -> None:
    """A-3"""
    check_usage_recorded(_read())


# ---- 判定が赤になること ----


def _broken(check: Any, text: str) -> None:
    assert text != _read(), "記録を壊せていない"
    try:
        check(text)
    except AssertionError:
        return
    raise AssertionError("壊した記録で判定が赤にならない")


def test_record_with_a_nonzero_check_is_red() -> None:
    text = re.sub(r"^\| `missing_options` \| 0 \|$", "| `missing_options` | 1 |", _read(), count=1, flags=re.M)
    _broken(check_recall_passed, text)


def test_record_with_a_failed_recall_is_red() -> None:
    text = _read()
    heading, body = _sections(text, "実行 ")[-1]
    _broken(check_recall_passed, text.replace(body, body.replace("| 終了コード | `0` |", "| 終了コード | `1` |"), 1))


def test_record_from_another_deck_is_red() -> None:
    _broken(check_recall_passed, re.sub(r"sha256:[0-9a-f]{64}", "sha256:" + "0" * 64, _read(), count=1))


def test_record_missing_a_regeneration_is_red() -> None:
    text = _read()
    first = _rows(_section(text, "問題ごとの再生成回数"))[0]
    _broken(check_usage_recorded, text.replace(f"| `{first[0]}` | {first[1]} |\n", "", 1))


def test_record_with_a_wrong_extrapolation_is_red() -> None:
    text = re.sub(r"^(\| トークン数 \| \d+ \| )[^|]+\|$", r"\g<1>1 |", _read(), count=1, flags=re.M)
    _broken(check_usage_recorded, text)


def test_record_with_a_wrong_estimate_is_red() -> None:
    text = re.sub(r"^\| seven_day \| [^|]+ \|$", "| seven_day | 0 |", _read(), count=1, flags=re.M)
    _broken(check_usage_recorded, text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="test_recall_rehearsal_logs.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("rehearse", "render"):
        command = commands.add_parser(name)
        command.add_argument("--out-root", type=Path, required=True, help="使い捨ての作業ルート")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT / "src"))
    return {"rehearse": _rehearse, "render": _render}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
