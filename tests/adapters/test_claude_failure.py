"""子プロセス1本の失敗分類と overage の走査(R-3、NFR-8 (h)、D-119・D-120・D-125・D-131・D-134・D-156)。

イベント列はすべて `tests/support/claude_fake.py` のファクトリから作る(A-7 の自己適用)。期待値の種別と
判定順序は要件と設計から書き写す(製品の定数を読まない。AR-30)。
"""

import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from support.claude_fake import (
    assert_no_inline_stream_json,
    classification_output,
    exited,
    failure_shape,
    fake_stdout,
    spawn_failed,
    success_events,
    timed_out,
)

from quiz_sift.adapters.claude_failure import Judgement, classify, scan_rate_limits
from quiz_sift.adapters.llm_contract import parse_proposals
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ports.llm import GroupProposal
from quiz_sift.ports.metering import FailureKind, QuotaWindow, RateLimitInfo
from quiz_sift.ports.process import CommandResult, Outcome

TESTS_ROOT = Path(__file__).resolve().parents[1]

KEYS = ("k1", "k2", "k3")


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


KEYED = {key: _question(f"q{i}") for i, key in enumerate(KEYS, 1)}
COMPLETE = classification_output([KEYS[:2]], unassigned=KEYS[2:])


def parse(data: Mapping[str, Any]) -> list[GroupProposal]:
    return parse_proposals(data, KEYED)


def judge(result: CommandResult) -> Judgement[list[GroupProposal]]:
    return classify(result, parse)


def kind_of(result: CommandResult) -> FailureKind | None:
    return judge(result).failure


def test_classification_order_reaches_all_seven_kinds() -> None:
    success = judge(exited(fake_stdout(structured_output=COMPLETE)))
    assert success.failure is None
    assert success.value is not None
    assert [p.question_ids for p in success.value] == [(KEYED["k1"].question_id, KEYED["k2"].question_id)]

    # 7種すべてに到達する入力。
    for kind in FailureKind:
        assert kind_of(failure_shape(kind, KEYS)) is kind, kind

    # 隣り合う2条件を同時に満たす入力は、先の種別に落ちる。
    overage = fake_stdout(structured_output=COMPLETE, is_using_overage=True)
    pairs: list[tuple[str, CommandResult, FailureKind]] = [
        ("SPAWN > overage", CommandResult(Outcome.SPAWN, None, overage, b""), FailureKind.SPAWN),
        ("overage > TIMEOUT", timed_out(overage), FailureKind.PROVIDER_ERROR),
        ("TIMEOUT > PARSE", timed_out(fake_stdout(structured_output=COMPLETE)), FailureKind.TIMEOUT),
        ("PARSE > ツール", exited(fake_stdout(tool_use_name="Bash", result_count=0)), FailureKind.PARSE),
        (
            "ツール > TRUNCATED",
            exited(fake_stdout(tools=["StructuredOutput", "Glob"], subtype="error_max_turns")),
            FailureKind.PROVIDER_ERROR,
        ),
        (
            "TRUNCATED > is_error",
            exited(fake_stdout(subtype="error_max_turns", is_error=True)),
            FailureKind.TRUNCATED,
        ),
        (
            "is_error > SCHEMA",
            exited(fake_stdout(is_error=True, structured_output={"groups": "合成"})),
            FailureKind.PROVIDER_ERROR,
        ),
        (
            # label が文字列でなく(SCHEMA)、k3 も欠けている(INCOMPLETE_IDS)。
            "SCHEMA > INCOMPLETE_IDS",
            exited(fake_stdout(structured_output={"groups": [{"label": 1, "question_ids": ["k1"]}], "unassigned": []})),
            FailureKind.SCHEMA,
        ),
    ]
    for name, result, expected in pairs:
        assert kind_of(result) is expected, name


def test_truncated_parse_and_spawn_inputs_land_in_their_kinds() -> None:
    # TRUNCATED は subtype か result の stop_reason だけで決まる(D-120)。
    assert kind_of(exited(fake_stdout(subtype="error_max_turns", structured_output=COMPLETE))) is FailureKind.TRUNCATED
    for reason in ("max_tokens", "model_context_window_exceeded"):
        assert kind_of(exited(fake_stdout(stop_reason=reason, structured_output=COMPLETE))) is FailureKind.TRUNCATED
    for reason in ("end_turn", "tool_use", "synthetic_future_reason"):
        assert kind_of(exited(fake_stdout(stop_reason=reason, structured_output=COMPLETE))) is None, reason
    # assistant イベントの stop_reason は読まない(D-131)。
    assert kind_of(exited(fake_stdout(assistant_stop_reason="max_tokens", structured_output=COMPLETE))) is None

    # PARSE は stdout だけで決まり、終了コードと stderr に関係しない(D-119、AR-108)。
    broken = {
        "空の stdout": b"",
        "JSON でない行": fake_stdout(structured_output=COMPLETE, junk_line="合成の非 JSON 行"),
        "result の無い列": fake_stdout(result_count=0),
        "result が2つ": fake_stdout(structured_output=COMPLETE, result_count=2),
    }
    for name, stdout in broken.items():
        for returncode, stderr in ((0, b""), (1, b"Error: synthetic failure\n")):
            assert kind_of(exited(stdout, returncode, stderr)) is FailureKind.PARSE, (name, returncode)
    normal = fake_stdout(structured_output=COMPLETE)
    # JSON 文字列の中の U+2028 / U+2029 は行の区切りではない。
    separators = classification_output([KEYS], unassigned=())
    separators["groups"][0]["label"] = "合成\u2028グループ\u2029"
    assert kind_of(exited(fake_stdout(structured_output=separators))) is None
    assert kind_of(exited(normal, 1, b"Error: synthetic failure\n")) is None

    # SPAWN は起動に失敗したときだけ。起動した子が何も出さず 127 で終わっても SPAWN ではない。
    assert kind_of(spawn_failed()) is FailureKind.SPAWN
    assert kind_of(exited(b"", 127, b"claude: command not found\n")) is FailureKind.PARSE


def _fixture_rate_limit() -> RateLimitInfo:
    """成功フィクスチャの `rate_limit_event` を、製品のパーサを通さずキーごとに読んだ値。"""
    (event,) = (event for event in success_events() if event["type"] == "rate_limit_event")
    info = event["rate_limit_info"]
    windows = info["unifiedWindows"]
    return RateLimitInfo(
        status=info["status"],
        resets_at=info["resetsAt"],
        five_hour=QuotaWindow(windows["five_hour"]["utilization"], windows["five_hour"]["resetsAt"]),
        seven_day=QuotaWindow(windows["seven_day"]["utilization"], windows["seven_day"]["resetsAt"]),
        rate_limit_type=info["rateLimitType"],
        is_using_overage=info["isUsingOverage"],
        overage_status=info["overageStatus"],
    )


FIXTURE_RATE_LIMIT = _fixture_rate_limit()


def test_overage_scan_covers_parse_and_timeout_output() -> None:
    # それ以外の点で success / TRUNCATED / SCHEMA / INCOMPLETE_IDS になるものも PROVIDER_ERROR になる。
    otherwise: dict[str, dict[str, Any]] = {
        "success": {"structured_output": COMPLETE},
        "TRUNCATED": {"subtype": "error_max_turns", "structured_output": COMPLETE},
        "SCHEMA": {"structured_output": {"groups": "合成"}},
        "INCOMPLETE_IDS": {"structured_output": classification_output([KEYS[:1]])},
    }
    for name, values in otherwise.items():
        assert kind_of(exited(fake_stdout(**values))) is not FailureKind.PROVIDER_ERROR, name
        assert kind_of(exited(fake_stdout(is_using_overage=True, **values))) is FailureKind.PROVIDER_ERROR, name

    # 厳格なパースに落ちる stdout とタイムアウトの途中の stdout でも、走査は overage を拾う。
    unparsable = fake_stdout(is_using_overage=True, result_count=0, junk_line="合成の非 JSON 行")
    assert kind_of(exited(unparsable)) is FailureKind.PROVIDER_ERROR
    assert kind_of(exited(fake_stdout(result_count=0, junk_line="合成の非 JSON 行"))) is FailureKind.PARSE
    partial = timed_out(fake_stdout(is_using_overage=True, structured_output=COMPLETE))
    assert kind_of(partial) is FailureKind.PROVIDER_ERROR
    assert judge(partial).rate_limits.overage_seen is True

    # 子1本の最初と最後の RateLimitInfo(全フィールド)・件数・overage が1回でも真だったか。
    single = judge(exited(fake_stdout(structured_output=COMPLETE))).rate_limits
    assert (single.first, single.last, single.count, single.overage_seen) == (
        FIXTURE_RATE_LIMIT,
        FIXTURE_RATE_LIMIT,
        1,
        False,
    )
    # 真は途中の1件だけ。最後の値は偽でも overage_seen は真のまま。
    later = {"status": "allowed_warning", "resetsAt": 1790390000, "overageStatus": "allowed"}
    stream = fake_stdout(structured_output=COMPLETE, later_rate_limits=[{"isUsingOverage": True}, later])
    scan = scan_rate_limits(stream)
    assert scan.first == FIXTURE_RATE_LIMIT
    assert scan.last is not None
    assert (scan.last.status, scan.last.resets_at, scan.last.is_using_overage, scan.last.overage_status) == (
        "allowed_warning",
        1790390000,
        False,
        "allowed",
    )
    assert scan.last.rate_limit_type == "five_hour"
    assert (scan.count, scan.overage_seen) == (3, True)
    assert kind_of(exited(stream)) is FailureKind.PROVIDER_ERROR

    # イベントが1件も無ければ最後の値は None で、resetsAt は残らない。
    dropped = judge(exited(fake_stdout(structured_output=COMPLETE, drop_rate_limit_event=True)))
    assert dropped.failure is None
    assert (dropped.rate_limits.first, dropped.rate_limits.last) == (None, None)
    assert (dropped.rate_limits.count, dropped.rate_limits.overage_seen) == (0, False)


def test_tools_outside_the_allowlist_fail_the_call() -> None:
    assert kind_of(exited(fake_stdout(structured_output=COMPLETE))) is None
    for tools in (["StructuredOutput", "Glob"], ["Grep"], [], "StructuredOutput"):
        result = exited(fake_stdout(tools=tools, structured_output=COMPLETE))
        assert kind_of(result) is FailureKind.PROVIDER_ERROR, tools
    assert kind_of(exited(fake_stdout(tool_use_name="Bash", structured_output=COMPLETE))) is FailureKind.PROVIDER_ERROR


def test_event_streams_come_only_from_the_fake_factory(tmp_path: Path) -> None:
    assert_no_inline_stream_json(TESTS_ROOT)

    # negative control: dict リテラルと、それを含む文字列リテラルが赤になる。support/ と measurements/ は対象外。
    kind = "result"
    dict_source = "EVENT = {" + repr("type") + ": " + repr(kind) + ", " + repr("is_error") + ": False}\n"
    string_source = "LINE = " + repr('{"type"' + ': "rate_limit_event"}') + "\n"
    for name, source in (("dict_literal", dict_source), ("string_literal", string_source)):
        root = tmp_path / name
        (root / "adapters").mkdir(parents=True)
        (root / "adapters" / "test_inline.py").write_text(source, encoding="utf-8")
        with pytest.raises(AssertionError, match="test_inline.py"):
            assert_no_inline_stream_json(root)
    excluded = tmp_path / "excluded"
    for directory in ("support", "measurements"):
        (excluded / directory).mkdir(parents=True)
        (excluded / directory / "fake.py").write_text(dict_source, encoding="utf-8")
    assert_no_inline_stream_json(excluded)
    # 同じ "type" キーでも stream-json のイベントでない値は拒まない。
    (excluded / "test_schema.py").write_text("SCHEMA = {" + repr("type") + ": " + repr("array") + "}\n")
    assert_no_inline_stream_json(excluded)

    # ファクトリは成功フィクスチャの骨格から値だけを置き換える: result イベントのキーと rate_limit_info の
    # キーは、どの形でも骨格と同じ。
    skeleton = {event["type"]: event for event in success_events()}
    shapes = {
        "overage": exited(fake_stdout(is_using_overage=True)),
        "rejected": exited(fake_stdout(rate_limit_status="rejected")),
        "rate_limit_event なし": exited(fake_stdout(drop_rate_limit_event=True)),
        **{kind.name: failure_shape(kind, KEYS) for kind in FailureKind},
    }
    for name, result in shapes.items():
        for event in _events(result.stdout).values():
            assert event.keys() == skeleton[event["type"]].keys(), (name, event["type"])
            if event["type"] == "rate_limit_event":
                assert event["rate_limit_info"].keys() == skeleton[event["type"]]["rate_limit_info"].keys(), name
    assert scan_rate_limits(shapes["rejected"].stdout).last == dataclasses.replace(
        FIXTURE_RATE_LIMIT, status="rejected"
    )
    assert scan_rate_limits(shapes["rate_limit_event なし"].stdout).count == 0


def _events(stdout: bytes) -> dict[str, dict[str, Any]]:
    """読める行だけを type ごとに1件ずつ(同じ type は後勝ち)。"""
    events: dict[str, dict[str, Any]] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        events[event["type"]] = event
    return events
