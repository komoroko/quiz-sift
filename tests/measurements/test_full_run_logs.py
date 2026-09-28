"""フル処理の途中までの実測ログの検査(T-047 / C-019・C-020 の途中までの証拠、D-164)。

実教材は NFR-2 によりコミットできず、実 CLI と CosyVoice はサンドボックスに無い(ADR-012、ADR-013)。
そのため証拠は**ホストで取った実測ログ**で、このテストは記録した md の中身を機械判定する。記録もこのファイルから
作る:

- `python tests/measurements/test_full_run_logs.py exec --out-root <作業ルート> --deck <デッキ> --kind full
  cluster --fresh-run` は製品の CLI(`python -m quiz_sift.cli`)を子プロセスで1回走らせ、argv・実行時刻・
  コミット ID・終了コード・`run_id` を `<作業ルート>/out/<デッキ>/t047/journal.jsonl` へ追記する。標準エラーは
  同じディレクトリのファイルに置く(応答を含むので NFR-2 によりコミットせず、render も読まない)。
- `... silence --out-root <作業ルート> --deck <デッキ>` は、フル処理の列が書き出した mp3 に無音構造判定
  (`audio.silence`)を掛けた結果を journal へ追記する。
- `... render --out-root <作業ルート> --deck <デッキ>` は journal と `meter.jsonl` から
  `docs/test/measurements/full-run.md` を書く。LLM も TTS も呼ばない。`meter.jsonl` の開始行と終了行は加工せずに
  写す。どの行も件数・ID・ダイジェスト・環境変数名・レート枠の値だけで、教材本文を含まない(NFR-2)。

本サイクルではフル処理は完走していない(D-164)。`exec` と `silence` は次サイクルで完走を取り直すときに使うので
残し、このタスクでは実行しない。

判定(各受入基準に1本):

- A-1: journal の実行 `EXECUTIONS` 件のそれぞれに argv・実行時刻・コミット ID・終了コード・run_id と、その
  実行の開始行・終了行がある。終了行のキー集合は `END_KEYS` と一致する。
- A-2: どの列も `completed` でなく、列ごとに status・止まった段・理由がある。列 `CLUSTERED_RUN` の M/N・
  最大クラスタ比率・「その他」比率が数値で、記録した割当から計算し直した値と一致する。
- A-3: 各実行の開始行の `metered_env_names` が空で、`env_names` と `METERED_ENV_NAMES` の積集合が空。
- A-4: 「次サイクルへの引き継ぎ」の節に、元の A-1〜A-6 が未達であること、止めた理由、次サイクルで
  `RECALL_SYSTEM` の修正を先に置くことがある。
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
MEASUREMENTS = ROOT / "docs" / "test" / "measurements"
FULL_LOG = MEASUREMENTS / "full-run.md"

METER_LOG = "meter.jsonl"
JOURNAL_DIR = "t047"
JOURNAL = "journal.jsonl"

EXECUTIONS = 9
"""作業ルートの journal にある実行の数(2026-09-26〜28。D-164)。"""
CLUSTERED_RUN = "591e7bb9ae6145d284a0ec993371c59c"
"""クラスタ段を完走した列(D-164)。"""
STATUSES = ("resumable", "not_completable")
"""完走していない列の status(`pipeline.run_sequence.aggregate`)。"""
HANDOVER = "次サイクルへの引き継ぎ"

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

OTHER_CLUSTER = "other"
"""「その他」クラスタの ID(`cluster.summary.OTHER_LOCAL_ID`)。"""
SILENCE_SAMPLE_RATE = 22050
"""無音判定のためにデコードするサンプルレート。CosyVoice-300M の出力と同じ。"""

_RUN_ID_LINE = re.compile(r"^run_id: ([0-9a-f]+)$", re.MULTILINE)


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
    起動した時刻までに書かれたもの。journal に記録の無い実行(人が退避した 004)の開始行も同じ run_id で
    この区間に入りうるので、段とキャッシュの扱いまで合わせる。どれにも1つずつ無ければ例外にする。
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


def _sequence_row(run_id: str, executions: int, meter: Sequence[tuple[str, dict[str, Any]]]) -> str:
    """列の状態を製品の集約(`run_sequence.aggregate`)で求め、止まった段と理由を最後の開始行から取る。

    journal に記録の無い実行の開始行も列に入る(C-019: 終了記録の無い登録はレート枠以外の停止)ので、列の
    状態は最後の終了行の `sequence` ではなく meter.jsonl の全行から集約し直す。
    """
    from quiz_sift.pipeline.run_sequence import aggregate

    lines = [line for _, line in meter]
    sequence: dict[str, Any] = aggregate(lines, run_id)
    starts = [line for line in lines if line["record"] == "start" and line["run_id"] == run_id]
    last = starts[-1]
    end = next(
        (line for line in lines if line["record"] == "end" and line["execution_id"] == last["execution_id"]), None
    )
    reason = "終了行なし" if end is None else f"`{end['stop']['exit_reason']}`"
    kind = "―" if end is None else f"`{end['stop']['kind']}`"
    failures = "―" if end is None else ", ".join(f"{k}={v}" for k, v in end["failures_by_kind"].items() if v) or "0"
    reasons = ", ".join(f"`{r}`" for r in sequence["not_completable_reasons"]) or "―"
    return (
        f"| `{run_id}` | {executions} | `{sequence['status']}` | `{last['stage']}` | {reason} | {kind} "
        f"| {failures} | {reasons} |"
    )


def _unpaired_rows(meter: Sequence[tuple[str, dict[str, Any]]], paired: set[str]) -> str:
    lines = [line for _, line in meter]
    ended = {line["execution_id"] for line in lines if line["record"] == "end"}
    return "\n".join(
        f"| `{line['execution_id']}` | `{line['run_id']}` | {line['started_at']} | `{line['stage']}` "
        f"| `{line['cache_mode']}` | {'あり' if line['execution_id'] in ended else 'なし'} |"
        for line in lines
        if line["record"] == "start" and line["execution_id"] not in paired
    )


def _execution_block(heading: str, entry: Mapping[str, Any], start: str, end: str) -> str:
    argv = "\n".join(entry["argv"])
    return f"""## {heading}

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


def render_partial_log(
    deck: str,
    executions: Sequence[tuple[Mapping[str, Any], str, str]],
    sequence_rows: Sequence[str],
    unpaired_rows: str,
    clusters: Mapping[str, Sequence[str]],
) -> str:
    metrics = compression_metrics(clusters)
    metric_rows = "\n".join(f"| {key} | {value:.4f} |" for key, value in metrics.items() if key not in ("N", "M"))
    blocks = "\n".join(
        _execution_block(f"実行 {i}({_stage_of(entry)})", entry, start, end)
        for i, (entry, start, end) in enumerate(executions, start=1)
    )
    return f"""# 実測ログ: フル処理の途中までの記録

NFR-7 (f)・NFR-8 (f)(h)(C-019、C-020)の「100〜200問のフル処理1回」を、1つの `run_id` の実行の列として
ホストで走らせた記録。本サイクルではどの列も完走していない。完走は次サイクルへ回した(D-164)ので、この記録は
途中までの証拠である(T-047 の受入基準 A-1〜A-4)。

各実行は `tests/measurements/test_full_run_logs.py exec` が製品の CLI を子プロセスとして走らせ、その argv・
実行時刻・コミット ID・終了コード・run_id を `out/{deck}/t047/journal.jsonl` に記録した。この md は
`tests/measurements/test_full_run_logs.py render` が journal と `out/{deck}/meter.jsonl` から作った。LLM も TTS
も呼んでいない。開始行と終了行は meter.jsonl の行を加工せずに写している。行にあるのは件数・ID・ダイジェスト・
環境変数名・レート枠の値だけで、教材本文は無い(NFR-2)。標準エラーは応答を含むので読まず、写さない。

## 実行条件

| 項目 | 値 |
|---|---|
| デッキ | `{deck}` |
| 実行数 | {len(executions)} |

## 列ごとの状態

status は meter.jsonl の全行を製品の集約(`pipeline.run_sequence.aggregate`)に掛けた、いまの値である。止まった
段と理由は、列の最後の開始行とその終了行の `stop` から取った。理由は例外名だけ、件数は最後の実行の
`failures_by_kind` のうち 0 でないものだけを書く。

| run_id | journal の実行数 | status | 止まった段 | 理由 | 停止の種別 | 失敗の件数 | not_completable_reasons |
|---|---|---|---|---|---|---|---|
{chr(10).join(sequence_rows)}

## journal に記録の無い開始行

meter.jsonl には、journal のどの実行とも対にならない開始行がある。行は写さず、ID と時刻だけを示す。列
`4f6872ef921c40728bcf32ccd46eb038` の `resume` の開始行は、journal に記録される前に止まった実行 004 のもの
である(人が標準出力と標準エラーを `orphan-004.*` へ退避した)。ほかの行は journal の外で起動した実行で、この
記録からは起動の仕方が分からない。終了行の無い開始行は、その列を `not_completable` にする(C-019)。

| execution_id | run_id | 開始時刻 | 段 | キャッシュ | 終了行 |
|---|---|---|---|---|---|
{unpaired_rows}

## 圧縮の指標

クラスタ段を完走した列 `{CLUSTERED_RUN}` の cluster 段の成果物の割当から計算した値。合格ラインは置かない
(D-004)。

| 項目 | 値 |
|---|---|
| N | {metrics["N"]} |
| M | {metrics["M"]} |
{metric_rows}

## クラスタの割当

クラスタ ID → 問題 ID の一覧(要約は教材から作った文なので写さない)。

```json
{json.dumps(dict(sorted(clusters.items())), ensure_ascii=False)}
```

## {HANDOVER}

フル処理の完走は未達である。元の T-047 の受入基準 A-1〜A-6 はどれも満たしていない。

- A-1 未達: 列の `sequence.status` が `completed` に達していない。
- A-2 未達: `cluster --fresh-run` の1実行だけで exit 0 まで走った再現性の列2本を取っていない。
- A-3 未達: 音声段まで進んでおらず、mp3 の無音構造判定をしていない。
- A-4 未達: 圧縮の3値は上の「圧縮の指標」にあるが、完走した列の値ではない。
- A-5 未達: metered 変数はどの実行の開始行にも無いが、列が `completed` でないので、
  overage 0・失敗 0 で完走したとは言えない。
- A-6 未達: 完走した列の `overage_check` とレート枠の記録が無い。

止めた理由: 列 `{CLUSTERED_RUN}` はクラスタ段を完走した(N={metrics["N"]})。続く想起素材の段が
`RecallRejected`(ungrounded_keys=175/175、missing_options=47)で止まった。件数は D-164 の記録から写した。
`RECALL_SYSTEM` は想起キーを本文の表記のまま抜き出すよう指示しておらず、`recall/verify.py` の実在検査と
噛み合わない。T-049 の要約と同じ形の欠陥(プロンプトと検査の不一致)である。

次サイクルでは、`RECALL_SYSTEM` を検査に合わせて直すタスクを先に置き、そのあとでフル処理の列を
`cluster --fresh-run` から取り直す。取り直しには、このファイルの `exec` と `silence` を使う。

{blocks}"""


def _render(args: argparse.Namespace) -> int:
    out_root: Path = args.out_root
    meter = _meter_lines(out_root, args.deck)
    # 起動前検査に落ちた実行は列に入らず、開始行も無い(D-132)ので、対にしない。
    entries = [e for e in _read_journal(out_root, args.deck) if e["entry"] == "execution" and e["run_id"] is not None]
    executions = _paired(entries, meter)
    run_ids = list(dict.fromkeys(e["run_id"] for e in entries))
    sequence_rows = [_sequence_row(r, sum(e["run_id"] == r for e in entries), meter) for r in run_ids]
    paired = {json.loads(start)["execution_id"] for _, start, _ in executions}
    cluster_entry = next(
        e for e in entries if e["run_id"] == CLUSTERED_RUN and _stage_of(e) == "cluster" and e["exit_code"] == 0
    )
    FULL_LOG.write_text(
        render_partial_log(
            args.deck, executions, sequence_rows, _unpaired_rows(meter, paired), _clusters_of(cluster_entry["stdout"])
        ),
        encoding="utf-8",
    )
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
    """実行1つの節から argv・実行時刻・コミット・終了コード・run_id・開始行・終了行を読む。"""
    started_at = datetime.fromisoformat(_table_value(body, "実行時刻"))
    commit = _table_value(body, "コミット")
    assert re.fullmatch(r"[0-9a-f]{7,40}", commit), f"コミット ID でない: {commit!r}"
    run_id = _table_value(body, "run_id")
    argv = _blocks(body, "text")
    assert argv and argv[0].strip(), "argv が無い"
    lines = _blocks(body, "json")
    assert len(lines) == 2, "開始行と終了行が揃っていない"
    start, end = json.loads(lines[0]), json.loads(lines[1])
    assert start["record"] == "start", "開始行の record が start でない"
    assert end["record"] == "end", "終了行の record が end でない"
    assert start["execution_id"] == end["execution_id"], "開始行と終了行の execution_id が違う"
    assert start["run_id"] == end["run_id"] == run_id, "開始行・終了行の run_id が実行の run_id と違う"
    assert datetime.fromisoformat(start["started_at"]) >= started_at, "開始行が実行の起動より前"
    exit_code = int(_table_value(body, "終了コード"))
    assert end["stop"]["exit_code"] == exit_code, "終了行の終了コードが実行の終了コードと違う"
    return {"argv": argv[0].splitlines(), "exit_code": exit_code, "start": start, "end": end}


def executions_of(text: str) -> list[dict[str, Any]]:
    executions = [_execution(body) for _, body in _sections(text, "実行 ")]
    assert executions, "実行の節が無い"
    return executions


def _assignment(body: str) -> dict[str, list[str]]:
    blocks = [json.loads(block) for block in _blocks(body, "json")]
    assert len(blocks) == 1, "割当がちょうど1つない"
    assignment: dict[str, list[str]] = blocks[0]
    return assignment


def _sequence_table(text: str) -> dict[str, list[str]]:
    """「列ごとの状態」の表を run_id → 残りの列で返す。"""
    body = _section(text, "列ごとの状態")
    rows = {}
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 8 and re.fullmatch(r"`[0-9a-f]{32}`", cells[0]):
            rows[cells[0].strip("`")] = cells[1:]
    assert rows, "列ごとの状態の表が無い"
    return rows


# ---- 判定 ----


def check_every_execution(text: str) -> None:
    """A-1: journal の実行すべてに argv・時刻・コミット・終了コード・run_id と、開始行・終了行がある。"""
    executions = executions_of(text)
    assert len(executions) == EXECUTIONS, f"実行が {EXECUTIONS} 件でない: {len(executions)}"
    assert int(_table_value(_section(text, "実行条件"), "実行数")) == len(executions), "実行数が節の数と合わない"
    ids = [execution["start"]["execution_id"] for execution in executions]
    assert len(set(ids)) == len(ids), "同じ実行の開始行を2度写している"
    for execution in executions:
        keys = set(execution["end"])
        assert keys == END_KEYS, (
            f"終了行のキー集合が設計の一覧と違う: 余分 {sorted(keys - END_KEYS)} 欠落 {sorted(END_KEYS - keys)}"
        )


def check_no_sequence_completed(text: str) -> None:
    """A-2: どの列も completed でなく、列ごとの status・段・理由と、クラスタを完走した列の3値がある。"""
    rows = _sequence_table(text)
    executed = {execution["start"]["run_id"] for execution in executions_of(text)}
    assert set(rows) == executed, f"記録した実行の列と表の列が違う: {sorted(set(rows) ^ executed)}"
    for run_id, (count, status, stage, reason, *_) in rows.items():
        status = status.strip("`")
        assert status != "completed", f"列 {run_id} が completed になっている"
        assert status in STATUSES, f"列 {run_id} の status が読めない: {status!r}"
        assert re.fullmatch(r"`[a-z_]+`", stage), f"列 {run_id} の止まった段が無い: {stage!r}"
        assert re.fullmatch(r"`[A-Za-z]+`|終了行なし", reason), f"列 {run_id} の理由が例外名でない: {reason!r}"
        assert int(count) >= 1, f"列 {run_id} の実行数が無い"
    assert CLUSTERED_RUN in rows, f"列 {CLUSTERED_RUN} が表に無い"
    body = _section(text, "圧縮の指標")
    expected = compression_metrics(_assignment(_section(text, "クラスタの割当")))
    for key in ("M/N", "最大クラスタ比率", "「その他」比率"):
        assert abs(_number(body, key) - expected[key]) < 5e-5, f"{key} が割当から計算した値と違う"


def check_no_metered_credentials(text: str) -> None:
    """A-3: どの実行の開始行も metered 認証変数を持たない。"""
    for execution in executions_of(text):
        start = execution["start"]
        assert start["metered_env_names"] == [], f"metered_env_names が空でない: {start['metered_env_names']}"
        names = start["env_names"]
        assert isinstance(names, list) and names, "環境変数名の一覧が記録されていない"
        assert not METERED_ENV_NAMES & set(names), f"metered 認証変数がある: {sorted(METERED_ENV_NAMES & set(names))}"


def check_handover(text: str) -> None:
    """A-4: 引き継ぎの節に、元の A-1〜A-6 の未達・止めた理由・RECALL_SYSTEM の修正を先に置くことがある。"""
    body = _section(text, HANDOVER)
    for number in range(1, 7):
        assert re.search(rf"^- A-{number} 未達: \S", body, re.MULTILINE), f"元の A-{number} が未達と書かれていない"
    for phrase in ("RecallRejected", "ungrounded_keys=175/175", "missing_options=47"):
        assert phrase in body, f"止めた理由に {phrase} が無い"
    assert re.search(r"次サイクルでは、`RECALL_SYSTEM` を[^。]*先に置", body), (
        "RECALL_SYSTEM の修正を先に置くと書かれていない"
    )


# ---- 実測ログに対する判定 ----


def _read() -> str:
    assert FULL_LOG.is_file(), f"実測ログが無い: {FULL_LOG.relative_to(ROOT)}"
    return FULL_LOG.read_text(encoding="utf-8")


def test_partial_record_lists_every_execution() -> None:
    """A-1"""
    check_every_execution(_read())


def test_partial_record_states_no_sequence_completed() -> None:
    """A-2"""
    check_no_sequence_completed(_read())


def test_partial_record_has_no_metered_credentials() -> None:
    """A-3"""
    check_no_metered_credentials(_read())


def test_partial_record_hands_over_to_the_next_cycle() -> None:
    """A-4"""
    check_handover(_read())


# ---- 判定が赤になること ----


def _broken(check: Any, text: str) -> None:
    try:
        check(text)
    except AssertionError:
        return
    raise AssertionError("壊した記録で判定が赤にならない")


def _drop_section(text: str, prefix: str) -> str:
    heading, _ = _sections(text, prefix)[-1]
    chunks = text.split("\n## ")
    return "\n## ".join(chunk for chunk in chunks if not chunk.startswith(heading + "\n"))


def test_record_missing_an_execution_is_red() -> None:
    _broken(check_every_execution, _drop_section(_read(), "実行 "))


def test_record_missing_an_end_line_is_red() -> None:
    text = _read()
    heading, body = _sections(text, "実行 ")[0]
    end = _blocks(body, "json")[1]
    _broken(check_every_execution, text.replace(f"終了行:\n\n```json\n{end}\n```\n", "", 1))


def test_record_with_a_completed_sequence_is_red() -> None:
    _broken(
        check_no_sequence_completed, re.sub(r"\| `(resumable|not_completable)` \|", "| `completed` |", _read(), count=1)
    )


def test_record_missing_a_compression_metric_is_red() -> None:
    _broken(check_no_sequence_completed, re.sub(r"^\| 「その他」比率 \|.*\n", "", _read(), flags=re.MULTILINE))


def test_record_with_a_metered_variable_is_red() -> None:
    _broken(check_no_metered_credentials, _read().replace('"env_names": ["', '"env_names": ["ANTHROPIC_API_KEY", "', 1))


def test_handover_without_the_recall_fix_is_red() -> None:
    _broken(check_handover, _read().replace("`RECALL_SYSTEM` を検査に合わせて直す", "何かを直す"))


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
