"""子プロセス1本の記録(C-014 / C-015、NFR-8 (h)、AR-42、ADR-024)。本文と認証情報はすべて合成。

フィールド名は要件(T-042 A-5)から書き写す(製品の定数を読まない。AR-30)。
"""

import dataclasses
import hashlib
import io
import json
import logging
from collections.abc import Iterator

import pytest

from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ports.llm import TokenUsage
from quiz_sift.ports.metering import FailureKind, QuotaWindow, RateLimitInfo
from quiz_sift.safety.invocation_log import InvocationRecord, log_invocation, record_invocation
from quiz_sift.safety.log_filter import MATERIAL_MASK, MaterialTextFilter

FIELDS = (
    "argv",
    "exit_code",
    "duration_sec",
    "usage",
    "failure_kind",
    "result_is_error",
    "rate_limit_first",
    "rate_limit_last",
    "rate_limit_event_count",
    "overage_seen",
    "stdin_sha256",
    "stdin_len",
    "init_tools",
)

Q1 = QuestionId(DeckId("deckA"), "q1")
STEM = '合成問題文: "ゾーン"をまたぐ構成\nはどれか'
OPTION_TEXTS = ("合成選択肢アルファ", "合成選択肢ブラボー", "合成選択肢チャーリー", "合成選択肢デルタ")
QUESTION = Question(
    question_id=Q1,
    stem=STEM,
    options=tuple(Option(OptionId(Q1, label), text) for label, text in zip("ABCD", OPTION_TEXTS, strict=True)),
    correct_option_ids=(OptionId(Q1, "A"),),
    explanation=None,
    studied=StudiedFlag.UNKNOWN,
)
PROMPT_ONLY = "合成プロンプト固有の指示文"
"""stdin にだけ入る文字列。記録のどこにも現れてはならない。"""
STDIN = f"{PROMPT_ONLY}\nk1: {STEM}\n"
OAUTH_TOKEN = "sk-ant-oat01-SyntheticOauthToken_0123456789"
FIRST = RateLimitInfo(
    status="allowed",
    resets_at=1790383200,
    five_hour=QuotaWindow(utilization=0.53, resets_at=1790383200),
    seven_day=QuotaWindow(utilization=0.45, resets_at=1790550000),
    rate_limit_type="five_hour",
    is_using_overage=False,
    overage_status="rejected",
)
LAST = dataclasses.replace(FIRST, status="allowed_warning", is_using_overage=True)


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    target = logging.getLogger("quiz_sift.safety.invocation_log")
    level = target.level
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    yield stream
    target.removeHandler(handler)
    target.setLevel(level)


def _record() -> InvocationRecord:
    material = MaterialTextFilter()
    material.register_question(QUESTION)
    return record_invocation(
        argv=("claude", "-p", "--model", "claude-haiku-4-5", "--system-prompt", f"{STEM} {OAUTH_TOKEN}"),
        exit_code=1,
        duration_sec=3.5,
        usage=TokenUsage(1144, 0, 0, 128),
        failure_kind=FailureKind.PROVIDER_ERROR,
        result_is_error=True,
        rate_limit_first=FIRST,
        rate_limit_last=LAST,
        rate_limit_event_count=2,
        overage_seen=True,
        stdin=STDIN,
        init_tools=("StructuredOutput", OPTION_TEXTS[0]),
        material=material,
    )


def test_record_fields_are_separate_and_stdin_body_never_appears(captured: io.StringIO) -> None:
    record = _record()
    assert tuple(field.name for field in dataclasses.fields(InvocationRecord)) == FIELDS

    stdin_bytes = STDIN.encode("utf-8")
    assert record.stdin_sha256 == hashlib.sha256(stdin_bytes).hexdigest()
    assert record.stdin_len == len(stdin_bytes)
    # argv と init.tools の教材本文・認証情報は伏せ字になり、それ以外は残る(全文を置き換える実装を赤にする)。
    assert record.argv[:5] == ("claude", "-p", "--model", "claude-haiku-4-5", "--system-prompt")
    assert MATERIAL_MASK in record.argv[5] and STEM not in record.argv[5] and OAUTH_TOKEN not in record.argv[5]
    assert record.init_tools == ("StructuredOutput", MATERIAL_MASK)

    log_invocation(record)
    output = captured.getvalue()
    rendered = json.loads(output[output.index("{") :])
    assert tuple(rendered) == FIELDS
    assert rendered["argv"] == list(record.argv)
    assert (rendered["exit_code"], rendered["duration_sec"], rendered["failure_kind"]) == (1, 3.5, "provider_error")
    assert rendered["usage"] == {
        "input_tokens": 1144,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 128,
    }
    assert rendered["result_is_error"] is True
    assert rendered["rate_limit_first"]["status"] == "allowed"
    assert rendered["rate_limit_first"]["is_using_overage"] is False
    assert rendered["rate_limit_last"]["status"] == "allowed_warning"
    assert rendered["rate_limit_last"]["resets_at"] == 1790383200
    assert (rendered["rate_limit_event_count"], rendered["overage_seen"]) == (2, True)
    assert rendered["init_tools"] == ["StructuredOutput", MATERIAL_MASK]

    # stdin の本文は、どのフィールドにも、ログのどこにも入らない。
    for name in FIELDS:
        assert PROMPT_ONLY not in json.dumps(rendered[name], ensure_ascii=False), name
    assert PROMPT_ONLY not in output
    for text in (STEM, *OPTION_TEXTS, OAUTH_TOKEN):
        assert text not in output
        assert json.dumps(text, ensure_ascii=False)[1:-1] not in output


def test_a_call_without_rate_limit_events_renders_nulls(captured: io.StringIO) -> None:
    record = dataclasses.replace(
        _record(), exit_code=None, failure_kind=None, rate_limit_first=None, rate_limit_last=None
    )
    log_invocation(record)
    rendered = json.loads(captured.getvalue().split(" ", 2)[2])
    assert (rendered["exit_code"], rendered["failure_kind"]) == (None, None)
    assert (rendered["rate_limit_first"], rendered["rate_limit_last"]) == (None, None)
