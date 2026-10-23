"""実 CLI の契約フィクスチャと、fake の骨格(R-3、NFR-6、D-118、AR-79)。

実物フィクスチャ(T-041)は meta の終了コードと stderr を付けたまま判定器へ渡す。fake の出力は
`tests/support/claude_fake.py` の単一のファクトリだけが作り、result を持つすべての形のキー骨格が成功
フィクスチャと同じであることを確かめる。
"""

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from support.claude_fake import (
    EXECUTABLE_DIR,
    assert_no_inline_stream_json,
    classification_output,
    exited,
    failure_shape,
    fake_stdout,
    max_turns_stdout,
    skeleton,
    success_events,
)

from quiz_sift.adapters.claude_failure import classify
from quiz_sift.adapters.llm_contract import ContractError, parse_proposals
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ports.metering import FailureKind
from quiz_sift.ports.process import CommandResult, Outcome

TESTS_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = TESTS_ROOT / "fixtures" / "claude_cli"
SUCCESS = FIXTURES / "success.stdout"

KEYS = ("k1", "k2", "k3")
REPLACED_BY_MAX_TURNS = {"subtype", "is_error", "stop_reason", "structured_output"}
"""D-118 が差し替えを認めた4キー。"""


def fixture(name: str) -> CommandResult:
    """実物フィクスチャを、meta が記録した終了コードと stderr を付けて子の結果にする。"""
    meta = yaml.safe_load((FIXTURES / f"{name}.meta.yaml").read_text(encoding="utf-8"))
    stdout = (FIXTURES / f"{name}.stdout").read_bytes()
    return CommandResult(Outcome.EXITED, meta["exit_code"], stdout, meta["stderr"].encode("utf-8"))


def parse_answer(data: Mapping[str, Any]) -> int:
    """採取時のスキーマ(`{"answer": integer}`)の割当を読む。"""
    answer = data.get("answer")
    if isinstance(answer, bool) or not isinstance(answer, int):
        raise ContractError(FailureKind.SCHEMA, "answer が整数でない")
    return answer


def result_event(stdout: bytes) -> dict[str, Any]:
    results: list[dict[str, Any]] = [e for e in map(json.loads, stdout.splitlines()) if e["type"] == "result"]
    assert len(results) == 1
    return results[0]


def test_real_fixtures_parse_to_the_expected_kinds() -> None:
    for name in ("success", "success_opus"):
        judgement = classify(fixture(name), parse_answer)
        assert judgement.failure is None, (name, judgement.detail)
        assert judgement.value == 5, name
    invalid = fixture("invalid_schema")
    assert invalid.returncode == 1 and invalid.stderr
    assert classify(invalid, parse_answer).failure is FailureKind.PARSE

    # error_max_turns の形: 成功フィクスチャの骨格から4キーの値だけを置き換え、キーは消さない。
    truncated = max_turns_stdout()
    assert classify(exited(truncated, 1), parse_answer).failure is FailureKind.TRUNCATED
    real = result_event(SUCCESS.read_bytes())
    fake = result_event(truncated)
    assert fake.keys() == real.keys()
    assert {key for key in real if real[key] != fake[key]} == REPLACED_BY_MAX_TURNS
    assert (fake["subtype"], fake["is_error"]) == ("error_max_turns", True)
    # result 以外のイベントは成功フィクスチャと同一。
    others = [line for line in truncated.splitlines() if json.loads(line)["type"] != "result"]
    assert others == [line for line in SUCCESS.read_bytes().splitlines() if json.loads(line)["type"] != "result"]


def test_fake_output_has_the_fixture_skeleton_with_negative_control() -> None:
    assert_no_inline_stream_json(TESTS_ROOT)

    expected = skeleton(SUCCESS.read_bytes())
    complete = classification_output([KEYS])
    shapes = {
        "成功": fake_stdout(structured_output=complete),
        "overage": fake_stdout(structured_output=complete, is_using_overage=True),
        "rejected": fake_stdout(rate_limit_status="rejected", is_error=True, subtype="error_during_execution"),
        **{
            kind.name: failure_shape(kind, KEYS).stdout
            for kind in (
                FailureKind.TRUNCATED,
                FailureKind.PROVIDER_ERROR,
                FailureKind.SCHEMA,
                FailureKind.INCOMPLETE_IDS,
            )
        },
    }
    for name, stdout in shapes.items():
        assert skeleton(stdout) == expected, name

    # negative control: usage.cache_read_input_tokens を落とした fake はこの判定で赤になる。
    events = success_events()
    del next(event for event in events if event["type"] == "result")["usage"]["cache_read_input_tokens"]
    dropped = "\n".join(json.dumps(event) for event in events).encode("utf-8")
    assert skeleton(dropped) != expected


def test_path_entry_returns_the_same_key_multiset(tmp_path: Path) -> None:
    """PATH に置ける実行ファイル: stdin のプロンプトの問題キーを、同じ多重集合で返す。"""
    keys = ["0", "1", "2", "1"]
    prompt = json.dumps({"existing_labels": [], "questions": [{"key": key} for key in keys]})
    schema = json.dumps({"type": "object", "properties": {"groups": {}, "unassigned": {}}})
    env = {"HOME": str(tmp_path), "PATH": f"{EXECUTABLE_DIR}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.run(
        ["claude", "-p", "--json-schema", schema], input=prompt.encode(), capture_output=True, env=env, check=True
    )
    structured = result_event(proc.stdout)["structured_output"]
    returned = [key for group in structured["groups"] for key in group["question_ids"]] + structured["unassigned"]
    assert sorted(returned) == sorted(keys)

    # 実パーサで割当になる(キーの多重集合が一致しないと INCOMPLETE_IDS)。
    keyed = {key: _question(key) for key in ("0", "1", "2")}
    judged = classify(exited(proc.stdout), lambda data: parse_proposals(data, keyed))
    assert judged.failure is FailureKind.INCOMPLETE_IDS  # "1" が重複しているので、渡した3キーとは一致しない。
    unique = json.dumps({"existing_labels": [], "questions": [{"key": key} for key in keyed]})
    proc = subprocess.run(["claude", "--json-schema", schema], input=unique.encode(), capture_output=True, env=env)
    assert classify(exited(proc.stdout), lambda data: parse_proposals(data, keyed)).failure is None


def _question(name: str) -> Question:
    qid = QuestionId(DeckId("deckA"), name)
    return Question(
        question_id=qid,
        stem=f"合成問題文 {name}",
        options=tuple(Option(OptionId(qid, label), f"合成選択肢 {label}") for label in "ABCD"),
        correct_option_ids=(OptionId(qid, "A"),),
        explanation=None,
        studied=StudiedFlag.UNKNOWN,
    )
