"""呼び出し経路の固定オーバーヘッドの較正表を採る(T-046 / C-019 NFR-7。設計「固定オーバーヘッドの分離」)。

**ホストで**人が走らせる。サンドボックスには `claude` が無く、ゲートはここで書いた表を読むだけ:

    PYTHONPATH=src uv run python tests/measurements/measure_overhead.py --repo <リポジトリ> [--model <モデル> ...]

前提(ADR-014 / ADR-017): 人が最小ホームを用意済みであること(`HOME=~/.quiz-sift-claude-home claude /login`)。

- argv は `ClaudeCliLlm` と同じ `build_argv` で組む(`--safe-mode` を含む)。本文のフラグ(`--system-prompt` /
  `--json-schema`)の値だけが合成のもので、`argv_digest` はその値を伏せて取るので製品の呼び出しと一致する。
- 子プロセスの起動は製品の `Workspace` / `SubprocessRunner` を使う。起動前検査も製品のものを先に走らせる。
  環境は製品の子と同じ `child_env()`(HOME・PATH・LANG・TZ の4つ。D-246)。
- 親環境に metered 認証変数が1つでもあれば採らない。
- 送るのは合成ブロック(`UNIT` を `REPEAT` 回並べたもの。既知長 `BLOCK_TOKENS`)の k 回反復だけ(k = 1, 2, 3)。
  既知長は構成から決めた値で、合計からは出さない。構成の仮定(`UNIT` 1つが1トークン)が外れていれば、
  ゲートの恒等式の判定が赤になる。

モデルごとに `docs/test/measurements/overhead-<model>.md` を書き直す。定数(`pipeline/overhead.py`)は
表の回帰から人が転記する。表と定数の突き合わせは `tests/measurements/test_overhead_logs.py` が行う。
"""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from quiz_sift.adapters import llm_prompts
from quiz_sift.adapters.claude_process import SubprocessRunner, Workspace
from quiz_sift.adapters.llm_claude_cli import argv_digest, build_argv
from quiz_sift.adapters.llm_contract import object_schema
from quiz_sift.pipeline.meter import METERED_ENV_NAMES
from quiz_sift.pipeline.overhead import skills_digest
from quiz_sift.ports.process import CommandSpec, Outcome

COLLECTOR = "tests/measurements/measure_overhead.py"
MODELS = tuple(sorted(set(llm_prompts.MODELS.values())))
KS = (1, 2, 3)
UNIT = " the"
REPEAT = 800
BLOCK_TOKENS = REPEAT
"""`UNIT` 1つを1トークンと数えた既知長。

opus 5.5 では " hello" が2トークンになったので " the" にした。長さは haiku のキャッシュ下限(約 4,096)を
k = 3 でも超えない値にした。
"""
SYSTEM_PROMPT = "Return ok=true."
SCHEMA = object_schema({"ok": {"type": "boolean"}})
BUCKETS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def table_path(repo: Path, model: str) -> Path:
    return repo / "docs" / "test" / "measurements" / f"overhead-{model}.md"


def measure_point(workspace: Workspace, argv: Sequence[str], k: int) -> dict[str, Any]:
    """合成ブロックの k 回反復を1回送り、表の1行ぶんを返す。失敗した呼び出しは表に入れず止める。"""
    with workspace.new_cwd() as cwd:
        spec = CommandSpec(argv=tuple(argv), env=workspace.child_env(), cwd=cwd, stdin=UNIT * REPEAT * k)
        result = SubprocessRunner().run(spec)
    if result.outcome is not Outcome.EXITED or result.returncode != 0:
        raise SystemExit(f"k={k}: claude が失敗した({result.outcome.value}, {result.returncode}): {result.stderr!r}")
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    final = [e for e in events if e.get("type") == "result"]
    init = [e for e in events if e.get("type") == "system" and e.get("subtype") == "init"]
    if len(final) != 1 or final[0].get("is_error") is not False or not init:
        raise SystemExit(f"k={k}: result が1件でないか is_error が偽でないか init が無い")
    usage = final[0]["usage"]
    counts = {bucket: usage[bucket] for bucket in BUCKETS}
    return {
        "k": k,
        "material": BLOCK_TOKENS * k,
        **counts,
        "total": sum(counts.values()),
        "num_turns": final[0]["num_turns"],
        "skills_digest": skills_digest(init[0].get("skills")),
    }


def regression(rows: Sequence[dict[str, Any]]) -> tuple[float, float]:
    """`total(k) = c + k·m` の最小二乗。(c, m) を返す。"""
    ks = [row["k"] for row in rows]
    totals = [row["total"] for row in rows]
    k_mean = sum(ks) / len(ks)
    t_mean = sum(totals) / len(totals)
    slope = sum((k - k_mean) * (t - t_mean) for k, t in zip(ks, totals, strict=True)) / sum(
        (k - k_mean) ** 2 for k in ks
    )
    return t_mean - slope * k_mean, slope


def bucket_of(rows: Sequence[dict[str, Any]]) -> str:
    """各点で最大のバケツ。3点で揃わなければ、1つの計上先を持つ定数にならないので止める。"""
    buckets = {max(BUCKETS, key=lambda b: row[b]) for row in rows}
    if len(buckets) != 1:
        raise SystemExit(f"計上先のバケツが点ごとに違う: {sorted(buckets)}")
    return buckets.pop()


def render(record: dict[str, Any], rows: Sequence[dict[str, Any]]) -> str:
    model = record["model"]
    columns = ["k", "material", *BUCKETS, "入力合計", "num_turns", "skills_digest"]
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows:
        cells = [row["k"], row["material"], *(row[b] for b in BUCKETS), row["total"], row["num_turns"]]
        lines.append("| " + " | ".join(str(c) for c in cells) + f" | `{row['skills_digest']}` |")
    table = "\n".join(lines)
    dumped = yaml.safe_dump(record, allow_unicode=True, sort_keys=False, width=1000)
    return f"""# 実測ログ: 固定オーバーヘッドの較正表(`{model}`)

NFR-7(C-019)の「呼び出し経路自身の固定オーバーヘッド」を、既知長の合成ブロックを k = 1, 2, 3 回反復した
入力で測った表。`{COLLECTOR}` がホストで書き、`tests/measurements/test_overhead_logs.py` が表から
切片を再計算して `src/quiz_sift/pipeline/overhead.py` の定数と突き合わせる。

argv は `ClaudeCliLlm` と同じ `build_argv` の形(`--safe-mode` を含む)で、`--system-prompt` と
`--json-schema` の値だけが合成のもの。`material` は合成ブロックの既知長 × k で、合計からの差し引きでは
出していない。入力合計は `input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` の和。

## 表

{table}

## 記録

```yaml
{dumped}```
"""


def measure(repo: Path, model: str, cli_version: str) -> None:
    workspace = Workspace()
    argv = build_argv(model, SYSTEM_PROMPT, SCHEMA)
    rows = [measure_point(workspace, argv, k) for k in KS]
    turns = {row["num_turns"] for row in rows}
    if len(turns) != 1:
        raise SystemExit(f"{model}: num_turns が点ごとに違う: {sorted(turns)}")
    intercept, slope = regression(rows)
    num_turns = turns.pop()
    record = {
        "model": model,
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cli_version": cli_version,
        "collector": COLLECTOR,
        "env_keys": sorted(workspace.child_env()),
        "metered_vars_in_parent_env": [],
        "argv": list(argv),
        "argv_digest": argv_digest(argv),
        "block": {"unit": UNIT, "repeat": REPEAT, "tokens": BLOCK_TOKENS},
        "bucket": bucket_of(rows),
        "regression": {
            "intercept": intercept,
            "slope": slope,
            "num_turns": num_turns,
            "per_turn_tokens": intercept / num_turns,
        },
    }
    path = table_path(repo, model)
    path.write_text(render(record, rows), encoding="utf-8")
    print(f"{path}: c={intercept} m={slope} num_turns={num_turns} per_turn_tokens={intercept / num_turns}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--model", action="append", choices=MODELS, help="測るモデル(既定はすべて)")
    args = parser.parse_args()
    metered = sorted(METERED_ENV_NAMES.intersection(os.environ))
    if metered:
        print(f"metered 認証変数が親環境にある(値は表示しない): {metered}", file=sys.stderr)
        return 2
    Workspace().preflight()
    env = Workspace().child_env()
    version = subprocess.run(["claude", "--version"], env=env, capture_output=True, text=True, check=True)
    cli_version = version.stdout.split()[0]
    for model in args.model or MODELS:
        measure(args.repo, model, cli_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
