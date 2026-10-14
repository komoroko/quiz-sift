"""合成デッキを実 CLI で script 段まで通したリハーサルの記録の検査(T-068 / C-026、NFR-7 合成リハーサル)。

実 CLI はサンドボックスに無い(ADR-013)ので、証拠は**ホストで取った実測**で、このテストは記録した md の中身を
機械判定する。記録もこのファイルから作る:

- `python tests/measurements/test_recall_rehearsal_logs.py rehearse --out-root <作業ルート>` は、空の作業ルートに
  `.gitignore` と合成デッキ(`tests/fixtures/decks/synthetic-nofm/`)を写し、製品の CLI(`python -m quiz_sift.cli`)
  で `cluster --fresh-run` を走らせ、exit 0 なら同じ列に `recall --resume <run_id>`、続けて `script --resume <run_id>`
  を足す。各実行の argv・実行時刻・コミット ID・終了コード・run_id・標準出力(成果物のパス)を
  `<作業ルート>/out/synthetic-nofm/t068/journal.jsonl` へ追記する。標準エラーは同じディレクトリのファイルに置き、
  render は読まない。実行条件(`conditions.json`)には、そのときのコードの分類・要約・想起素材・判定の
  プロンプトとスキーマのダイジェストと、generate_recall の子の環境の鍵を書く。script まで exit 0 なら、
  合成デッキの各問題の誤答を正解と述べるミューテーションを、1問1回のまとめた判定で実 CLI
  (`ClaudeCliLlm.check_entailments`)に聞き、判定値の件数を `mutations.json` に
  その呼び出しの計測を `mutation-meter.jsonl` に書く。
- `... render --out-root <作業ルート>` は journal・`meter.jsonl`・recall と script の成果物・`mutations.json` から
  `docs/test/measurements/recall-rehearsal.md` を書く。LLM は呼ばない。開始行と終了行は加工せずに写す。
  レート枠の推定に使う前サイクルの列は、書庫の原本 `docs/archive/2026-10-06-fisrt/test/measurements/full-run.md` の
  終了行から読む(書庫は読むだけ)。

レート枠の使用率は utilization の差分から外挿しない(小数2桁に量子化されるので、10〜20 問の差分は 0 か過大に
なる。D-218)。呼び出し数とトークン数を 190 問に外挿し、前サイクルの列の「utilization の増分 / トークン数」を
掛けて推定する。utilization の生の値も併記する。

判定(各受入基準に1本):

- A-2: 記録のデッキのダイジェストがフィクスチャと一致し、記録のプロンプトとスキーマのダイジェストと子の環境の鍵が
  今のコードの値と一致し(古いコードで取った記録は赤)、cluster → recall → script が同じ列で exit 0 で終わり、
  想起素材の検査の件数 `CHECKS` がすべて 0 で、素材が全問ぶんある。含意判定の結果は製品がログに出さないので記録に
  無い。成果物は判定が `consistent` のときだけ書かれるので、exit 0 の recall が全問の素材を書いたことから通過を
  推論する。
- A-3: 全問の再生成回数(0〜`MAX_REGENERATIONS`)と、段ごとと合計の呼び出し数・トークン数・190 問への外挿・
  前サイクルの列の比から推定した枠の使用率・utilization の生の値があり、どれも記録した終了行から計算し直した値と
  一致する。
- A-4: ミューテーションの件数がフィクスチャの誤答の数と一致し、判定の呼び出しが問題数と同じ(1問1回)で、
  contradicted・uncertain・consistent の和が件数と一致する。合格ラインは置かない(D-044)。
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
FULL_LOG = ROOT / "docs" / "archive" / "2026-10-06-fisrt" / "test" / "measurements" / "full-run.md"
"""前サイクルの途中までの記録の原本。docs/test/measurements/full-run.md は T-071 の記録の置き場所(T-070)。"""
FIXTURE = ROOT / "tests" / "fixtures" / "decks" / "synthetic-nofm"

DECK = "synthetic-nofm"
METER_LOG = "meter.jsonl"
JOURNAL_DIR = "t068"
JOURNAL = "journal.jsonl"
STAGES = ("cluster", "recall", "script")
CONDITIONS = "conditions.json"
MUTATIONS = "mutations.json"
MUTATION_METER = "mutation-meter.jsonl"

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
MUTATION_COLUMNS = ("statements", "contradicted", "uncertain", "consistent")
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


def prompt_digest(system: str, schema: Mapping[str, object]) -> str:
    """システムプロンプトと JSON Schema のダイジェスト。記録がどのコードで取られたかを示す。"""
    payload = json.dumps([system, schema], ensure_ascii=False, sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def code_conditions() -> dict[str, Any]:
    """今のコードの、分類・要約・想起素材・判定のプロンプトとスキーマのダイジェストと、generate_recall の子の環境の鍵。

    子の環境は `ClaudeCliLlm._spawn` が `workspace.child_env()` に `CHILD_ENV` を重ねたもので、`SubprocessRunner` は
    鍵が `CHILD_ENV_KEYS` と `model_env` の和に一致しなければ起動しない。exit 0 の recall はこの鍵で走っている。
    """
    from quiz_sift.adapters import llm_prompts
    from quiz_sift.adapters.claude_process import CHILD_ENV_KEYS

    model = llm_prompts.MODELS["generate_recall"]
    return {
        "classify_digest": prompt_digest(llm_prompts.CLASSIFY_SYSTEM, llm_prompts.CLASSIFY_SCHEMA),
        "summarize_digest": prompt_digest(llm_prompts.SUMMARIZE_SYSTEM, llm_prompts.SUMMARIZE_SCHEMA),
        "recall_digest": prompt_digest(llm_prompts.RECALL_SYSTEM, llm_prompts.RECALL_SCHEMA),
        "entailment_digest": prompt_digest(llm_prompts.ENTAILMENT_SYSTEM, llm_prompts.ENTAILMENT_SCHEMA),
        "recall_env_keys": sorted(CHILD_ENV_KEYS | llm_prompts.model_env(model).keys()),
    }


def mutation_count(questions: Sequence[Mapping[str, Any]]) -> int:
    """ミューテーションの件数。各問題の誤答1件につき1件。"""
    return sum(len(q["options"]) - len(q["answers"]) for q in questions)


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
    (directory / CONDITIONS).write_text(
        json.dumps(
            {
                "cli_version": _cli_version(),
                "deck_digest": fixture_digest(out_root / "data" / DECK),
                **code_conditions(),
            }
        ),
        encoding="utf-8",
    )
    cluster = _exec(out_root, 1, ["cluster", "--fresh-run"])
    if cluster["exit_code"] != 0 or cluster["run_id"] is None:
        return int(cluster["exit_code"]) or 1
    for number, stage in enumerate(STAGES[1:], start=2):
        entry = _exec(out_root, number, [stage, "--resume", cluster["run_id"]])
        if entry["exit_code"] != 0:
            return int(entry["exit_code"])
    _mutate(out_root)
    return 0


def _mutate(out_root: Path) -> None:
    """各問題の誤答を正解と述べる主張を、1問1回のまとめた判定で実 CLI に聞き、判定値の件数を書く(D-044)。

    主張の本文と判定の応答は書かず、件数だけを残す。呼び出しは別の meter のログに数える(列の meter.jsonl とは
    混ぜない)。
    """
    import uuid

    from quiz_sift.adapters.claude_process import default_preflight
    from quiz_sift.composition import build_default_llm
    from quiz_sift.domain.ids import DeckId
    from quiz_sift.ingest.deck import ingest_deck
    from quiz_sift.pipeline.meter import Meter
    from quiz_sift.ports.clock import SystemClock
    from quiz_sift.ports.llm import Entailment, EntailmentStatement
    from quiz_sift.safety.entailment import ask, reference_for

    directory = _journal_dir(out_root)
    deck = ingest_deck(out_root / "data", DeckId(DECK)).deck
    default_preflight()
    meter = Meter(
        SystemClock(),
        log_path=directory / MUTATION_METER,
        run_id=uuid.uuid4().hex,
        execution_id=uuid.uuid4().hex,
        stage="mutations",
        cache_mode="fresh",
        env_names=os.environ.keys(),
    )
    llm = build_default_llm(meter)
    by_question: dict[str, dict[str, int]] = {}
    try:
        for question in deck.questions:
            wrong = [o.text for o in question.options if o.option_id not in question.correct_option_ids]
            statements = [
                EntailmentStatement(f"m{i}", f"この問題の正解は「{text}」である。") for i, text in enumerate(wrong, 1)
            ]
            verdicts = ask(llm, reference_for(question), statements)
            by_question[question.question_id.local_id] = {
                "statements": len(statements),
                **{e.value: verdicts.count(e) for e in Entailment},
            }
    except BaseException as error:
        meter.finish(exit_code=1, exit_reason=type(error).__name__)
        raise
    meter.finish(exit_code=0, exit_reason=None)
    (directory / MUTATIONS).write_text(json.dumps({"by_question": by_question}, ensure_ascii=False), encoding="utf-8")
    print(
        f"mutations: {sum(c['statements'] for c in by_question.values())} 件を {len(by_question)} 回で判定", flush=True
    )


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
    conditions: Mapping[str, Any],
    questions: Sequence[Mapping[str, Any]],
    executions: Sequence[tuple[Mapping[str, Any], str, str]],
    recall: Mapping[str, Any],
    script: Mapping[str, Any],
    mutations: Mapping[str, Any],
    mutation_end: str,
    full_log: str,
) -> str:
    report = recall["report"]
    ends = [json.loads(end) for _, _, end in executions]
    prior = previous_ends(full_log)
    ratios = usage_per_token(prior)
    usage = usage_estimate(ends, len(questions), ratios)
    scale = TARGET_QUESTIONS / len(questions)
    claims = sum(len(m["answers"]) + 1 for m in recall["materials"])
    multi = sum(len(q["answers"]) > 1 for q in questions)
    check_rows = "\n".join(f"| `{name}` | {report[name]} |" for name in CHECKS)
    regen_rows = "\n".join(f"| `{q}` | {n} |" for q, n in report["regenerations"].items())
    stage_rows = "\n".join(
        f"| {end['stage']} | {end['llm_calls']} | {end['provider_invocations']} | {total_tokens(end['tokens'])} "
        f"| {end['llm_calls'] * scale:.2f} | {total_tokens(end['tokens']) * scale:.2f} |"
        for end in (json.loads(start) | json.loads(e) for _, start, e in executions)
    )
    raw_rows = "\n".join(
        f"| {i} | `{end['execution_id']}` | {window} | {end['quota_at_start'][window]['utilization']} "
        f"| {end['quota_at_end'][window]['utilization']} | {end['quota_at_end'][window]['resetsAt']} |"
        for i, end in enumerate(ends, start=1)
        for window in WINDOWS
    )
    prior_tokens = sum(total_tokens(end["tokens"]) for end in prior)
    ratio_rows = "\n".join(f"| {window} | {_fmt(ratios[window])} |" for window in WINDOWS)
    by_question = mutations["by_question"]
    mutation_rows = "\n".join(
        f"| `{qid}` | {c['statements']} | {c['contradicted']} | {c['uncertain']} | {c['consistent']} |"
        for qid, c in by_question.items()
    )
    totals = {key: sum(c[key] for c in by_question.values()) for key in MUTATION_COLUMNS}
    tracks = script["tracks"]
    blocks = "\n".join(_execution_block(i, e, s, end) for i, (e, s, end) in enumerate(executions, start=1))
    return f"""# 実測ログ: 合成デッキの script 段までのリハーサル

NFR-7 の合成リハーサル(C-026)。合成デッキ `tests/fixtures/decks/synthetic-nofm/` を使い捨ての作業ルートに写し、
ホストの実 CLI で `cluster --fresh-run` を走らせ、同じ列に `recall --resume` と `script --resume` を足した記録である。
続けて、合成デッキから作ったミューテーションを、1問1回のまとめた判定で実 CLI に聞いた。

この md は `tests/measurements/test_recall_rehearsal_logs.py render` が journal・`meter.jsonl`・recall と script の
成果物・`mutations.json` から作った。LLM は呼んでいない。開始行と終了行は meter.jsonl の行を加工せずに写している。
合成デッキの文は教材ではないが、想起素材・台本・主張の本文は写さず、件数と ID だけを書く。

## 実行条件

ダイジェストと環境の鍵は、rehearse のときのコードから取った値である(`code_conditions`)。

| 項目 | 値 |
|---|---|
| デッキ | `{DECK}` |
| デッキのダイジェスト | `{conditions["deck_digest"]}` |
| 問題数 | {len(questions)} |
| 複数正解の問題数 | {multi} |
| CLI | `{conditions["cli_version"]}` |
| 実行数 | {len(executions)} |
| 分類のプロンプトとスキーマのダイジェスト | `{conditions["classify_digest"]}` |
| 要約のプロンプトとスキーマのダイジェスト | `{conditions["summarize_digest"]}` |
| 想起素材のプロンプトとスキーマのダイジェスト | `{conditions["recall_digest"]}` |
| 判定のプロンプトとスキーマのダイジェスト | `{conditions["entailment_digest"]}` |
| generate_recall の子の環境の鍵 | `{",".join(conditions["recall_env_keys"])}` |

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
| 判定に渡る主張の件数(素材から数えた値) | {claims} |
| 台本のトラック数 | {len(tracks)} |
| 台本の問題数 | {sum(len(t["questions"]) for t in tracks)} |

判定の結果は製品がログに出さないので、ここには実測した値が無い。recall と script の成果物は、含意判定が
`consistent` のときだけ書かれる(それ以外は `EntailmentRejected` か `RegenerationExhausted` で止まる)。上の
素材と台本は exit 0 の recall と script が書いた成果物から数えたもので、判定の通過はそこから推論している。主張の
件数も判定器に渡った件数の実測ではなく、素材ごとの答えの要素と識別ポイント1件を数えた値である。

## 問題ごとの再生成回数

| 問題 | 再生成回数 |
|---|---|
{regen_rows}

## 呼び出し数とトークン数

トークン数は終了行の `tokens` の4区分(input・cache_creation_input・cache_read_input・output)の和。190 問への
外挿は、問題数の比 {TARGET_QUESTIONS}/{len(questions)} で伸ばした値である。cluster 段の分類は全問を1度に見るので
問題数に比例しないが、ここでは比例として扱う。

段ごと:

| 段 | 呼び出し数 | プロバイダ呼び出し数 | トークン数 | 呼び出し数(外挿) | トークン数(外挿) |
|---|---|---|---|---|---|
{stage_rows}

cluster から script までの合計:

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

{TARGET_QUESTIONS} 問へ外挿したトークン数にこの比を掛けた、cluster から script までの枠の使用率の推定:

| 枠 | 推定使用率 |
|---|---|
| five_hour | {_fmt(usage["estimated_five_hour"])} |
| seven_day | {_fmt(usage["estimated_seven_day"])} |

## ミューテーション

各問題の誤答1件につき「この問題の正解は「誤答の本文」である。」という主張を作り、1問の主張をまとめて1回の
判定(`ClaudeCliLlm.check_entailments`、reference は `reference_for`)で実 CLI に聞いた件数。合格ラインは置かない
(D-044)。

| 項目 | 値 |
|---|---|
| ミューテーションの件数 | {totals["statements"]} |
| contradicted | {totals["contradicted"]} |
| uncertain | {totals["uncertain"]} |
| consistent | {totals["consistent"]} |

| 問題 | 件数 | contradicted | uncertain | consistent |
|---|---|---|---|---|
{mutation_rows}

判定の呼び出しの終了行(`mutation-meter.jsonl`):

```json
{mutation_end}
```

{blocks}"""


def _render(args: argparse.Namespace) -> int:
    out_root: Path = args.out_root.resolve()
    directory = _journal_dir(out_root)
    conditions = json.loads((directory / CONDITIONS).read_text(encoding="utf-8"))
    entries = [e for e in _read_journal(out_root) if e["run_id"] is not None]
    executions = _paired(entries, _meter_lines(out_root))

    def artifact(stage: str) -> Any:
        entry = next(e for e in entries if _stage_of(e) == stage and e["exit_code"] == 0)
        return json.loads(Path(entry["stdout"]).read_bytes())

    mutation_ends = [
        raw
        for raw in (directory / MUTATION_METER).read_text(encoding="utf-8").splitlines()
        if json.loads(raw)["record"] == "end"
    ]
    assert len(mutation_ends) == 1, f"ミューテーションの終了行が {len(mutation_ends)} 行ある"
    REHEARSAL_LOG.write_text(
        render_log(
            conditions,
            fixture_questions(out_root / "data" / DECK),
            executions,
            artifact("recall"),
            artifact("script"),
            json.loads((directory / MUTATIONS).read_text(encoding="utf-8")),
            mutation_ends[0],
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


def _table_with(body: str, header: str) -> str:
    """`body` の中で、見出し行が `header` で始まる表だけを返す。"""
    found = [t for t in body.split("\n\n") if t.startswith(header)]
    assert len(found) == 1, f"見出し {header} の表がちょうど1つない"
    return found[0]


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
    """A-2: 今のコードで、cluster → recall → script が同じ列で exit 0、検査がすべて 0、含意判定が通った。"""
    conditions = _table(_section(text, "実行条件"))
    assert conditions["デッキのダイジェスト"][0] == fixture_digest(), "記録のデッキがフィクスチャと違う"
    code = code_conditions()
    for label, key in (
        ("分類のプロンプトとスキーマのダイジェスト", "classify_digest"),
        ("要約のプロンプトとスキーマのダイジェスト", "summarize_digest"),
        ("想起素材のプロンプトとスキーマのダイジェスト", "recall_digest"),
        ("判定のプロンプトとスキーマのダイジェスト", "entailment_digest"),
    ):
        assert conditions[label][0] == code[key], f"{label} が今のコードの値と違う"
    recorded_keys = conditions["generate_recall の子の環境の鍵"][0].split(",")
    assert recorded_keys == code["recall_env_keys"], "generate_recall の子の環境の鍵が今のコードの値と違う"
    questions = fixture_questions()
    assert int(conditions["問題数"][0]) == len(questions), "問題数がフィクスチャと違う"
    executions = executions_of(text)
    assert [e["start"]["stage"] for e in executions] == list(STAGES), "実行が cluster → recall → script の順でない"
    assert executions[0]["start"]["cache_mode"] == "fresh", "cluster が新しい列で走っていない"
    for execution in executions[1:]:
        assert execution["start"]["cache_mode"] == "resume", f"{execution['heading']} が同じ列への追加になっていない"
    assert len({e["start"]["run_id"] for e in executions}) == 1, "段どうしの列が違う"
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
    claims = entailment["判定に渡る主張の件数(素材から数えた値)"][0]
    assert int(claims) == minimum, "主張の件数が答えの要素と識別ポイントの数と違う"


def check_usage_recorded(text: str) -> None:
    """A-3: 再生成回数、呼び出し数・トークン数とその外挿、前サイクルの比からの推定、utilization の生の値。"""
    questions = fixture_questions()
    regenerations = _table(_section(text, "問題ごとの再生成回数"))
    assert set(regenerations) == {q["id"] for q in questions}, "再生成回数が全問ぶん無い"
    for qid, (count,) in regenerations.items():
        assert 0 <= int(count) <= MAX_REGENERATIONS, f"問題 {qid} の再生成回数が範囲外: {count}"
    executions = executions_of(text)
    ends = [e["end"] for e in executions]
    ratios = usage_per_token(previous_ends(FULL_LOG.read_text(encoding="utf-8")))
    usage = usage_estimate(ends, len(questions), ratios)
    scale = TARGET_QUESTIONS / len(questions)
    counts = _table(_section(text, "呼び出し数とトークン数"))
    for execution in executions:
        stage, end = execution["start"]["stage"], execution["end"]
        calls, invocations, tokens, calls_190, tokens_190 = counts[stage]
        assert int(calls) == end["llm_calls"], f"{stage} の呼び出し数が終了行と違う"
        assert int(invocations) == end["provider_invocations"], f"{stage} のプロバイダ呼び出し数が終了行と違う"
        assert int(tokens) == total_tokens(end["tokens"]), f"{stage} のトークン数が終了行と違う"
        assert _close(calls_190, end["llm_calls"] * scale), f"{stage} の呼び出し数の外挿が違う"
        assert _close(tokens_190, total_tokens(end["tokens"]) * scale), f"{stage} のトークン数の外挿が違う"
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


def check_mutations_recorded(text: str) -> None:
    """A-4: 誤答ごとのミューテーションを1問1回で判定した件数・contradicted・uncertain。合格ラインは無い(D-044)。"""
    questions = fixture_questions()
    body = _section(text, "ミューテーション")
    summary = _table(_table_with(body, "| 項目 | 値 |"))
    per_question = _table(_table_with(body, "| 問題 | 件数 |"))
    total = int(summary["ミューテーションの件数"][0])
    assert total == mutation_count(questions), "ミューテーションの件数がフィクスチャの誤答の数と違う"
    verdicts = [int(summary[key][0]) for key in MUTATION_COLUMNS[1:]]
    assert sum(verdicts) == total, "判定値の件数の和がミューテーションの件数と違う"
    expected = {q["id"]: len(q["options"]) - len(q["answers"]) for q in questions}
    assert {qid: int(row[0]) for qid, row in per_question.items()} == expected, "問題ごとの件数がフィクスチャと違う"
    for column, key in enumerate(MUTATION_COLUMNS[1:], start=1):
        assert sum(int(row[column]) for row in per_question.values()) == int(summary[key][0]), f"{key} の和が違う"
    (end_line,) = _blocks(_section(text, "ミューテーション"), "json")
    end = json.loads(end_line)
    assert end["record"] == "end" and end["stop"]["exit_code"] == 0, "ミューテーションの判定が exit 0 で終わっていない"
    assert end["llm_calls"] == len(questions), "判定の呼び出しが1問1回になっていない"


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


def test_c026_record_has_batched_mutation_counts() -> None:
    """A-4"""
    check_mutations_recorded(_read())


# ---- 判定が赤になること ----


def _broken(check: Any, text: str, message: str) -> None:
    """壊した記録で、狙った判定(`message` を含む AssertionError)が赤になる。"""
    assert text != _read(), "記録を壊せていない"
    try:
        check(text)
    except AssertionError as error:
        assert message in str(error), f"狙いと別の判定で赤になった: {error}"
        return
    raise AssertionError("壊した記録で判定が赤にならない")


def test_record_with_a_nonzero_check_is_red() -> None:
    text = re.sub(r"^\| `missing_options` \| 0 \|$", "| `missing_options` | 1 |", _read(), count=1, flags=re.M)
    _broken(check_recall_passed, text, "検査 missing_options の件数が 0 でない")


def test_record_with_a_failed_recall_is_red() -> None:
    """表と終了行の両方を非0にして、整合の判定を越えて exit 0 の判定に届かせる。"""
    text = _read()
    heading, body = _sections(text, "実行 ")[-1]
    failed = body.replace("| 終了コード | `0` |", "| 終了コード | `1` |").replace(
        '"stop": {"exit_code": 0,', '"stop": {"exit_code": 1,'
    )
    _broken(check_recall_passed, text.replace(body, failed, 1), f"{heading} が exit 0 で終わっていない")


def test_record_taken_with_another_entailment_prompt_is_red() -> None:
    """古いコード(判定のプロンプトかスキーマが今と違う)で取った記録。"""
    current = code_conditions()["entailment_digest"]
    _broken(
        check_recall_passed,
        _read().replace(current, "sha256:" + "1" * 64, 1),
        "判定のプロンプトとスキーマのダイジェスト が今のコードの値と違う",
    )


def test_record_taken_with_another_summary_prompt_is_red() -> None:
    """作り直しの理由を渡す前(D-245)の要約のプロンプトで取った記録。"""
    current = code_conditions()["summarize_digest"]
    _broken(
        check_recall_passed,
        _read().replace(current, "sha256:" + "1" * 64, 1),
        "要約のプロンプトとスキーマのダイジェスト が今のコードの値と違う",
    )


def test_record_taken_with_another_recall_prompt_is_red() -> None:
    """作り直しの理由を渡す前(D-245)の想起素材のプロンプトで取った記録。"""
    current = code_conditions()["recall_digest"]
    _broken(
        check_recall_passed,
        _read().replace(current, "sha256:" + "1" * 64, 1),
        "想起素材のプロンプトとスキーマのダイジェスト が今のコードの値と違う",
    )


def test_record_taken_without_the_recall_env_is_red() -> None:
    """generate_recall の思考を止める環境(ADR-029)の前に取った記録。"""
    _broken(
        check_recall_passed,
        _read().replace(",MAX_THINKING_TOKENS,", ",", 1),
        "generate_recall の子の環境の鍵が今のコードの値と違う",
    )


def test_record_without_the_script_stage_is_red() -> None:
    text = _read()
    heading, body = _sections(text, "実行 ")[-1]
    _broken(check_recall_passed, text.replace(f"\n## {heading}\n{body}", "", 1), "cluster → recall → script の順でない")


def test_record_with_mutations_judged_one_by_one_is_red() -> None:
    """1件ずつ聞いた判定(呼び出し数がミューテーションの件数)は、まとめた判定の記録ではない。"""
    text = _read()
    calls = mutation_count(fixture_questions())
    block = _blocks(_section(text, "ミューテーション"), "json")[0]
    broken = re.sub(r'"llm_calls": \d+', f'"llm_calls": {calls}', block, count=1)
    _broken(check_mutations_recorded, text.replace(block, broken, 1), "判定の呼び出しが1問1回になっていない")


def test_record_with_a_wrong_mutation_total_is_red() -> None:
    text = re.sub(
        r"^\| ミューテーションの件数 \| \d+ \|$", "| ミューテーションの件数 | 1 |", _read(), count=1, flags=re.M
    )
    _broken(check_mutations_recorded, text, "ミューテーションの件数がフィクスチャの誤答の数と違う")


def test_record_from_another_deck_is_red() -> None:
    _broken(
        check_recall_passed,
        re.sub(r"sha256:[0-9a-f]{64}", "sha256:" + "0" * 64, _read(), count=1),
        "記録のデッキがフィクスチャと違う",
    )


def test_record_missing_a_regeneration_is_red() -> None:
    text = _read()
    first = _rows(_section(text, "問題ごとの再生成回数"))[0]
    _broken(check_usage_recorded, text.replace(f"| `{first[0]}` | {first[1]} |\n", "", 1), "再生成回数が全問ぶん無い")


def test_record_with_a_wrong_extrapolation_is_red() -> None:
    text = re.sub(r"^(\| トークン数 \| \d+ \| )[^|]+\|$", r"\g<1>1 |", _read(), count=1, flags=re.M)
    _broken(check_usage_recorded, text, "トークン数 の外挿が計算し直した値と違う")


def test_record_with_a_wrong_estimate_is_red() -> None:
    """比の表ではなく、推定使用率の表の行を壊す。"""
    head, marker, tail = _read().partition("| 枠 | 推定使用率 |")
    assert marker, "推定使用率の表が無い"
    tail = re.sub(r"^\| seven_day \| [^|]+ \|$", "| seven_day | 0 |", tail, count=1, flags=re.M)
    _broken(check_usage_recorded, head + marker + tail, "seven_day の推定が違う")


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
