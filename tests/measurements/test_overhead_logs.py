"""固定オーバーヘッドの較正表と定数の突き合わせ(T-046 / C-019 NFR-7)。

サンドボックスには `claude` が無いので、証拠は `tests/measurements/measure_overhead.py` がホストで書いた
`docs/test/measurements/overhead-<model>.md` で、このテストは表から切片を再計算して機械判定する。

- A-1: 2モデルとも k = 1, 2, 3 の3点があり、`ClaudeCliLlm` と同じ argv(`--safe-mode` を含む)で測った。
- A-2: 最小二乗の切片 c を num_turns で割った値と出所が `pipeline/overhead.py` の定数と一致する。
- A-3: 3点の回帰残差と、`overhead × turns + material = 入力合計` がどちらも相対誤差 `TOLERANCE` 以内。
  material は記録した既知長 × k からだけ出し、表の `material` 列もそれと一致しなければならない。
"""

import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from quiz_sift.adapters.llm_claude_cli import argv_digest, build_argv
from quiz_sift.pipeline.overhead import OVERHEAD, OverheadConstant

ROOT = Path(__file__).resolve().parent.parent.parent
MEASUREMENTS = ROOT / "docs" / "test" / "measurements"
COLLECTOR = "tests/measurements/measure_overhead.py"
MODELS = ("claude-opus-5-5", "claude-haiku-4-5")
KS = [1, 2, 3]
TOLERANCE = 0.01
"""相対誤差の許容幅。計画で凍結した値(AR-145)。"""
BUCKETS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
COLUMNS = ("k", "material", *BUCKETS, "入力合計", "num_turns", "skills_digest")
DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclasses.dataclass(frozen=True)
class Log:
    record: dict[str, Any]
    rows: list[dict[str, Any]]


def parse(text: str) -> Log:
    """「## 表」の Markdown 表と「## 記録」の YAML を読む。"""
    table = text.split("## 表", 1)[1].split("## 記録", 1)[0]
    lines = [line for line in table.splitlines() if line.startswith("|")]
    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    assert tuple(header) == COLUMNS, header
    rows = []
    for line in lines[2:]:
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        row: dict[str, Any] = {name: int(cell) for name, cell in zip(COLUMNS[:-1], cells[:-1], strict=True)}
        row["skills_digest"] = cells[-1]
        rows.append(row)
    block = re.search(r"## 記録\s*```yaml\n(.*?)```", text, re.DOTALL)
    assert block is not None, "## 記録 の YAML が無い"
    return Log(yaml.safe_load(block.group(1)), rows)


def load(model: str) -> Log:
    return parse((MEASUREMENTS / f"overhead-{model}.md").read_text(encoding="utf-8"))


def fit(log: Log) -> tuple[float, float]:
    """`total(k) = c + k·m` の最小二乗。表の入力合計だけから (c, m) を出す。"""
    ks = [row["k"] for row in log.rows]
    totals = [row["入力合計"] for row in log.rows]
    k_mean = sum(ks) / len(ks)
    t_mean = sum(totals) / len(totals)
    slope = sum((k - k_mean) * (t - t_mean) for k, t in zip(ks, totals, strict=True)) / sum(
        (k - k_mean) ** 2 for k in ks
    )
    return t_mean - slope * k_mean, slope


def current_argv_digest(model: str) -> str:
    """製品の argv のダイジェスト。本文フラグの値は伏せて取るので、ここで渡す本文は何でもよい。"""
    return argv_digest(build_argv(model, "", {}))


def check_table(model: str, log: Log) -> None:
    record = log.record
    assert record["model"] == model
    assert record["collector"] == COLLECTOR and (ROOT / COLLECTOR).is_file()
    assert record["env_keys"] == ["HOME", "LANG", "PATH", "TZ"]
    assert record["metered_vars_in_parent_env"] == []
    assert "--safe-mode" in record["argv"]
    assert record["argv"][record["argv"].index("--model") + 1] == model
    assert record["argv_digest"] == argv_digest(record["argv"])
    # ClaudeCliLlm の argv と、本文フラグの値を除いて同じ形で測った。
    assert record["argv_digest"] == current_argv_digest(model)
    assert [row["k"] for row in log.rows] == KS
    for row in log.rows:
        assert row["入力合計"] == sum(row[bucket] for bucket in BUCKETS), row
        assert row["num_turns"] > 0, row
        assert DIGEST.fullmatch(row["skills_digest"]), row
    assert len({row["skills_digest"] for row in log.rows}) == 1
    assert len({row["num_turns"] for row in log.rows}) == 1


def check_constant(model: str, log: Log, constant: OverheadConstant) -> None:
    intercept, _ = fit(log)
    num_turns = log.rows[0]["num_turns"]
    assert constant.model == model
    assert constant.per_turn_tokens == pytest.approx(intercept / num_turns, rel=1e-9)
    assert constant.bucket == log.record["bucket"]
    assert constant.bucket in BUCKETS
    # 計上先は、どの点でも最大のバケツ。
    assert all(max(BUCKETS, key=lambda b: row[b]) == constant.bucket for row in log.rows)
    assert constant.measured_at.isoformat() == log.record["measured_at"][:10]
    assert constant.cli_version == str(log.record["cli_version"])
    assert constant.argv_digest == current_argv_digest(model)
    assert constant.argv_digest == log.record["argv_digest"]
    assert constant.skills_digest == log.rows[0]["skills_digest"]


def relative_error(actual: float, expected: float) -> float:
    return abs(actual - expected) / expected


def check_linearity_and_identity(log: Log, constant: OverheadConstant) -> None:
    block_tokens = log.record["block"]["tokens"]
    assert block_tokens == log.record["block"]["repeat"]
    intercept, slope = fit(log)
    for row in log.rows:
        material = block_tokens * row["k"]
        # material は既知長 × k。合計からの差し引きで作った列はここで落ちる。
        assert row["material"] == material, row
        total = row["入力合計"]
        assert relative_error(intercept + slope * row["k"], total) <= TOLERANCE, row
        identity = constant.per_turn_tokens * row["num_turns"] + material
        assert relative_error(identity, total) <= TOLERANCE, (identity, row)


@pytest.mark.parametrize("model", MODELS)
def test_three_point_tables_exist_for_both_models(model: str) -> None:
    check_table(model, load(model))


@pytest.mark.parametrize("model", MODELS)
def test_constants_are_recomputed_from_the_tables(model: str) -> None:
    log = load(model)
    check_table(model, log)
    check_constant(model, log, OVERHEAD[model])


@pytest.mark.parametrize("model", MODELS)
def test_linearity_and_identity_hold_without_subtraction(model: str) -> None:
    log = load(model)
    check_table(model, log)
    check_linearity_and_identity(log, OVERHEAD[model])


# --- 判定が赤になるべき入力(判定そのものの negative control) ---


def synthetic_log(block_tokens: int, real_tokens_per_block: int, overhead: int, subtract: bool) -> Log:
    """合成ブロックの実トークン数が記録した既知長と違う表。`subtract` なら material を合計から差し引いて書く。"""
    rows = []
    for k in KS:
        total = overhead + real_tokens_per_block * k
        material = total - overhead if subtract else block_tokens * k
        rows.append(
            {
                "k": k,
                "material": material,
                "input_tokens": total,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "入力合計": total,
                "num_turns": 2,
                "skills_digest": "0" * 64,
            }
        )
    record = {"block": {"unit": " the", "repeat": block_tokens, "tokens": block_tokens}, "bucket": "input_tokens"}
    return Log(record, rows)


def synthetic_constant(per_turn_tokens: float) -> OverheadConstant:
    return dataclasses.replace(OVERHEAD["claude-haiku-4-5"], per_turn_tokens=per_turn_tokens)


def test_identity_is_green_when_the_known_length_is_right() -> None:
    log = synthetic_log(block_tokens=800, real_tokens_per_block=800, overhead=1000, subtract=False)
    check_linearity_and_identity(log, synthetic_constant(500.0))


def test_subtracted_material_table_is_red() -> None:
    # 実際は1ブロック 1600 トークン(既知長の仮定が外れた)。差し引きの material なら恒等式は常に成り立つ。
    log = synthetic_log(block_tokens=800, real_tokens_per_block=1600, overhead=1000, subtract=True)
    with pytest.raises(AssertionError):
        check_linearity_and_identity(log, synthetic_constant(500.0))


def test_wrong_known_length_is_red_even_with_an_honest_material_column() -> None:
    log = synthetic_log(block_tokens=800, real_tokens_per_block=1600, overhead=1000, subtract=False)
    with pytest.raises(AssertionError):
        check_linearity_and_identity(log, synthetic_constant(500.0))


def test_nonlinear_table_is_red() -> None:
    log = synthetic_log(block_tokens=800, real_tokens_per_block=800, overhead=1000, subtract=False)
    log.rows[1]["入力合計"] += 200
    with pytest.raises(AssertionError):
        check_linearity_and_identity(log, synthetic_constant(500.0))


@pytest.mark.parametrize("field", ["argv_digest", "skills_digest", "per_turn_tokens", "bucket"])
def test_altered_constant_is_red(field: str) -> None:
    model = "claude-opus-5-5"
    constant = OVERHEAD[model]
    value = getattr(constant, field)
    altered = ("0" if value[0] != "0" else "1") + value[1:] if isinstance(value, str) else value + 1.0
    with pytest.raises(AssertionError):
        check_constant(model, load(model), dataclasses.replace(constant, **{field: altered}))  # type: ignore[arg-type]
