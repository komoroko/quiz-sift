"""フル処理の実行の列と再現性・無音判定の記録の検査(T-070 / C-025・C-007。判定は元の T-047 の A-1〜A-6)。

実教材は NFR-2 によりコミットできず、実 CLI と CosyVoice はサンドボックスに無い(ADR-012、ADR-013)。
そのため証拠は**人がホストで取った実測の記録**で、このテストは記録した md の中身を機械判定する。実行と記録の
整形は、人が同じ作業ルートに対してこのファイルから行う(D-190、D-220):

- `python tests/measurements/test_full_run_logs.py exec --out-root <作業ルート> --deck <デッキ> --kind full
  cluster --fresh-run` は製品の CLI(`python -m quiz_sift.cli`)を子プロセスで1回走らせ、argv・実行時刻・
  コミット ID・終了コード・`run_id` を `<作業ルート>/out/<デッキ>/t071/journal.jsonl` へ追記する。標準エラーは
  同じディレクトリのファイルに置く(コミットしない)。フル処理の列は `--kind full` で `cluster --fresh-run`
  から始め、続く段と週次枠で止まった段を `--resume <run_id>` で足す。再現性の列は `--kind repro` で
  `cluster --fresh-run` を1回ずつ走らせる。
- `... silence --out-root <作業ルート> --deck <デッキ>` は、フル処理の列が書き出した mp3 に無音構造判定
  (`audio.silence`)を掛けた結果を journal へ追記する。
- `... render --out-root <作業ルート> --deck <デッキ>` は journal・`meter.jsonl`・標準エラーの子プロセスの記録から
  `docs/test/measurements/` の full-run.md・reproducibility.md・audio-silence.md を書く。LLM も TTS も呼ばない。
  `meter.jsonl` の開始行と終了行は加工せずに写す。標準エラーからは子プロセスの数とクラスタ段の実行サマリ(数値)
  だけを取る。出力は件数・ID・ダイジェスト・環境変数名・レート枠の値だけで、教材本文を含まない(NFR-2)。
- `... precheck --out-root <作業ルート> --deck <デッキ>` は、最後のフル処理の列が書いた cluster と recall の成果物に、
  検査 `option_key` と日本語のラベル判定(D-247)だけをかける。LLM は呼ばない。recall の成果物は素材だけを読み
  (report の形が変わっても読める)、option_key で落ちる問題の ID と、ラベル列挙が出るトラックの番号と系列と、
  そのトラックに option_key で落ちる問題があるかを出す。本文は出さない。

ループ側のテストは out/ を読まない。読むのはコミットされた md と、テストが一時ディレクトリに作る合成の
作業ルートだけである(D-220)。

判定(元の T-047 の受入基準に1つずつ。`check_*`):

- A-1: フル処理の列の各実行に argv・実行時刻・コミット ID と、その実行の開始行・終了行がある。終了行の
  キー集合は `END_KEYS` と一致する。最後の終了行の `sequence.status` が `completed`、
  `sequence.estimated_cost.status` が `recorded`、`relative_diff` が数値。問題数が 100〜200。
- A-2: 再現性の列がちょうど2本で、どちらも `cluster --fresh-run` の1実行だけで exit 0。各列で
  `classify_clusters` の `llm_calls` が 1、`provider_invocations` が ⌈N/25⌉ で、2本の和がその2倍。
  メソッド別の内訳の和が終了行の値と一致し、他メソッドの `llm_calls` が子プロセス数から TRUNCATED の出し直しを
  除いた数と一致する。割当一致率が、記録した2つの割当から計算し直した値と一致する。
- A-3: 無音判定の結果が全クラスタについて真で、クラスタ一覧のどのクラスタにも判定したファイルがある。
- A-4: full-run.md に M/N・最大クラスタ比率・「その他」比率が数値としてあり、記録した割当から計算し直した
  値と一致する。
- A-5: 各実行の開始行の `metered_env_names` が空で、`env_names` と `METERED_ENV_NAMES` の積集合が空。
  列が `completed`、`overage_invocations` が 0、最後の終了行の `failures_by_kind` がすべて 0。
- A-6: 列の `overage_check` が `passed` か `unknown`。`unknown` なら
  `invocations_without_rate_limit_event` の件数が本文にある。各実行の `quota_at_start` / `quota_at_end` が
  あり、`null` の件数が列の `quota_missing` と一致する。
- 試行の予算(D-181): フル処理の列は `--fresh-run` で始めた列1本を1回と数え、同じ列の `--resume` は数えない。
  再現性の列は予算の外。`MAX_ATTEMPTS` 本を超えると赤。
"""

import argparse
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
MEASUREMENTS = ROOT / "docs" / "test" / "measurements"
FULL_LOG = MEASUREMENTS / "full-run.md"
REPRO_LOG = MEASUREMENTS / "reproducibility.md"
SILENCE_LOG = MEASUREMENTS / "audio-silence.md"
PARTIAL_ORIGINAL = ROOT / "docs" / "archive" / "2026-10-06-fisrt" / "test" / "measurements" / "full-run.md"
"""前サイクルの途中までの記録の原本(D-164)。full-run.md の置き場所は T-071 の記録に空けた(T-070)。"""

METER_LOG = "meter.jsonl"
JOURNAL_DIR = "t071"
"""前サイクルの journal(`t047/`)と分ける。試行は、この journal の最初の実行より後の開始行で数える。"""
JOURNAL = "journal.jsonl"

MIN_QUESTIONS, MAX_QUESTIONS = 100, 200
CLASSIFY_BATCH_SIZE = 25
"""分類の分割呼び出し1回あたりの問題数(設計 R-3 節、AR-62)。⌈N/25⌉ の 25。"""
REPRO_SEQUENCES = 2
MAX_ATTEMPTS = 6
"""フル処理の試行の上限(D-181、D-241、D-246、D-247、NFR-7。1回 = `--fresh-run` の列1本)。"""
ATTEMPTS = "試行"

END_KEYS = frozenset(
    {
        "record",
        "run_id",
        "execution_id",
        "started_at",
        "finished_at",
        "elapsed_sec",
        "llm_calls",
        "provider_invocations",
        "model_turns",
        "turns_by_model",
        "max_concurrency",
        "tokens",
        "tokens_by_model",
        "calls_by_model",
        "estimated_cost",
        "disclaimer",
        "prices",
        "overhead",
        "failures_by_kind",
        "failed_invocations",
        "invocations_without_usage",
        "invocations_without_turns",
        "rate_limit_stopped_invocations",
        "ended_by_rate_limit",
        "quota_at_start",
        "quota_at_end",
        "overage_invocations",
        "invocations_without_rate_limit_event",
        "overage_check",
        "stop",
        "sequence",
    }
)
"""設計 NFR-7 節「ログのフィールド一覧」の終了行。製品のコードから読まず、設計の一覧をここへ写す。"""

METERED_ENV_NAMES = frozenset(
    {
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
    }
)
"""要件 NFR-8 が列挙した13個の metered 認証変数。製品の定数から読まず、要件から写す(AR-137)。"""

CLUSTER_METHODS = ("classify_clusters", "summarize_cluster", "check_entailments")
"""cluster 段が呼ぶ `Llm` のメソッド。"""
OTHER_CLUSTER = "other"
"""「その他」クラスタの ID(`cluster.summary.OTHER_LOCAL_ID`)。"""
SILENCE_SAMPLE_RATE = 22050
"""無音判定のためにデコードするサンプルレート。CosyVoice-300M の出力と同じ。"""

_INVOCATION_LINE = re.compile(r"^INFO quiz_sift\.safety\.invocation_log: claude invocation (\{.*\})$", re.MULTILINE)
_SUMMARY_LINE = re.compile(r"^INFO quiz_sift\.cluster\.metrics: (clustering summary: [^\n]*)$", re.MULTILINE)
_SUMMARY_FIELDS = re.compile(r"clustering summary:( \w+=-?\d+(\.\d+)?)+")
_RUN_ID_LINE = re.compile(r"^run_id: ([0-9a-f]+)$", re.MULTILINE)
_TRACK_NAME = re.compile(r"^\d{3}[a-z]?_(?P<deck>.+)_(?P<cluster>[^_]+)\.mp3$")


# ---- ホストでの実行(`python tests/measurements/test_full_run_logs.py ...`) ----


def _journal_dir(out_root: Path, deck: str) -> Path:
    return out_root / "out" / deck / JOURNAL_DIR


def _append_journal(out_root: Path, deck: str, entry: Mapping[str, object]) -> None:
    path = _journal_dir(out_root, deck) / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_journal(out_root: Path, deck: str) -> list[dict[str, Any]]:
    path = _journal_dir(out_root, deck) / JOURNAL
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _exec(args: argparse.Namespace) -> int:
    """製品の CLI を1回走らせ、journal へ追記する。子の終了コードを返す。"""
    out_root: Path = args.out_root
    directory = _journal_dir(out_root, args.deck)
    directory.mkdir(parents=True, exist_ok=True)
    number = len(_read_journal(out_root, args.deck)) + 1 if (directory / JOURNAL).is_file() else 1
    stage, *rest = args.stage_args
    argv = [sys.executable, "-m", "quiz_sift.cli", "--root", str(out_root), stage, args.deck, *rest]
    # この worktree の製品コードを走らせる。CosyVoice への PYTHONPATH は呼び出し側の値を残す。
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (str(ROOT / "src"), os.environ.get("PYTHONPATH"))))}
    stdout_path = directory / f"{number:03d}.stdout"
    stderr_path = directory / f"{number:03d}.stderr"
    started_at = datetime.now().astimezone().isoformat()
    commit = _commit()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        exit_code = subprocess.run(argv, env=env, stdout=stdout, stderr=stderr, check=False).returncode
    # 解析は render で行う。ここで落ちると、戻せない枠を使った実行の記録が journal に残らない。
    run_ids = _RUN_ID_LINE.findall(stderr_path.read_text(encoding="utf-8", errors="backslashreplace"))
    _append_journal(
        out_root,
        args.deck,
        {
            "entry": "execution",
            "kind": args.kind,
            "argv": argv,
            "started_at": started_at,
            "commit": commit,
            "exit_code": exit_code,
            "run_id": run_ids[0] if run_ids else None,
            "stdout": stdout_path.read_text(encoding="utf-8").strip(),
            "stderr": stderr_path.name,
        },
    )
    print(f"exit_code={exit_code} run_id={run_ids[0] if run_ids else None} stderr={stderr_path}", flush=True)
    return exit_code


def _silence(args: argparse.Namespace) -> int:
    """フル処理の列が書き出した mp3 に無音構造判定を掛け、結果を journal へ追記する。"""
    from quiz_sift.audio.codec import decode_mp3
    from quiz_sift.audio.silence import PAUSE_THRESHOLD_SECONDS, detect_structure, has_pause_structure

    audio_dir = args.out_root / "out" / args.deck / "audio"
    files = []
    for path in sorted(audio_dir.glob("*.mp3")):
        pcm = decode_mp3(path, SILENCE_SAMPLE_RATE)
        runs = detect_structure(pcm.samples, pcm.sample_rate)
        inner = [
            run.seconds
            for i, run in enumerate(runs)
            if not run.voiced
            and run.seconds >= PAUSE_THRESHOLD_SECONDS
            and any(r.voiced for r in runs[:i])
            and any(r.voiced for r in runs[i + 1 :])
        ]
        files.append(
            {
                "file": path.name,
                "seconds": round(len(pcm.samples) / 2 / pcm.sample_rate, 3),
                "pauses": len(inner),
                "shortest_pause": round(min(inner), 3) if inner else None,
                "result": has_pause_structure(runs),
            }
        )
    _append_journal(
        args.out_root,
        args.deck,
        {
            "entry": "silence",
            "started_at": datetime.now().astimezone().isoformat(),
            "commit": _commit(),
            "audio_dir": str(audio_dir.relative_to(args.out_root)),
            "sample_rate": SILENCE_SAMPLE_RATE,
            "threshold_seconds": PAUSE_THRESHOLD_SECONDS,
            "files": files,
        },
    )
    print(f"files={len(files)} false={sum(not f['result'] for f in files)}", flush=True)
    return 0


# ---- 記録の整形(`render`。人が作業ルートに対して走らせる) ----


def _method_of_prompt() -> dict[str, str]:
    """system プロンプトの本文 → `Llm` のメソッド名。"""
    from quiz_sift.adapters import llm_prompts

    return {
        llm_prompts.CLASSIFY_SYSTEM: "classify_clusters",
        llm_prompts.SUMMARIZE_SYSTEM: "summarize_cluster",
        llm_prompts.RECALL_SYSTEM: "generate_recall",
        llm_prompts.DESCRIBE_SYSTEM: "describe_term",
        llm_prompts.ENTAILMENT_SYSTEM: "check_entailments",
    }


def invocations_by_method(stderr: str) -> tuple[dict[str, int], dict[str, int]]:
    """標準エラーの `claude` 子プロセスの記録を、`--system-prompt` の値でメソッドに振り分けて数える。

    返すのは (子プロセス数, そのうち TRUNCATED で終わり出し直された子の数)。exit 0 で終わった実行では、TRUNCATED の
    子はすべて出し直されている(出し直しが尽きた呼び出しは段を止める。D-231)。
    """
    methods = _method_of_prompt()
    counts: Counter[str] = Counter()
    reissued: Counter[str] = Counter()
    for raw in _INVOCATION_LINE.findall(stderr):
        record = json.loads(raw)
        argv = record["argv"]
        prompt = argv[argv.index("--system-prompt") + 1]
        if prompt not in methods:
            raise ValueError("既知のどのメソッドの system プロンプトでもない子プロセスの記録がある")
        counts[methods[prompt]] += 1
        if record["failure_kind"] == "truncated":
            reissued[methods[prompt]] += 1
    return dict(counts), dict(reissued)


def _parse_stderr(out_root: Path, deck: str, name: str) -> dict[str, Any]:
    """実行の標準エラーから、メソッド別の子プロセス数と cluster 段の実行サマリ(数値だけ)を読む。"""
    err = (_journal_dir(out_root, deck) / name).read_text(encoding="utf-8", errors="backslashreplace")
    summaries = _SUMMARY_LINE.findall(err)
    summary = summaries[-1] if summaries else None
    if summary is not None and not _SUMMARY_FIELDS.fullmatch(summary):
        raise ValueError("クラスタ段の実行サマリが数値の項目だけでない")
    counts, reissued = invocations_by_method(err)
    return {"invocations_by_method": counts, "reissues_by_method": reissued, "cluster_summary": summary}


def _meter_lines(out_root: Path, deck: str) -> list[tuple[str, dict[str, Any]]]:
    """`meter.jsonl` の読める行を (元の行, 読んだ値) で返す。元の行は加工せずに写すために持つ。"""
    lines = []
    for raw in (out_root / "out" / deck / METER_LOG).read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        lines.append((raw, value))
    return lines


def _stage_of(entry: Mapping[str, Any]) -> str:
    argv: list[str] = entry["argv"]
    return argv[argv.index("--root") + 2]


def _cache_mode_of(entry: Mapping[str, Any]) -> str:
    return "fresh" if "--fresh-run" in entry["argv"] else "resume"


def _paired(
    entries: Sequence[Mapping[str, Any]], meter: Sequence[tuple[str, dict[str, Any]]]
) -> list[tuple[Mapping[str, Any], str, str]]:
    """journal の実行を、その実行が書いた開始行・終了行と対にする。

    開始行は、run_id・段・キャッシュの扱いが argv と同じ開始行のうち、その実行を起動した時刻から次の実行を
    起動した時刻までに書かれたもの。どれにも1つずつ無ければ例外にする(起動前検査に落ちた実行は列に入らないので、
    ここには渡さない)。
    """
    out = []
    for i, entry in enumerate(entries):
        begin = datetime.fromisoformat(entry["started_at"])
        until = datetime.fromisoformat(entries[i + 1]["started_at"]) if i + 1 < len(entries) else None
        starts = [
            (raw, line)
            for raw, line in meter
            if line["record"] == "start"
            and line["run_id"] == entry["run_id"]
            and line["stage"] == _stage_of(entry)
            and line["cache_mode"] == _cache_mode_of(entry)
            and begin <= datetime.fromisoformat(line["started_at"])
            and (until is None or datetime.fromisoformat(line["started_at"]) < until)
        ]
        if len(starts) != 1:
            raise ValueError(f"{entry['started_at']} の実行に対応する開始行が {len(starts)} 行ある")
        start_raw, start = starts[0]
        ends = [raw for raw, line in meter if line["record"] == "end" and line["execution_id"] == start["execution_id"]]
        if len(ends) != 1:
            raise ValueError(f"実行 {start['execution_id']} の終了行が {len(ends)} 行ある")
        out.append((entry, start_raw, ends[0]))
    return out


def _clusters_of(artifact: str) -> dict[str, list[str]]:
    """cluster 段の成果物から、クラスタ ID → 問題 ID の一覧だけを取り出す。要約は写さない(NFR-2)。"""
    data = json.loads(Path(artifact).read_bytes())
    return {row["id"]: list(row["questions"]) for row in data["clusters"]}


def _execution_block(heading: str, entry: Mapping[str, Any], start: str, end: str) -> str:
    argv = "\n".join(entry["argv"])
    return f"""## {heading}

| 項目 | 値 |
|---|---|
| 実行時刻 | {entry["started_at"]} |
| コミット | `{entry["commit"]}` |
| 終了コード | `{entry["exit_code"]}` |

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


def _assignment_block(clusters: Mapping[str, Sequence[str]]) -> str:
    return "```json\n" + json.dumps(dict(sorted(clusters.items())), ensure_ascii=False) + "\n```\n"


def compression_metrics(clusters: Mapping[str, Sequence[str]]) -> dict[str, float]:
    """割当から N・M・M/N・最大クラスタ比率・「その他」比率を計算する(`cluster.metrics` と同じ定義)。"""
    sizes = [len(members) for members in clusters.values() if members]
    n = sum(sizes)
    return {
        "N": n,
        "M": len(sizes),
        "M/N": len(sizes) / n,
        "最大クラスタ比率": max(sizes) / n,
        "「その他」比率": len(clusters.get(OTHER_CLUSTER, ())) / n,
    }


def attempts_of(
    meter: Sequence[tuple[str, dict[str, Any]]], since: datetime, repro: set[str]
) -> list[tuple[str, int, int]]:
    """列を run_id ごとに (run_id, `--fresh-run` の実行数, `--resume` の実行数) で数える(D-181)。

    journal ではなく meter.jsonl の開始行から数えるので、exec を通さずに起動した実行も入る。数えるのは journal の
    最初の実行の起動より後の開始行(それより前は前サイクルの行)で、journal で再現性の列とした run_id は除く。
    """
    counts: dict[str, Counter[str]] = {}
    for _, line in meter:
        if line["record"] != "start" or line["run_id"] in repro or datetime.fromisoformat(line["started_at"]) < since:
            continue
        counts.setdefault(line["run_id"], Counter())[line["cache_mode"]] += 1
    return [(run_id, count["fresh"], count["resume"]) for run_id, count in counts.items()]


def render_full_log(
    deck: str,
    run_id: str,
    executions: Sequence[tuple[Mapping[str, Any], str, str]],
    attempts: Sequence[tuple[str, int, int]],
    clusters: Mapping[str, Sequence[str]],
    summary: str,
) -> str:
    final = json.loads(executions[-1][2])["sequence"]
    metrics = compression_metrics(clusters)
    metric_rows = "\n".join(f"| {key} | {value:.4f} |" for key, value in metrics.items() if key not in ("N", "M"))
    attempt_rows = "\n".join(f"| `{r}` | {fresh} | {resume} |" for r, fresh, resume in attempts)
    blocks = "\n".join(
        _execution_block(f"実行 {i}({_stage_of(entry)})", entry, start, end)
        for i, (entry, start, end) in enumerate(executions, start=1)
    )
    return f"""# 実測ログ: フル処理の実行の列

R-3・R-7・NFR-7・NFR-8(C-025、C-007)の実デッキのフル処理を、1つの `run_id` の実行の列として人がホストで
走らせた記録(元の T-047 の受入基準 A-1・A-4・A-5・A-6 と試行の予算)。列は `cluster --fresh-run` で始め、
続く段と週次枠で止まった段を `--resume <run_id>` で足した(D-190)。

各実行は `tests/measurements/test_full_run_logs.py exec` が製品の CLI を子プロセスとして走らせ、その argv・
実行時刻・コミット ID・終了コードを記録した。この md は人が同じ作業ルートに対して `render` で作った(D-220)。
開始行と終了行は `out/{deck}/meter.jsonl` の行を加工せずに写している。行にあるのは件数・ID・ダイジェスト・
環境変数名・レート枠の値だけで、教材本文は無い(NFR-2)。`tests/measurements/test_full_run_logs.py` がこの記録を
機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| デッキ | `{deck}` |
| run_id | `{run_id}` |
| 実行数 | {len(executions)} |

## {ATTEMPTS}

meter.jsonl の開始行のうち journal の最初の実行より後のものを run_id ごとに数えた(exec を通さずに起動した実行も
入る)。`--fresh-run` で始めた列1本を1回と数え、同じ列の `--resume` は数えない。再現性の列(journal の
`--kind repro`)は予算の外で、ここには入れない(D-181)。上限は {MAX_ATTEMPTS} 本。最後の行が上の列である。

| run_id | --fresh-run | --resume |
|---|---|---|
{attempt_rows}

## 圧縮の指標

cluster 段の成果物の割当から計算した値。合格ラインは置かない(D-004)。

| 項目 | 値 |
|---|---|
| N | {metrics["N"]} |
| M | {metrics["M"]} |
{metric_rows}

cluster 段の標準エラーの実行サマリ:

```text
{summary}
```

## クラスタの割当

クラスタ ID → 問題 ID の一覧(要約は教材から作った文なので写さない)。

{_assignment_block(clusters)}
## 列の集約

最後の終了行の `sequence` から。

| 項目 | 値 |
|---|---|
| status | `{final["status"]}` |
| overage_check | `{final["overage_check"]}` |
| overage_invocations | {final["overage_invocations"]} |
| invocations_without_rate_limit_event | {final["invocations_without_rate_limit_event"]} |
| quota_missing.at_start | {final["quota_missing"]["at_start"]} |
| quota_missing.at_end | {final["quota_missing"]["at_end"]} |

{blocks}"""


def rand_agreement(a: Mapping[str, Sequence[str]], b: Mapping[str, Sequence[str]]) -> float:
    """2つの割当の一致率。問題の対ごとに「同じクラスタか」が両方で一致する対の割合(Rand 指数)。

    クラスタ ID は実行ごとに振り直されるので、ID の対応を取らずに比べられる対の一致で数える。
    """
    label_a = {q: c for c, members in a.items() for q in members}
    label_b = {q: c for c, members in b.items() for q in members}
    if label_a.keys() != label_b.keys():
        raise ValueError("2つの割当の問題集合が違う")
    pairs = list(itertools.combinations(sorted(label_a), 2))
    agreed = sum((label_a[p] == label_a[q]) == (label_b[p] == label_b[q]) for p, q in pairs)
    return agreed / len(pairs)


def render_repro_log(
    deck: str,
    sequences: Sequence[tuple[tuple[Mapping[str, Any], str, str], Mapping[str, Sequence[str]]]],
) -> str:
    parts = []
    for i, ((entry, start, end), clusters) in enumerate(sequences, start=1):
        counts, reissued = entry["invocations_by_method"], entry["reissues_by_method"]
        end_line = json.loads(end)
        calls = {method: counts.get(method, 0) - reissued.get(method, 0) for method in CLUSTER_METHODS}
        calls["classify_clusters"] = end_line["llm_calls"] - sum(
            calls[method] for method in CLUSTER_METHODS if method != "classify_clusters"
        )
        rows = "\n".join(
            f"| {method} | {calls[method]} | {counts.get(method, 0)} | {reissued.get(method, 0)} |"
            for method in CLUSTER_METHODS
        )
        parts.append(
            _execution_block(f"列 {i}", entry, start, end)
            + f"""
メソッド別の内訳:

| メソッド | llm_calls | provider_invocations | 出し直し |
|---|---|---|---|
{rows}

割当:

{_assignment_block(clusters)}"""
        )
    agreement = rand_agreement(sequences[0][1], sequences[1][1])
    body = "\n".join(parts)
    return f"""# 実測ログ: キャッシュを迂回した再クラスタリング

R-3(C-025)の「キャッシュを迂回して再クラスタリングした2回の実行について割当一致率が記録され、その2回の
`llm_calls` が1回分の2倍になる」ことを人がホストで測った記録(元の T-047 の受入基準 A-2)。使ったのは
`cluster --fresh-run` の1実行だけで exit 0 まで走った列2本で、再開した列は使っていない(D-142)。この2列は
フル処理の試行の予算の外である(D-181)。判定は分類の呼び出しで行う(D-155): 各列で `classify_clusters` の
`llm_calls` が 1、`provider_invocations` が ⌈N/25⌉。

開始行と終了行は `out/{deck}/meter.jsonl` の行を加工せずに写している。メソッド別の内訳は、標準エラーにある
`claude` 子プロセス1本ずつの記録を `--system-prompt` の値でメソッドに振り分けて数えた(標準エラーそのものは
写さない)。「出し直し」は、そのうち TRUNCATED で終わり同じ argv と stdin で出し直された子の数(D-231)。
`summarize_cluster` と `check_entailments` は1回の呼び出しが子プロセス1本で、出し直しのほかは再試行しない(D-101)
ので、`llm_calls` は子プロセス数から出し直しを除いた数。`classify_clusters` の `llm_calls` は終了行の
`llm_calls` からその2つを引いた値である。

## 実行条件

| 項目 | 値 |
|---|---|
| デッキ | `{deck}` |
| 列の数 | {len(sequences)} |

## 割当一致率

問題の対ごとに「同じクラスタか」が2本で一致する対の割合(Rand 指数。クラスタ ID は列ごとに振り直されるので
ID の対応は取らない)。

`{agreement:.4f}`

{body}"""


def render_silence_log(run_id: str, silence: Mapping[str, Any], clusters: Sequence[str]) -> str:
    rows = "\n".join(
        f"| `{f['file']}` | `{_cluster_of_track(f['file'])}` | {f['seconds']} | {f['pauses']} "
        f"| {f['shortest_pause']} | {'真' if f['result'] else '偽'} |"
        for f in silence["files"]
    )
    return f"""# 実測ログ: 生成した mp3 の無音構造判定

R-7(C-007)の「先頭と末尾を除く位置に、前後を有音区間に挟まれた3秒以上の無音区間が機械判定で確認できる」
ことを、フル処理の列(`run_id` `{run_id}`)が書き出した実物の mp3 に対して判定した記録(元の T-047 の受入基準
A-3)。判定は `audio.silence.detect_structure` と `has_pause_structure`(しきい値 {silence["threshold_seconds"]} 秒)
で、mp3 は ffmpeg で {silence["sample_rate"]} Hz にデコードした。`tests/measurements/test_full_run_logs.py` が
この記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| 実行時刻 | {silence["started_at"]} |
| コミット | `{silence["commit"]}` |
| 対象 | `{silence["audio_dir"]}` |
| ファイル数 | {len(silence["files"])} |

## クラスタ一覧

フル処理の列の cluster 段の成果物にあるクラスタ ID。

```text
{chr(10).join(clusters)}
```

## 判定結果

「無音」は、3.0 秒以上で前後いずれにも有音がある無音区間の数。「最短」はそのうち最短の秒数。

| ファイル | クラスタ | 長さ(秒) | 無音 | 最短 | 判定 |
|---|---|---|---|---|---|
{rows}
"""


def _cluster_of_track(name: str) -> str:
    match = _TRACK_NAME.match(name)
    if match is None:
        raise ValueError(f"連番のファイル名でない: {name}")
    return match.group("cluster")


def render_records(out_root: Path, deck: str) -> tuple[str, str, str]:
    """作業ルートの journal・meter.jsonl・標準エラーから full-run.md・reproducibility.md・audio-silence.md を作る。"""
    journal = _read_journal(out_root, deck)
    meter = _meter_lines(out_root, deck)
    # 起動前検査に落ちた実行は列に入らず、開始行も無い(D-132)ので、対にしない。
    executions = [e for e in journal if e["entry"] == "execution" and e["run_id"] is not None]

    # 標準エラーは記録する列(本列と使う再現性の列)の実行だけを読む。別の列の実行は、旧いプロンプトの子の記録を
    # 持ちうる(プロンプトを変えた後のサイクル)ので、メソッドに振り分けられない(T-084)。
    def parsed(entry: Mapping[str, Any]) -> dict[str, Any]:
        return {**entry, **_parse_stderr(out_root, deck, entry["stderr"])}

    full = [e for e in executions if e["kind"] == "full"]
    run_id = full[-1]["run_id"]
    full_paired = _paired([parsed(e) for e in full if e["run_id"] == run_id], meter)
    # 下流が使ったのは最後に成功した cluster の割当(列の途中で cluster を作り直すことがある。AR-13)。
    cluster_entry = [e for e, _, _ in full_paired if _stage_of(e) == "cluster" and e["exit_code"] == 0][-1]
    clusters = _clusters_of(cluster_entry["stdout"])
    since = min(datetime.fromisoformat(e["started_at"]) for e in journal if e["entry"] == "execution")
    attempts = attempts_of(meter, since, {e["run_id"] for e in executions if e["kind"] == "repro"})
    full_log = render_full_log(deck, run_id, full_paired, attempts, clusters, cluster_entry["cluster_summary"])

    repro = [e for e in executions if e["kind"] == "repro" and e["exit_code"] == 0][-REPRO_SEQUENCES:]
    sequences = [(_paired([parsed(e)], meter)[0], _clusters_of(e["stdout"])) for e in repro]
    repro_log = render_repro_log(deck, sequences)

    silence = [e for e in journal if e["entry"] == "silence"][-1]
    silence_log = render_silence_log(run_id, silence, sorted(clusters))
    return full_log, repro_log, silence_log


def precheck(out_root: Path, deck: str) -> str:
    """最後のフル処理の列の cluster と recall の成果物に option_key とラベル判定をかけた結果。ID と件数だけを返す。"""
    from quiz_sift.domain.cluster import Cluster
    from quiz_sift.domain.ids import ClusterId, DeckId, QuestionId
    from quiz_sift.ingest.deck import ingest_deck
    from quiz_sift.recall.generate import material_from
    from quiz_sift.recall.verify import leaked_option_keys
    from quiz_sift.script.generate import question_script
    from quiz_sift.script.label_guard import find_enumerations
    from quiz_sift.script.split import plan_tracks

    deck_id = DeckId(deck)
    loaded = ingest_deck(out_root / "data", deck_id).deck
    questions = {q.question_id: q for q in loaded.questions}
    executions = [e for e in _read_journal(out_root, deck) if e["entry"] == "execution" and e["run_id"] is not None]
    run_id = [e for e in executions if e["kind"] == "full"][-1]["run_id"]

    def artifact(stage: str) -> Any:
        done = [e for e in executions if e["run_id"] == run_id and _stage_of(e) == stage and e["exit_code"] == 0]
        if not done:
            raise SystemExit(f"列 {run_id} に exit 0 の {stage} が無い")
        return json.loads(Path(done[-1]["stdout"]).read_bytes())

    clusters = [
        Cluster(ClusterId(deck_id, row["id"]), row["summary"], tuple(QuestionId(deck_id, q) for q in row["questions"]))
        for row in artifact("cluster")["clusters"]
    ]
    materials = {
        (m.cluster_id, m.question_id): m for m in (material_from(row, deck_id) for row in artifact("recall")["materials"])
    }
    leaked = {qid for (_, qid), m in materials.items() if leaked_option_keys(m.draft, questions[qid])}
    per_cluster = [
        (
            c.cluster_id,
            [question_script(materials[(c.cluster_id, q)], questions[q], loaded.language) for q in c.question_ids],
        )
        for c in clusters
    ]
    lines = [f"run_id: {run_id}", f"option_key: {len(leaked)} 問"]
    lines += [f"  {qid.local_id}" for qid in sorted(leaked, key=lambda q: q.local_id)]
    enumerating = []
    for track in plan_tracks(per_cluster, loaded.language).tracks:
        option_count = min(len(questions[q.question_id].options) for q in track.questions)
        stems = [questions[q.question_id].stem for q in track.questions]
        found = find_enumerations(track.segments, loaded.language, option_count, stems)
        if found:
            series = ",".join(sorted({e.series for e in found}))
            with_leak = "あり" if any(q.question_id in leaked for q in track.questions) else "なし"
            enumerating.append(f"  {track.cluster_id.local_id}-{track.part} {series} option_key={with_leak}")
    return "\n".join([*lines, f"label_enumerations: {len(enumerating)} トラック", *enumerating]) + "\n"


def _precheck(args: argparse.Namespace) -> int:
    print(precheck(args.out_root, args.deck), end="", flush=True)
    return 0


def _render(args: argparse.Namespace) -> int:
    for path, text in zip((FULL_LOG, REPRO_LOG, SILENCE_LOG), render_records(args.out_root, args.deck), strict=True):
        path.write_text(text, encoding="utf-8")
    return 0


# ---- 記録の読み取り ----


def _sections(text: str, prefix: str) -> list[tuple[str, str]]:
    """`## {prefix}` で始まる見出しの節を (見出し, 本文) で返す。"""
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


def _table_value(body: str, key: str) -> str:
    match = re.search(rf"^\| {re.escape(key)} \| (.+?) \|$", body, re.MULTILINE)
    assert match is not None, f"表に {key} が無い"
    return match.group(1).strip().strip("`")


def _number(body: str, key: str) -> float:
    value = _table_value(body, key)
    assert re.fullmatch(r"-?\d+(\.\d+)?", value), f"{key} が数値でない: {value!r}"
    return float(value)


def _execution(body: str) -> dict[str, Any]:
    """実行1つの節から argv・実行時刻・コミット・終了コード・開始行・終了行を読む。"""
    datetime.fromisoformat(_table_value(body, "実行時刻"))
    commit = _table_value(body, "コミット")
    assert re.fullmatch(r"[0-9a-f]{7,40}", commit), f"コミット ID でない: {commit!r}"
    argv = _blocks(body, "text")
    assert argv and argv[0].strip(), "argv が無い"
    lines = _blocks(body, "json")
    assert len(lines) >= 2, "開始行と終了行が無い"
    start, end = json.loads(lines[0]), json.loads(lines[1])
    assert start["record"] == "start", "開始行の record が start でない"
    assert end["record"] == "end", "終了行の record が end でない"
    assert start["execution_id"] == end["execution_id"], "開始行と終了行の execution_id が違う"
    assert start["run_id"] == end["run_id"], "開始行と終了行の run_id が違う"
    exit_code = int(_table_value(body, "終了コード"))
    assert end["stop"]["exit_code"] == exit_code, "終了行の終了コードが実行の終了コードと違う"
    return {"argv": argv[0].splitlines(), "exit_code": exit_code, "start": start, "end": end}


def full_executions(text: str) -> list[dict[str, Any]]:
    executions = [_execution(body) for _, body in _sections(text, "実行 ")]
    assert executions, "実行の節が無い"
    return executions


def _assignment(body: str) -> dict[str, list[str]]:
    blocks = [json.loads(block) for block in _blocks(body, "json")]
    assert blocks, "割当が無い"
    assignment: dict[str, list[str]] = blocks[-1]
    return assignment


# ---- 判定 ----


def check_full_sequence(text: str) -> None:
    """A-1: 列の各実行が記録され、最後の終了行で列が completed、推定コストが記録済み。"""
    executions = full_executions(text)
    run_id = _table_value(_section(text, "実行条件"), "run_id")
    assert int(_table_value(_section(text, "実行条件"), "実行数")) == len(executions), "実行数が節の数と合わない"
    for execution in executions:
        assert execution["start"]["run_id"] == run_id, "列の run_id でない実行がある"
        keys = set(execution["end"])
        assert keys == END_KEYS, (
            f"終了行のキー集合が設計の一覧と違う: 余分 {sorted(keys - END_KEYS)} 欠落 {sorted(END_KEYS - keys)}"
        )
    assert executions[0]["start"]["cache_mode"] == "fresh", "列が --fresh-run で始まっていない"
    sequence = executions[-1]["end"]["sequence"]
    assert len(sequence["quota"]) == len(executions), "記録した実行の数が列の実行の数と違う"
    assert sequence["status"] == "completed", (
        f"列が completed でない: {sequence['status']} {sequence['not_completable_reasons']}"
    )
    cost = sequence["estimated_cost"]
    assert cost["status"] == "recorded", f"推定コストが記録済みでない: {cost}"
    diff = cost["relative_diff"]
    assert isinstance(diff, (int, float)) and not isinstance(diff, bool), f"relative_diff が数値でない: {diff!r}"
    n = compression_metrics(_assignment(_section(text, "クラスタの割当")))["N"]
    assert MIN_QUESTIONS <= n <= MAX_QUESTIONS, f"問題数が {MIN_QUESTIONS}〜{MAX_QUESTIONS} に入らない: {n}"


def check_compression(text: str) -> None:
    """A-4: M/N・最大クラスタ比率・「その他」比率が数値で、記録した割当から計算し直した値と一致する。"""
    body = _section(text, "圧縮の指標")
    expected = compression_metrics(_assignment(_section(text, "クラスタの割当")))
    for key in ("M/N", "最大クラスタ比率", "「その他」比率"):
        assert abs(_number(body, key) - expected[key]) < 5e-5, f"{key} が割当から計算した値と違う"


def check_no_metered_credentials(text: str) -> None:
    """A-5: どの実行も metered 認証変数を持たずに、列が overage 0・失敗 0 で completed。"""
    executions = full_executions(text)
    for execution in executions:
        start = execution["start"]
        assert start["metered_env_names"] == [], f"metered_env_names が空でない: {start['metered_env_names']}"
        names = start["env_names"]
        assert isinstance(names, list) and names, "環境変数名の一覧が記録されていない"
        assert not METERED_ENV_NAMES & set(names), f"metered 認証変数がある: {sorted(METERED_ENV_NAMES & set(names))}"
    last = executions[-1]["end"]
    assert last["sequence"]["status"] == "completed", "列が completed でない"
    assert last["sequence"]["overage_invocations"] == 0, "overage の呼び出しがある"
    failures = last["failures_by_kind"]
    assert failures and all(count == 0 for count in failures.values()), f"最後の実行に失敗がある: {failures}"


def check_overage_and_quota(text: str) -> None:
    """A-6: overage_check が passed か unknown で、レート枠の値か欠落件数が記録されている。"""
    executions = full_executions(text)
    sequence = executions[-1]["end"]["sequence"]
    summary = _section(text, "列の集約")
    assert sequence["overage_check"] in ("passed", "unknown"), f"overage_check: {sequence['overage_check']}"
    assert _table_value(summary, "overage_check") == sequence["overage_check"], "本文の overage_check が終了行と違う"
    if sequence["overage_check"] == "unknown":
        recorded = _number(summary, "invocations_without_rate_limit_event")
        assert recorded == sequence["invocations_without_rate_limit_event"] > 0, "届かなかった件数が本文に無い"
    for edge in ("start", "end"):
        values = [execution["end"][f"quota_at_{edge}"] for execution in executions]
        assert all(value is None or isinstance(value, dict) for value in values), f"quota_at_{edge} の形が違う"
        missing = sum(value is None for value in values)
        assert sequence["quota_missing"][f"at_{edge}"] == missing, f"quota_at_{edge} の欠落件数が合わない"
        assert _number(summary, f"quota_missing.at_{edge}") == missing, f"本文の quota_missing.at_{edge} が合わない"


def check_reproducibility(text: str) -> None:
    """A-2: 1実行だけの --fresh-run cluster の列2本で、分類の呼び出しが各1回・⌈N/25⌉本、一致率が記録済み。"""
    sequences = _sections(text, "列 ")
    assert len(sequences) == REPRO_SEQUENCES, f"列が {REPRO_SEQUENCES} 本でない: {len(sequences)}"
    assignments = []
    classify_calls = classify_invocations = 0
    run_ids = set()
    for _, body in sequences:
        execution = _execution(body)
        start, end = execution["start"], execution["end"]
        argv = execution["argv"]
        assert "cluster" in argv and "--fresh-run" in argv and "--resume" not in argv, (
            "cluster --fresh-run の実行でない"
        )
        assert start["cache_mode"] == "fresh" and start["stage"] == "cluster", "開始行が fresh の cluster でない"
        assert execution["exit_code"] == 0, "exit 0 で終わっていない"
        assert len(end["sequence"]["quota"]) == 1, "1実行だけの列でない(再開した列は使わない。D-142)"
        run_ids.add(start["run_id"])
        assignment = _assignment(body)
        n = compression_metrics(assignment)["N"]
        rows = {
            m: (int(c), int(i), int(r))
            for m, c, i, r in re.findall(r"^\| (\w+) \| (\d+) \| (\d+) \| (\d+) \|$", body, re.MULTILINE)
        }
        assert set(rows) == set(CLUSTER_METHODS), f"メソッド別の内訳が揃っていない: {sorted(rows)}"
        assert sum(c for c, _, _ in rows.values()) == end["llm_calls"], "内訳の llm_calls の和が終了行と違う"
        assert sum(i for _, i, _ in rows.values()) == end["provider_invocations"], (
            "内訳の子プロセス数の和が終了行と違う"
        )
        for method in CLUSTER_METHODS[1:]:
            c, i, r = rows[method]
            assert c == i - r, f"{method} の llm_calls が子プロセス数から出し直しを除いた数と違う"
        calls, invocations, _ = rows["classify_clusters"]
        assert calls == 1, f"classify_clusters の llm_calls が 1 でない: {calls}"
        assert invocations == math.ceil(n / CLASSIFY_BATCH_SIZE), f"分類の子プロセス数が ⌈{n}/25⌉ でない: {invocations}"
        classify_calls += calls
        classify_invocations += invocations
        assignments.append(assignment)
    assert len(run_ids) == REPRO_SEQUENCES, "2本の列の run_id が同じ"
    n = compression_metrics(assignments[0])["N"]
    assert classify_calls == 2 and classify_invocations == 2 * math.ceil(n / CLASSIFY_BATCH_SIZE), (
        "2本の和が1回分の2倍でない"
    )
    recorded = _section(text, "割当一致率")
    match = re.search(r"^`(\d\.\d{4})`$", recorded, re.MULTILINE)
    assert match is not None, "割当一致率が数値として記録されていない"
    assert abs(float(match.group(1)) - rand_agreement(*assignments)) < 5e-5, "割当一致率が2つの割当から計算した値と違う"


def check_audio_silence(text: str) -> None:
    """A-3: 全クラスタについて、3.0 秒以上の無音が前後を有音に挟まれて検出された。"""
    clusters = set(_blocks(_section(text, "クラスタ一覧"), "text")[0].split())
    assert clusters, "クラスタ一覧が空"
    rows = re.findall(
        r"^\| `([^`]+)` \| `([^`]+)` \| [\d.]+ \| (\d+) \| ([\d.]+|None) \| (真|偽) \|$", text, re.MULTILINE
    )
    assert rows, "判定結果が無い"
    assert int(_table_value(_section(text, "実行条件"), "ファイル数")) == len(rows), "ファイル数が行の数と合わない"
    for name, cluster, pauses, _, result in rows:
        assert _cluster_of_track(name) == cluster, f"{name} のクラスタ欄が違う"
        assert result == "真" and int(pauses) >= 1, f"{name} で無音構造が検出されない"
    missing = clusters - {cluster for _, cluster, _, _, _ in rows}
    assert not missing, f"判定したファイルの無いクラスタがある: {sorted(missing)}"


def check_attempt_budget(text: str) -> None:
    """D-181: フル処理の列は `--fresh-run` で始めた本数で数え、`MAX_ATTEMPTS` 本以内。最後の列が記録した列。"""
    rows = [
        (run_id, int(fresh), int(resume))
        for run_id, fresh, resume in re.findall(
            r"^\| `([0-9a-f]+)` \| (\d+) \| (\d+) \|$", _section(text, ATTEMPTS), re.MULTILINE
        )
    ]
    assert rows, "試行の表が無い"
    assert len({run_id for run_id, _, _ in rows}) == len(rows), "同じ列が試行の表に2度ある"
    for run_id, fresh, _ in rows:
        assert fresh == 1, f"列 {run_id} が --fresh-run の1実行で始まっていない: {fresh}"
    assert len(rows) <= MAX_ATTEMPTS, f"--fresh-run の列が {len(rows)} 本で、上限 {MAX_ATTEMPTS} 本を超えた"
    run_id, fresh, resume = rows[-1]
    conditions = _section(text, "実行条件")
    assert run_id == _table_value(conditions, "run_id"), "試行の最後の列が記録した列でない"
    assert fresh + resume == int(_table_value(conditions, "実行数")), "記録した列の実行数が試行の表と合わない"


# ---- T-071 の記録に対する判定(記録がコミットされるまでは skip) ----


def _person_record(path: Path) -> str:
    """T-071 の記録を読む。3つとも無ければまだコミットされていないので skip、一部だけ無ければ赤。"""
    if not any(record.is_file() for record in (FULL_LOG, REPRO_LOG, SILENCE_LOG)):
        pytest.skip("T-071 の記録(full-run.md・reproducibility.md・audio-silence.md)がまだコミットされていない")
    assert path.is_file(), f"記録の一部が無い: {path.name}"
    return path.read_text(encoding="utf-8")


def test_c025_full_run_record_passes_every_judge() -> None:
    """T-071 A-2: 元の T-047 の A-1〜A-6。"""
    full = _person_record(FULL_LOG)
    check_full_sequence(full)
    check_reproducibility(_person_record(REPRO_LOG))
    check_audio_silence(_person_record(SILENCE_LOG))
    check_compression(full)
    check_no_metered_credentials(full)
    check_overage_and_quota(full)


def test_c007_every_cluster_has_inner_silence() -> None:
    """T-071 A-2: 元の T-047 の A-3。"""
    check_audio_silence(_person_record(SILENCE_LOG))


def test_c025_attempt_budget_was_kept() -> None:
    """T-071 A-3。"""
    check_attempt_budget(_person_record(FULL_LOG))


# ---- 判定の道具の検査(合成の作業ルート。実データも out/ も使わない) ----

_SENTINEL = "番兵の教材本文-7f3a9c"
STALE_PROMPT = "旧いプロンプト(今の製品のどのメソッドでもない)"
"""合成の作業ルートで教材本文の代わりに置く文字列。記録に1回でも出れば NFR-2 の破れ。"""
_DECK = "synth-deck"
_N = 100
_ASSIGNMENTS = (
    {
        "c001": [f"q{i:03d}" for i in range(1, 31)],
        "c002": [f"q{i:03d}" for i in range(31, 61)],
        "c003": [f"q{i:03d}" for i in range(61, 91)],
        OTHER_CLUSTER: [f"q{i:03d}" for i in range(91, 101)],
    },
    {
        "c001": [f"q{i:03d}" for i in range(1, 36)],
        "c002": [f"q{i:03d}" for i in range(36, 61)],
        "c003": [f"q{i:03d}" for i in range(61, 95)],
        OTHER_CLUSTER: [f"q{i:03d}" for i in range(95, 101)],
    },
)


class _SyntheticRoot:
    """journal・meter.jsonl・成果物・標準エラーを持つ合成の作業ルート。exec の書く形をそのまま作る。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = _journal_dir(root, _DECK)
        self.dir.mkdir(parents=True)
        self.meter: list[dict[str, Any]] = []
        self.clock = datetime.fromisoformat("2026-10-10T09:00:00+09:00")
        self.number = 0

    def _tick(self) -> str:
        self.clock += timedelta(minutes=1)
        return self.clock.isoformat()

    def _stderr(
        self,
        run_id: str,
        stage: str,
        clusters: Mapping[str, Sequence[str]] | None,
        reissued: Sequence[str],
        stale_prompt: bool,
    ) -> str:
        from quiz_sift.adapters import llm_prompts

        lines = [f"run_id: {run_id}", f"WARNING quiz_sift.cli: 応答 {_SENTINEL}"]
        if stale_prompt:
            # 今の製品のどのメソッドの system プロンプトでもない(旧いプロンプトで走った子の記録)。
            stale: dict[str, object] = {"argv": ["claude", "-p", "--system-prompt", STALE_PROMPT], "failure_kind": None}
            lines.append(f"INFO quiz_sift.safety.invocation_log: claude invocation {json.dumps(stale)}")
        if stage == "cluster" and clusters is not None:
            system = {method: prompt for prompt, method in _method_of_prompt().items()}
            children: list[tuple[str, str | None]] = [(llm_prompts.CLASSIFY_SYSTEM, None)] * math.ceil(
                _N / CLASSIFY_BATCH_SIZE
            )
            # 出し直された子は TRUNCATED で終わり、同じ argv の子がその後に続く(D-231)。
            children += [(system[method], "truncated") for method in reissued]
            children += [(llm_prompts.SUMMARIZE_SYSTEM, None), (llm_prompts.ENTAILMENT_SYSTEM, None)] * len(clusters)
            for prompt, failure_kind in children:
                record = {
                    "argv": ["claude", "-p", "--system-prompt", prompt],
                    "failure_kind": failure_kind,
                    "stdin_sha256": "0" * 64,
                }
                lines.append(
                    f"INFO quiz_sift.safety.invocation_log: claude invocation {json.dumps(record, ensure_ascii=False)}"
                )
            metrics = compression_metrics(clusters)
            lines.append(
                f"INFO quiz_sift.cluster.metrics: clustering summary: n={_N} m={metrics['M']} "
                f"m_over_n={metrics['M/N']:.4f} other_ratio={metrics['「その他」比率']:.4f} "
                f"max_cluster_ratio={metrics['最大クラスタ比率']:.4f} failure_count=0"
            )
        return "\n".join(lines) + "\n"

    def execute(
        self,
        kind: str,
        stage: str,
        run_id: str,
        *,
        fresh: bool,
        exit_code: int = 0,
        clusters: Mapping[str, Sequence[str]] | None = None,
        quota: bool = True,
        rate_limited: bool = False,
        events_missing: int = 0,
        reissued: Sequence[str] = (),
        stale_prompt: bool = False,
    ) -> None:
        """製品の CLI の1実行ぶんの journal の行と、meter.jsonl の開始行・終了行を書く。

        `reissued` は cluster 段で TRUNCATED で終わり出し直された子のメソッド名(1つが子1本)。`stale_prompt` なら
        標準エラーに、今の製品のどのメソッドのプロンプトでもない子の記録を1件置く。
        """
        from quiz_sift.pipeline.run_sequence import aggregate

        self.number += 1
        started_at = self._tick()
        stdout = ""
        if clusters is not None:
            artifact = self.root / "out" / _DECK / f"cluster-{self.number:03d}.json"
            rows = [{"id": c, "summary": _SENTINEL, "questions": list(q)} for c, q in clusters.items()]
            artifact.write_text(json.dumps({"clusters": rows}, ensure_ascii=False), encoding="utf-8")
            stdout = str(artifact)
        stderr = f"{self.number:03d}.stderr"
        (self.dir / stderr).write_text(self._stderr(run_id, stage, clusters, reissued, stale_prompt), encoding="utf-8")
        cache = ["--fresh-run"] if fresh else ["--resume", run_id]
        argv = ["python", "-m", "quiz_sift.cli", "--root", str(self.root), stage, _DECK, *cache]
        entry = {
            "entry": "execution",
            "kind": kind,
            "argv": argv,
            "started_at": started_at,
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "exit_code": exit_code,
            "run_id": run_id,
            "stdout": stdout,
            "stderr": stderr,
        }
        _append_journal(self.root, _DECK, entry)
        start = self._start(run_id, stage, fresh=fresh)
        execution_id = start["execution_id"]
        invocations = (
            math.ceil(_N / CLASSIFY_BATCH_SIZE) + len(reissued) + 2 * len(clusters)
            if stage == "cluster" and clusters
            else 3
        )
        calls = 1 + 2 * len(clusters) if stage == "cluster" and clusters else 3
        window = {"status": "allowed", "utilization": 0.25, "resets_at": "2026-10-17T08:00:00+09:00"}
        end = {
            "record": "end",
            "run_id": run_id,
            "execution_id": execution_id,
            "started_at": start["started_at"],
            "finished_at": self._tick(),
            "elapsed_sec": 60.0,
            "llm_calls": calls,
            "provider_invocations": invocations,
            "model_turns": invocations,
            "turns_by_model": {"claude-opus-5-5": invocations},
            "max_concurrency": 4,
            "tokens": {"input": 10, "cache_creation_input": 0, "cache_read_input": 0, "output": 5},
            "tokens_by_model": {"claude-opus-5-5": {"input": 10, "output": 5}},
            "calls_by_model": {"claude-opus-5-5": calls},
            "estimated_cost": {"status": "recorded", "declared": 1.0, "computed": 1.0},
            "disclaimer": "notional",
            "prices": {},
            "overhead": {},
            "failures_by_kind": {"timeout": 0, "schema": 0, "rate_limit": 0},
            "failed_invocations": 0,
            "invocations_without_usage": 0,
            "invocations_without_turns": 0,
            "rate_limit_stopped_invocations": 0,
            "ended_by_rate_limit": {"value": rate_limited},
            "quota_at_start": window if quota else None,
            "quota_at_end": window,
            "overage_invocations": 0,
            "invocations_without_rate_limit_event": events_missing,
            "overage_check": "unknown" if events_missing else "passed",
            "stop": {"exit_code": exit_code, "kind": "completed" if exit_code == 0 else "error", "exit_reason": "ok"},
        }
        end["sequence"] = aggregate([*self.meter, end], run_id)
        self._write_meter(end)

    def _start(self, run_id: str, stage: str, *, fresh: bool) -> dict[str, Any]:
        start = {
            "record": "start",
            "run_id": run_id,
            "execution_id": f"{run_id[:8]}-{len(self.meter):03d}",
            "started_at": self._tick(),
            "cache_mode": "fresh" if fresh else "resume",
            "stage": stage,
            "env_names": ["HOME", "LANG", "PATH"],
            "metered_env_names": [],
        }
        self._write_meter(start)
        return start

    def _write_meter(self, line: dict[str, Any]) -> None:
        self.meter.append(line)
        (self.root / "out" / _DECK / METER_LOG).write_text(
            "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in self.meter), encoding="utf-8"
        )

    def unjournaled_fresh_run(self, run_id: str) -> None:
        """exec を通さずに起動した `cluster --fresh-run`。meter.jsonl に開始行だけがあり、journal には無い。"""
        self._start(run_id, "cluster", fresh=True)

    def silence(self, clusters: Sequence[str]) -> None:
        files = [
            {"file": f"{i:03d}_{_DECK}_{c}.mp3", "seconds": 42.5, "pauses": 2, "shortest_pause": 3.1, "result": True}
            for i, c in enumerate(clusters, start=1)
        ]
        entry = {
            "entry": "silence",
            "started_at": self._tick(),
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "audio_dir": f"out/{_DECK}/audio",
            "sample_rate": SILENCE_SAMPLE_RATE,
            "threshold_seconds": 3.0,
            "files": files,
        }
        _append_journal(self.root, _DECK, entry)


def _synthetic_records(
    root: Path,
    *,
    earlier_attempts: int = 0,
    events_missing: int = 0,
    stopped: int = 0,
    unjournaled: int = 0,
    previous_cycle: int = 0,
    recluster: Mapping[str, Sequence[str]] | None = None,
    repro_reissued: Sequence[str] = (),
    stale_earlier: bool = False,
    stale_main: bool = False,
) -> tuple[str, str, str]:
    """合成の作業ルートを作って render する。

    本列の前に、止まった --fresh-run の列を `earlier_attempts` 本置く。本列は cluster --fresh-run の後、
    `recluster` があればその割当で cluster を --resume で作り直し、週次枠で止まった recall を `stopped` 回 --resume
    し、recall から audio までを --resume で通して completed にする。無音判定は下流が使った割当のクラスタに掛ける。
    再現性の列の cluster 段では、`repro_reissued` のメソッドの子が TRUNCATED で出し直される。
    `previous_cycle` 本は journal の最初の実行より前の、`unjournaled` 本は journal の実行の間の、exec を通さない
    --fresh-run の開始行である。`stale_earlier` なら止まった列の、`stale_main` なら本列の cluster の標準エラーに、
    旧いプロンプトの子の記録を置く。
    """
    synthetic = _SyntheticRoot(root)
    for i in range(previous_cycle):
        synthetic.unjournaled_fresh_run(f"{i + 1:x}" * 31 + "e")
    for i in range(earlier_attempts):
        prior = f"{i + 1:x}" * 32
        synthetic.execute("full", "cluster", prior, fresh=True, exit_code=1, stale_prompt=stale_earlier)
        synthetic.execute("full", "cluster", prior, fresh=False, exit_code=1, stale_prompt=stale_earlier)
    for i, clusters in enumerate(_ASSIGNMENTS):
        synthetic.execute("repro", "cluster", f"{'cd'[i]}" * 32, fresh=True, clusters=clusters, reissued=repro_reissued)
    for i in range(unjournaled):
        synthetic.unjournaled_fresh_run(f"{i + 1:x}" * 31 + "f")
    main = "a" * 32
    synthetic.execute(
        "full", "cluster", main, fresh=True, clusters=_ASSIGNMENTS[0], quota=False, stale_prompt=stale_main
    )
    if recluster is not None:
        synthetic.execute("full", "cluster", main, fresh=False, clusters=recluster)
    for _ in range(stopped):
        synthetic.execute("full", "recall", main, fresh=False, exit_code=75, rate_limited=True)
    for stage in ("recall", "glossary", "script", "audio"):
        synthetic.execute("full", stage, main, fresh=False, events_missing=events_missing)
    synthetic.silence(sorted(_ASSIGNMENTS[0] if recluster is None else recluster))
    return render_records(root, _DECK)


def _every_judge(full: str, repro: str, silence: str) -> None:
    check_full_sequence(full)
    check_reproducibility(repro)
    check_audio_silence(silence)
    check_compression(full)
    check_no_metered_credentials(full)
    check_overage_and_quota(full)
    check_attempt_budget(full)


def _red(check: Any, text: str) -> None:
    try:
        check(text)
    except AssertionError:
        return
    raise AssertionError("壊した記録で判定が赤にならない")


def test_full_run_judges_go_red_on_each_missing_item(tmp_path: Path) -> None:
    """T-070 A-1: 合成の記録は全判定を通り、項目を1つずつ欠くと赤。overage_check は passed も unknown も通す。"""
    full, repro, silence = _synthetic_records(tmp_path / "passed")
    _every_judge(full, repro, silence)
    assert '"overage_check": "passed"' in full
    unknown = _synthetic_records(tmp_path / "unknown", events_missing=4)
    _every_judge(*unknown)
    assert "| overage_check | `unknown` |" in unknown[0]

    # 列の completed
    _red(check_full_sequence, full.replace('"status": "completed"', '"status": "resumable"'))
    _red(check_no_metered_credentials, full.replace('"status": "completed"', '"status": "resumable"'))
    # 再現性の2列の割当一致率
    _red(check_reproducibility, re.sub(r"^`\d\.\d{4}`$\n", "", repro, flags=re.MULTILINE))
    _red(check_reproducibility, re.sub(r"^`\d\.\d{4}`$", "`0.0001`", repro, flags=re.MULTILINE))
    _red(check_reproducibility, "\n## ".join(repro.split("\n## ")[:-1]))
    # 全クラスタの無音判定
    _red(check_audio_silence, silence.replace("| 真 |", "| 偽 |", 1))
    _red(check_audio_silence, re.sub(r"^\| `001_[^\n]*\n", "", silence, flags=re.MULTILINE))
    # 一部のクラスタの mp3 しか判定しなかった記録(ファイル数は行の数と合っている)。
    partial = re.sub(r"^\| `001_[^\n]*\n", "", silence, flags=re.MULTILINE).replace(
        "| ファイル数 | 4 |", "| ファイル数 | 3 |"
    )
    _red(check_audio_silence, partial)
    # M/N・最大クラスタ比率・「その他」比率
    for key in ("M/N", "最大クラスタ比率", "「その他」比率"):
        _red(check_compression, re.sub(rf"^\| {re.escape(key)} \|.*\n", "", full, flags=re.MULTILINE))
        _red(check_compression, re.sub(rf"^\| {re.escape(key)} \| [\d.]+ \|$", f"| {key} | 0.9999 |", full, flags=re.M))
    # metered 認証変数なしと overage 0 件
    _red(check_no_metered_credentials, full.replace('"env_names": ["', '"env_names": ["ANTHROPIC_API_KEY", "', 1))
    _red(
        check_no_metered_credentials, full.replace('"metered_env_names": []', '"metered_env_names": ["AWS_PROFILE"]', 1)
    )
    _red(check_no_metered_credentials, full.replace('"overage_invocations": 0', '"overage_invocations": 1'))
    # overage_check の passed/unknown
    # 表と終了行が同じ failed の記録(render はどちらも同じ値で書く)。
    failed = full.replace('"overage_check": "passed"', '"overage_check": "failed"')
    _red(check_overage_and_quota, failed.replace("| overage_check | `passed` |", "| overage_check | `failed` |"))
    _red(check_overage_and_quota, unknown[0].replace("| invocations_without_rate_limit_event |", "| x |"))
    _red(check_overage_and_quota, full.replace("| quota_missing.at_start | 1 |", "| quota_missing.at_start | 0 |"))


def test_attempts_count_fresh_runs_only(tmp_path: Path) -> None:
    """T-070・T-087 A-5: 試行は --fresh-run の列ごと。--resume と再現性の列は数えず、7本目の本列は赤。"""
    # 止まった列2本(どちらも --resume を1回ずつ)+ 本列(--resume 5回、うち枠で止まった1回)+ 再現性の2列。
    full, _, _ = _synthetic_records(tmp_path / "three", earlier_attempts=2, stopped=1)
    check_attempt_budget(full)
    rows = re.findall(r"^\| `([0-9a-f]{32})` \| (\d+) \| (\d+) \|$", _section(full, ATTEMPTS), re.MULTILINE)
    assert [(int(fresh), int(resume)) for _, fresh, resume in rows] == [(1, 1), (1, 1), (1, 5)]
    assert not {"c" * 32, "d" * 32} & {run_id for run_id, _, _ in rows}, "再現性の列を試行に数えた"

    assert MAX_ATTEMPTS == 6
    check_attempt_budget(_synthetic_records(tmp_path / "six", earlier_attempts=5)[0])
    seventh, _, _ = _synthetic_records(tmp_path / "seven", earlier_attempts=6)
    _red(check_attempt_budget, seventh)
    # exec を通さずに起動した --fresh-run も試行に数える。前サイクルの開始行(journal の最初の実行より前)は数えない。
    _red(check_attempt_budget, _synthetic_records(tmp_path / "unjournaled", unjournaled=6)[0])
    _red(check_attempt_budget, _synthetic_records(tmp_path / "mixed", earlier_attempts=2, unjournaled=4)[0])
    check_attempt_budget(_synthetic_records(tmp_path / "previous", earlier_attempts=5, previous_cycle=3)[0])
    _red(check_attempt_budget, full.replace("| 1 | 5 |", "| 1 | 4 |"))
    _red(check_attempt_budget, full.replace(f"| `{'1' * 32}` | 1 |", f"| `{'1' * 32}` | 2 |"))


PRECHECK_DECK = "synthetic-nofm"
PRECHECK_TEXT = "合成の素材本文-5c1e"
"""precheck の合成の素材に入れる本文。出力に1回でも出れば本文を出している。"""


def _precheck_root(root: Path) -> Path:
    """合成デッキと、その最後のフル処理の列の cluster・recall の成果物を持つ作業ルート。

    db-01 の識別ポイントに選択肢のキー(o2)が漏れ、クラスタ c-db の素材は「肢A」〜「肢D」を言及する。recall の
    成果物の report は旧い形(option_keys が無い)で、precheck は素材だけを読む。
    """
    fixture = ROOT / "tests" / "fixtures" / "decks" / PRECHECK_DECK
    shutil.copytree(fixture, root / "data" / PRECHECK_DECK)
    rows = [json.loads(line) for line in (fixture / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    groups = {prefix: [r["id"] for r in rows if r["id"].startswith(prefix)] for prefix in ("db", "net", "ml", "k8s")}
    labels = {"db-01": "肢A", "db-02": "肢B", "db-03": "肢C", "db-04": "肢D"}
    materials = []
    for prefix, ids in groups.items():
        for row in rows:
            if row["id"] not in ids:
                continue
            wrong = [o["id"] for o in row["options"] if o["id"] not in row["answers"]]
            leak = "（o2）" if row["id"] == "db-01" else ""
            materials.append(
                {
                    "cluster": f"c-{prefix}",
                    "question": row["id"],
                    "terms": [row["stem"][:4]],
                    "answers": [{"option": a, "text": f"{PRECHECK_TEXT}の正解"} for a in row["answers"]],
                    "discrimination": {
                        "text": f"{labels.get(row['id'], '')}{PRECHECK_TEXT}の切り分け{leak}",
                        "distractors": wrong[:1],
                    },
                }
            )
    directory = _journal_dir(root, PRECHECK_DECK)
    directory.mkdir(parents=True)
    cluster_path, recall_path = directory / "cluster.json", directory / "recall.json"
    clusters = [{"id": f"c-{p}", "summary": PRECHECK_TEXT, "questions": ids} for p, ids in groups.items()]
    cluster_path.write_text(json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    old_report = {"verbatim": 0, "regenerations": {}}
    recall_path.write_text(json.dumps({"materials": materials, "report": old_report}, ensure_ascii=False), "utf-8")
    run_id = "a" * 32
    for number, (stage, path) in enumerate((("cluster", cluster_path), ("recall", recall_path)), start=1):
        argv = [sys.executable, "-m", "quiz_sift.cli", "--root", str(root), stage, PRECHECK_DECK]
        _append_journal(
            root,
            PRECHECK_DECK,
            {
                "entry": "execution",
                "kind": "full",
                "argv": [*argv, "--fresh-run"] if stage == "cluster" else [*argv, "--resume", run_id],
                "started_at": f"2026-10-19T10:0{number}:00+09:00",
                "commit": "0" * 40,
                "exit_code": 0,
                "run_id": run_id,
                "stdout": str(path),
                "stderr": f"{number:03d}.stderr",
            },
        )
    return root


def test_precheck_reports_ids_and_counts_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """T-087 A-6: precheck は LLM を呼ばずに option_key で落ちる問題の ID と、ラベル列挙が出るトラックを出す。"""
    root = _precheck_root(tmp_path / "work")
    assert main(["precheck", "--out-root", str(root), "--deck", PRECHECK_DECK]) == 0
    output = capsys.readouterr().out
    assert output == (
        f"run_id: {'a' * 32}\n"
        "option_key: 1 問\n"
        "  db-01\n"
        "label_enumerations: 1 トラック\n"
        "  c-db-0 letter option_key=あり\n"
    )
    # 本文(素材・要約・問題文・選択肢)の断片は出ない。
    rows = [json.loads(line) for line in (root / "data" / PRECHECK_DECK / "questions.jsonl").read_text().splitlines()]
    for text in (PRECHECK_TEXT, "肢A", "o2", *(r["stem"][:8] for r in rows), *(o["text"][:8] for o in rows[0]["options"])):
        assert text not in output, text


def test_render_uses_the_last_successful_cluster(tmp_path: Path) -> None:
    """T-078 A-1: cluster が2回成功した列では、2回目(M−1 クラスタ)の割当とサマリが記録に入り、1回目は入らない。"""
    first = _ASSIGNMENTS[0]
    second = {"c001": first["c001"], "c002": [*first["c002"], *first["c003"]], OTHER_CLUSTER: first[OTHER_CLUSTER]}
    full, repro, silence = _synthetic_records(tmp_path, recluster=second)
    _every_judge(full, repro, silence)
    assert _assignment(_section(full, "クラスタの割当")) == second
    assert f" m={len(second)} " in _section(full, "圧縮の指標")
    assert set(_blocks(_section(silence, "クラスタ一覧"), "text")[0].split()) == set(second)
    # 1回目の割当を使った記録は、無音判定したファイルの無いクラスタがあり赤になる。
    stale = silence.replace("\n".join(sorted(second)), "\n".join(sorted(first)))
    assert stale != silence
    _red(check_audio_silence, stale)


def test_render_reads_only_the_recorded_runs_stderr(tmp_path: Path) -> None:
    """T-084 A-5: 旧いプロンプトの子の記録を持つ別の列の実行が journal にあっても、記録する列だけを読んで作る。"""
    full, repro, silence = _synthetic_records(tmp_path / "stale-earlier", earlier_attempts=2, stale_earlier=True)
    _every_judge(full, repro, silence)
    # 対照: 記録する列の標準エラーに旧いプロンプトの子があれば、振り分けられずに落ちる(読んでいる証拠)。
    with pytest.raises(ValueError, match="既知のどのメソッド"):
        _synthetic_records(tmp_path / "stale-main", stale_main=True)


def test_repro_classify_calls_survive_reissues(tmp_path: Path) -> None:
    """T-078 A-2: summarize_cluster・check_entailments の TRUNCATED の出し直しがあっても classify の llm_calls は 1。"""
    _, repro, _ = _synthetic_records(tmp_path / "other", repro_reissued=("summarize_cluster", "check_entailments"))
    check_reproducibility(repro)
    for _, body in _sections(repro, "列 "):
        assert "| classify_clusters | 1 | 4 | 0 |" in body
        assert "| summarize_cluster | 4 | 5 | 1 |" in body
        assert "| check_entailments | 4 | 5 | 1 |" in body
    # 出し直しを数えない内訳は、他メソッドの比較で赤になる。
    _red(check_reproducibility, repro.replace("| summarize_cluster | 4 | 5 | 1 |", "| summarize_cluster | 4 | 5 | 0 |"))
    # classify の分割呼び出しで出し直しが起きた列は、子プロセス数が ⌈N/25⌉ でなく赤のまま(取り直す)。
    _, classify, _ = _synthetic_records(tmp_path / "classify", repro_reissued=("classify_clusters",))
    _red(check_reproducibility, classify)


def test_render_output_holds_no_material_text(tmp_path: Path) -> None:
    """T-070 A-3: render の出力に教材本文は無く、判定は out/ を消した後も md だけで行える(D-220)。"""
    root = tmp_path / "work"
    records = _synthetic_records(root)
    assert any(_SENTINEL in p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()), (
        "合成の作業ルートに番兵が無い"
    )
    for text in records:
        assert _SENTINEL not in text, "render の出力に教材本文がある"
    # 標準エラーのクラスタ段の実行サマリは、数値の項目だけを写す。
    (_journal_dir(root, _DECK) / "003.stderr").write_text(
        f"run_id: {'c' * 32}\nINFO quiz_sift.cluster.metrics: clustering summary: n=100 note={_SENTINEL}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="数値の項目だけでない"):
        render_records(root, _DECK)
    shutil.rmtree(root / "out")
    _every_judge(*records)


def test_person_record_nodes_skip_until_the_record_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-070 A-4: 前サイクルの途中までの記録は外れていて、T-071 の記録の判定は記録が無い間 skip、あれば判定する。"""
    assert PARTIAL_ORIGINAL.is_file(), "書庫に途中までの記録の原本が無い"
    assert not FULL_LOG.is_file() or FULL_LOG.read_bytes() != PARTIAL_ORIGINAL.read_bytes(), (
        "前サイクルの途中までの記録が full-run.md に残っている"
    )
    module = sys.modules[__name__]
    docs = tmp_path / "measurements"
    docs.mkdir()
    paths = {
        "FULL_LOG": docs / "full-run.md",
        "REPRO_LOG": docs / "reproducibility.md",
        "SILENCE_LOG": docs / "audio-silence.md",
    }
    for name, path in paths.items():
        monkeypatch.setattr(module, name, path)
    nodes = (
        test_c025_full_run_record_passes_every_judge,
        test_c007_every_cluster_has_inner_silence,
        test_c025_attempt_budget_was_kept,
    )

    for node in nodes:
        with pytest.raises(pytest.skip.Exception):
            node()

    records = _synthetic_records(tmp_path / "work")
    for path, text in zip(paths.values(), records, strict=True):
        path.write_text(text, encoding="utf-8")
    for node in nodes:
        node()

    # 記録があれば skip せずに判定する: 壊れた記録は赤、一部だけの記録も赤。
    paths["SILENCE_LOG"].write_text(records[2].replace("| 真 |", "| 偽 |"), encoding="utf-8")
    paths["FULL_LOG"].write_text(records[0].replace('"status": "completed"', '"status": "resumable"'), encoding="utf-8")
    seventh, _, _ = _synthetic_records(tmp_path / "seven", earlier_attempts=6)
    for node in nodes[:2]:
        with pytest.raises(AssertionError):
            node()
    paths["FULL_LOG"].write_text(seventh, encoding="utf-8")
    with pytest.raises(AssertionError):
        nodes[2]()
    paths["REPRO_LOG"].unlink()
    with pytest.raises(AssertionError, match="記録の一部が無い"):
        test_c025_full_run_record_passes_every_judge()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="test_full_run_logs.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("exec", "silence", "render", "precheck"):
        command = commands.add_parser(name)
        command.add_argument("--out-root", type=Path, required=True, help="data/ と out/ を持つ作業ルート")
        command.add_argument("--deck", required=True)
        if name == "exec":
            command.add_argument("--kind", choices=("full", "repro"), required=True)
            command.add_argument("stage_args", nargs=argparse.REMAINDER, help="<段> と段の引数")
    args = parser.parse_args(argv)
    # ホストでは worktree の src/ を import する(pytest は pyproject の pythonpath で同じ場所を見る)。
    sys.path.insert(0, str(ROOT / "src"))
    return {"exec": _exec, "silence": _silence, "render": _render, "precheck": _precheck}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
