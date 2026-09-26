"""フル処理の実行の列と再現性・無音判定の実測ログの検査(T-047 / C-003・C-007・C-019・C-020)。

実教材は NFR-2 によりコミットできず、実 CLI と CosyVoice はサンドボックスに無い(ADR-012、ADR-013)。
そのため証拠は**ホストで取った実測ログ**で、このテストはログの中身を機械判定する。実行もこのファイルから行う:

- `python tests/measurements/test_full_run_logs.py exec --out-root <作業ルート> --deck <デッキ> --kind full
  cluster --fresh-run` は製品の CLI(`python -m quiz_sift.cli`)を子プロセスで1回走らせ、argv・実行時刻・
  コミット ID・終了コード・`run_id` を `<作業ルート>/out/<デッキ>/t047/journal.jsonl` へ追記する。標準エラーは
  同じディレクトリのファイルに置き(応答を含むので NFR-2 によりコミットしない)、そこから `claude` 子プロセス
  1本ずつの記録を数えてメソッド別の呼び出し数を得る。フル処理の列は `--kind full` で `cluster --fresh-run`
  から始め、続く段を `--resume <run_id>` で足す。再現性の列は `--kind repro` で `cluster --fresh-run` を
  1回ずつ走らせる。
- `... silence --out-root <作業ルート> --deck <デッキ>` は、フル処理の列が書き出した mp3 に無音構造判定
  (`audio.silence`)を掛けた結果を journal へ追記する。
- `... render --out-root <作業ルート> --deck <デッキ>` は journal と `meter.jsonl` から
  `docs/test/measurements/` の full-run.md・reproducibility.md・audio-silence.md を書く。`meter.jsonl` の
  開始行と終了行は加工せずに写す。どの行も件数・ID・ダイジェスト・環境変数名・レート枠の値だけで、教材本文を
  含まない(NFR-2)。

判定(各受入基準に1本):

- A-1: フル処理の列の各実行に argv・実行時刻・コミット ID と、その実行の開始行・終了行がある。終了行の
  キー集合は `END_KEYS` と一致する。最後の終了行の `sequence.status` が `completed`、
  `sequence.estimated_cost.status` が `recorded`、`relative_diff` が数値。
- A-2: 再現性の列がちょうど2本で、どちらも `cluster --fresh-run` の1実行だけで exit 0。各列で
  `classify_clusters` の `llm_calls` が 1、`provider_invocations` が ⌈N/25⌉ で、2本の和がその2倍。
  メソッド別の内訳の和が終了行の値と一致する。割当一致率が、記録した2つの割当から計算し直した値と一致する。
- A-3: 無音判定の結果が全クラスタについて真で、クラスタ一覧のどのクラスタにも判定したファイルがある。
- A-4: full-run.md に M/N・最大クラスタ比率・「その他」比率が数値としてあり、記録した割当から計算し直した
  値と一致する。
- A-5: 各実行の開始行の `metered_env_names` が空で、`env_names` と `METERED_ENV_NAMES` の積集合が空。
  列が `completed`、`overage_invocations` が 0、最後の終了行の `failures_by_kind` がすべて 0。
- A-6: 列の `overage_check` が `passed` か `unknown`。`unknown` なら
  `invocations_without_rate_limit_event` の件数が本文にある。各実行の `quota_at_start` / `quota_at_end` が
  あり、`null` の件数が列の `quota_missing` と一致する。
"""

import argparse
import itertools
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
MEASUREMENTS = ROOT / "docs" / "test" / "measurements"
FULL_LOG = MEASUREMENTS / "full-run.md"
REPRO_LOG = MEASUREMENTS / "reproducibility.md"
SILENCE_LOG = MEASUREMENTS / "audio-silence.md"

METER_LOG = "meter.jsonl"
JOURNAL_DIR = "t047"
JOURNAL = "journal.jsonl"

MIN_QUESTIONS, MAX_QUESTIONS = 100, 200
CLASSIFY_BATCH_SIZE = 25
"""分類の分割呼び出し1回あたりの問題数(設計 R-3 節、AR-62)。⌈N/25⌉ の 25。"""
REPRO_SEQUENCES = 2

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

CLUSTER_METHODS = ("classify_clusters", "summarize_cluster", "check_entailment")
"""cluster 段が呼ぶ `Llm` のメソッド。"""
OTHER_CLUSTER = "other"
"""「その他」クラスタの ID(`cluster.summary.OTHER_LOCAL_ID`)。"""
SILENCE_SAMPLE_RATE = 22050
"""無音判定のためにデコードするサンプルレート。CosyVoice-300M の出力と同じ。"""

_INVOCATION_LINE = re.compile(r"^INFO quiz_sift\.safety\.invocation_log: claude invocation (\{.*\})$", re.MULTILINE)
_SUMMARY_LINE = re.compile(r"^INFO quiz_sift\.cluster\.metrics: (clustering summary: .*)$", re.MULTILINE)
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


def _method_of_prompt() -> dict[str, str]:
    """system プロンプトの本文 → `Llm` のメソッド名。"""
    from quiz_sift.adapters import llm_prompts

    return {
        llm_prompts.CLASSIFY_SYSTEM: "classify_clusters",
        llm_prompts.SUMMARIZE_SYSTEM: "summarize_cluster",
        llm_prompts.RECALL_SYSTEM: "generate_recall",
        llm_prompts.DESCRIBE_SYSTEM: "describe_term",
        llm_prompts.ENTAILMENT_SYSTEM: "check_entailment",
    }


def invocations_by_method(stderr: str) -> dict[str, int]:
    """標準エラーの `claude` 子プロセスの記録を、`--system-prompt` の値でメソッドに振り分けて数える。"""
    methods = _method_of_prompt()
    counts: Counter[str] = Counter()
    for raw in _INVOCATION_LINE.findall(stderr):
        argv = json.loads(raw)["argv"]
        prompt = argv[argv.index("--system-prompt") + 1]
        if prompt not in methods:
            raise ValueError("既知のどのメソッドの system プロンプトでもない子プロセスの記録がある")
        counts[methods[prompt]] += 1
    return dict(counts)


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


# ---- 記録の書き出し(`render`) ----


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


def _paired(
    entries: Sequence[Mapping[str, Any]], meter: Sequence[tuple[str, dict[str, Any]]]
) -> list[tuple[Mapping[str, Any], str, str]]:
    """journal の実行を、その実行が書いた開始行・終了行と対にする。

    開始行は、同じ `run_id` の開始行のうち、その実行を起動した時刻から次の実行を起動した時刻までに
    書かれたもの。どれにも1つずつ無ければ例外にする(起動前検査に落ちた実行は列に入らないので、ここには渡さない)。
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


def _stage_of(entry: Mapping[str, Any]) -> str:
    argv: list[str] = entry["argv"]
    return argv[argv.index("--root") + 2]


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


def render_full_log(
    deck: str,
    run_id: str,
    executions: Sequence[tuple[Mapping[str, Any], str, str]],
    clusters: Mapping[str, Sequence[str]],
    summary: str,
) -> str:
    final = json.loads(executions[-1][2])["sequence"]
    metrics = compression_metrics(clusters)
    metric_rows = "\n".join(f"| {key} | {value:.4f} |" for key, value in metrics.items() if key not in ("N", "M"))
    blocks = "\n".join(
        _execution_block(f"実行 {i}({_stage_of(entry)})", entry, start, end)
        for i, (entry, start, end) in enumerate(executions, start=1)
    )
    return f"""# 実測ログ: フル処理の実行の列

NFR-7 (f)・NFR-8 (f)(h)(C-019、C-020)の「100〜200問のフル処理1回」を、1つの `run_id` の実行の列として
ホストで走らせた記録(T-047 の受入基準 A-1・A-4・A-5・A-6)。列は `cluster --fresh-run` で始め、続く段を
`--resume <run_id>` で足した。実 CLI の呼び出しとレート枠の消費は戻せないので、`rein approve T-047` の承認の
もとで実行した。

各実行は `tests/measurements/test_full_run_logs.py exec` が製品の CLI を子プロセスとして走らせ、その argv・
実行時刻・コミット ID・終了コードを記録した。開始行と終了行は `out/{deck}/meter.jsonl` の行を加工せずに写して
いる。行にあるのは件数・ID・ダイジェスト・環境変数名・レート枠の値だけで、教材本文は無い(NFR-2)。標準エラーには
応答が含まれるので写さない。`tests/measurements/test_full_run_logs.py` がこの記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| デッキ | `{deck}` |
| run_id | `{run_id}` |
| 実行数 | {len(executions)} |

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
        counts = entry["invocations_by_method"]
        end_line = json.loads(end)
        others = sum(counts.get(method, 0) for method in CLUSTER_METHODS if method != "classify_clusters")
        calls = {method: counts.get(method, 0) for method in CLUSTER_METHODS}
        calls["classify_clusters"] = end_line["llm_calls"] - others
        rows = "\n".join(f"| {method} | {calls[method]} | {counts.get(method, 0)} |" for method in CLUSTER_METHODS)
        parts.append(
            _execution_block(f"列 {i}", entry, start, end)
            + f"""
メソッド別の内訳:

| メソッド | llm_calls | provider_invocations |
|---|---|---|
{rows}

割当:

{_assignment_block(clusters)}"""
        )
    agreement = rand_agreement(sequences[0][1], sequences[1][1])
    body = "\n".join(parts)
    return f"""# 実測ログ: キャッシュを迂回した再クラスタリング

R-3(C-003)の「キャッシュを迂回して再クラスタリングした2回の実行について割当一致率が記録され、その2回の
`llm_calls` が1回分の2倍になる」ことをホストで測った記録(T-047 の受入基準 A-2)。使ったのは
`cluster --fresh-run` の1実行だけで exit 0 まで走った列2本で、再開した列は使っていない(D-142)。判定は分類の
呼び出しで行う(D-155): 各列で `classify_clusters` の `llm_calls` が 1、`provider_invocations` が ⌈N/25⌉。

開始行と終了行は `out/{deck}/meter.jsonl` の行を加工せずに写している。メソッド別の内訳は、標準エラーにある
`claude` 子プロセス1本ずつの記録を `--system-prompt` の値でメソッドに振り分けて数えた(標準エラーは応答を含む
ので写さない)。`summarize_cluster` と `check_entailment` は1回の呼び出しが子プロセス1本で再試行しない
(D-101)ので `llm_calls` は子プロセス数と同じ。`classify_clusters` の `llm_calls` は終了行の `llm_calls` から
その2つを引いた値である。

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


def render_silence_log(deck: str, run_id: str, silence: Mapping[str, Any], clusters: Sequence[str]) -> str:
    rows = "\n".join(
        f"| `{f['file']}` | `{_cluster_of_track(f['file'])}` | {f['seconds']} | {f['pauses']} "
        f"| {f['shortest_pause']} | {'真' if f['result'] else '偽'} |"
        for f in silence["files"]
    )
    return f"""# 実測ログ: 生成した mp3 の無音構造判定

R-7(C-007)の「先頭と末尾を除く位置に、前後を有音区間に挟まれた3秒以上の無音区間が機械判定で確認できる」
ことを、フル処理の列(`run_id` `{run_id}`)が書き出した実物の mp3 に対して判定した記録(T-047 の受入基準
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


def _parse_stderr(out_root: Path, deck: str, name: str) -> dict[str, Any]:
    """実行の標準エラーから、メソッド別の子プロセス数と cluster 段の実行サマリを読む。"""
    err = (_journal_dir(out_root, deck) / name).read_text(encoding="utf-8", errors="backslashreplace")
    summaries = _SUMMARY_LINE.findall(err)
    summary = summaries[-1] if summaries else None
    return {"invocations_by_method": invocations_by_method(err), "cluster_summary": summary}


def _render(args: argparse.Namespace) -> int:
    out_root: Path = args.out_root
    journal = _read_journal(out_root, args.deck)
    meter = _meter_lines(out_root, args.deck)
    # 起動前検査に落ちた実行は列に入らず、開始行も無い(D-132)ので、対にしない。
    executions = [
        {**e, **_parse_stderr(out_root, args.deck, e["stderr"])}
        for e in journal
        if e["entry"] == "execution" and e["run_id"] is not None
    ]

    full = [e for e in executions if e["kind"] == "full"]
    run_id = full[-1]["run_id"]
    full_paired = _paired([e for e in full if e["run_id"] == run_id], meter)
    cluster_entry = next(e for e, _, _ in full_paired if _stage_of(e) == "cluster" and e["exit_code"] == 0)
    clusters = _clusters_of(cluster_entry["stdout"])
    FULL_LOG.write_text(
        render_full_log(args.deck, run_id, full_paired, clusters, cluster_entry["cluster_summary"]), encoding="utf-8"
    )

    repro = [e for e in executions if e["kind"] == "repro" and e["exit_code"] == 0][-REPRO_SEQUENCES:]
    sequences = [(_paired([e], meter)[0], _clusters_of(e["stdout"])) for e in repro]
    REPRO_LOG.write_text(render_repro_log(args.deck, sequences), encoding="utf-8")

    silence = [e for e in journal if e["entry"] == "silence"][-1]
    SILENCE_LOG.write_text(render_silence_log(args.deck, run_id, silence, sorted(clusters)), encoding="utf-8")
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
    return {
        "argv": argv[0].splitlines(),
        "exit_code": int(_table_value(body, "終了コード")),
        "start": start,
        "end": end,
        "body": body,
    }


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
        assert execution["exit_code"] == 0 and end["stop"]["exit_code"] == 0, "exit 0 で終わっていない"
        assert len(end["sequence"]["quota"]) == 1, "1実行だけの列でない(再開した列は使わない。D-142)"
        run_ids.add(start["run_id"])
        assignment = _assignment(body)
        n = compression_metrics(assignment)["N"]
        rows = dict(
            (m, (int(c), int(i))) for m, c, i in re.findall(r"^\| (\w+) \| (\d+) \| (\d+) \|$", body, re.MULTILINE)
        )
        assert set(rows) == set(CLUSTER_METHODS), f"メソッド別の内訳が揃っていない: {sorted(rows)}"
        assert sum(c for c, _ in rows.values()) == end["llm_calls"], "内訳の llm_calls の和が終了行と違う"
        assert sum(i for _, i in rows.values()) == end["provider_invocations"], "内訳の子プロセス数の和が終了行と違う"
        for method in CLUSTER_METHODS[1:]:
            assert rows[method][0] == rows[method][1], f"{method} の llm_calls が子プロセス数と違う"
        calls, invocations = rows["classify_clusters"]
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


# ---- 実測ログに対する判定 ----


def _read(path: Path) -> str:
    assert path.is_file(), f"実測ログが無い: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_full_run_sequence_is_completed_and_records_every_field() -> None:
    """A-1"""
    check_full_sequence(_read(FULL_LOG))


def test_reproducibility_uses_two_single_execution_fresh_sequences() -> None:
    """A-2"""
    check_reproducibility(_read(REPRO_LOG))


def test_audio_silence_is_true_for_every_cluster() -> None:
    """A-3"""
    check_audio_silence(_read(SILENCE_LOG))


def test_compression_metrics_are_present() -> None:
    """A-4"""
    check_compression(_read(FULL_LOG))


def test_sequence_completed_without_metered_credentials_or_overage() -> None:
    """A-5"""
    check_no_metered_credentials(_read(FULL_LOG))


def test_overage_check_and_quota_are_recorded() -> None:
    """A-6"""
    check_overage_and_quota(_read(FULL_LOG))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="test_full_run_logs.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("exec", "silence", "render"):
        command = commands.add_parser(name)
        command.add_argument("--out-root", type=Path, required=True, help="data/ と out/ を持つ作業ルート")
        command.add_argument("--deck", required=True)
        if name == "exec":
            command.add_argument("--kind", choices=("full", "repro"), required=True)
            command.add_argument("stage_args", nargs=argparse.REMAINDER, help="<段> と段の引数")
    args = parser.parse_args(argv)
    # ホストでは worktree の src/ を import する(pytest は pyproject の pythonpath で同じ場所を見る)。
    sys.path.insert(0, str(ROOT / "src"))
    return {"exec": _exec, "silence": _silence, "render": _render}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
