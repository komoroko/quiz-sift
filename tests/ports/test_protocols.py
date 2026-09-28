"""ports の Protocol と、外部 SDK の import が adapters に閉じていること(C-019 / D-008)。"""

import ast
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from quiz_sift.adapters import llm_anthropic
from quiz_sift.adapters.llm_anthropic import AnthropicLlm
from quiz_sift.adapters.llm_prompts import CLASSIFY_BATCH_SIZE
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ports.clock import Clock, SystemClock
from quiz_sift.ports.llm import Entailment, Llm, LlmCallError, TokenUsage
from quiz_sift.ports.metering import FailureKind
from quiz_sift.ports.tts import PcmAudio, Tts

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "quiz_sift"
ADAPTERS = PACKAGE / "adapters"

NON_SDK_DEPENDENCIES = frozenset({"yaml", "fugashi", "unidic_lite"})
"""外部サービスに触らない実行時依存。adapters の外から import してよい。"""


def external_imports(source: str) -> set[str]:
    """標準ライブラリ・自パッケージ・非 SDK 依存のどれでもない import の最上位名。"""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    allowed = sys.stdlib_module_names | NON_SDK_DEPENDENCIES | {"quiz_sift"}
    return roots - allowed


def test_external_sdk_imports_live_only_in_adapters() -> None:
    offenders = {
        str(path.relative_to(PACKAGE)): found
        for path in PACKAGE.rglob("*.py")
        if ADAPTERS not in path.parents and (found := external_imports(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}

    # 検査器が空振りしていないこと: adapters の実装は SDK を import しており、それを検出する。
    assert "anthropic" in external_imports((ADAPTERS / "llm_anthropic.py").read_text(encoding="utf-8"))
    assert external_imports("from anthropic.types import Message\n") == {"anthropic"}
    assert external_imports("import json\nfrom quiz_sift.ports.llm import Llm\nimport yaml\n") == set()

    # Llm / Tts / Clock が Protocol として定義されている。
    for port, methods in (
        (Llm, {"classify_clusters", "summarize_cluster", "generate_recall", "describe_term", "check_entailment"}),
        (Tts, {"synthesize"}),
        (Clock, {"now", "monotonic"}),
    ):
        assert getattr(port, "_is_protocol", False), port
        assert methods <= {name for name, _ in inspect.getmembers(port, inspect.isfunction)}, port


def test_system_clock_satisfies_the_port() -> None:
    clock: Clock = SystemClock()
    assert clock.now().tzinfo is not None
    assert clock.monotonic() <= clock.monotonic()


def test_pcm_audio_refuses_malformed_frames() -> None:
    with pytest.raises(ValueError):
        PcmAudio(samples=b"\x00", sample_rate=24000)
    with pytest.raises(ValueError):
        PcmAudio(samples=b"", sample_rate=0)


# --- adapters/llm_anthropic.py(実 API は呼ばない。クライアントはスタブ)---


def make_question(n: int) -> Question:
    qid = QuestionId(DeckId("deck"), f"q{n}")
    options = tuple(Option(OptionId(qid, label), f"選択肢{label}") for label in "ABCD")
    return Question(qid, f"問題{n}", options, options[0].option_id, None, StudiedFlag.UNKNOWN)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, TokenUsage]] = []

    def record(self, model: str, usage: TokenUsage) -> None:
        self.calls.append((model, usage))

    def begin_call(self, method: str) -> None:
        pass


def response(payload: object, *, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(payload))],
        usage=SimpleNamespace(
            input_tokens=11, cache_creation_input_tokens=22, cache_read_input_tokens=None, output_tokens=44
        ),
    )


class FakeClient:
    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **request: Any) -> object:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_llm(client: FakeClient, recorder: Recorder) -> Llm:
    llm = AnthropicLlm(cast(Any, recorder))
    llm.client = cast(Any, client)
    return llm


def test_adapter_records_each_call_with_the_token_breakdown() -> None:
    recorder = Recorder()
    client = FakeClient(response({"verdict": "uncertain"}), response({"verdict": "x"}, stop_reason="refusal"))
    llm = make_llm(client, recorder)

    assert llm.check_entailment("正解は A", "A ではない") is Entailment.UNCERTAIN
    with pytest.raises(LlmCallError, match="refusal"):
        llm.check_entailment("正解は A", "A ではない")

    # 拒否された呼び出しも課金されるので記録される。None は 0 に読む。
    assert recorder.calls == [("claude-opus-5-5", TokenUsage(11, 22, 0, 44))] * 2
    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"]["format"]["type"] == "json_schema"


def test_classification_keeps_failed_batches_as_failures() -> None:
    questions = [make_question(n) for n in range(CLASSIFY_BATCH_SIZE + 1)]
    keys = [str(n) for n in range(CLASSIFY_BATCH_SIZE)]
    ok = response({"groups": [{"label": "S3", "question_ids": keys[:2]}], "unassigned": keys[2:]})
    client = FakeClient(ok, response({"groups": "not-a-list", "unassigned": []}))
    deck = Deck(DeckId("deck"), Language.JA, None, tuple(questions))
    result = make_llm(client, Recorder()).classify_clusters(deck)

    assert len(client.requests) == 2, "バッチごとに分割して呼ぶ"
    assert client.requests[0]["model"] == "claude-opus-5-5"
    (group,) = result.assignments
    assert group.label == "S3"
    assert group.question_ids == (questions[0].question_id, questions[1].question_id)
    (failure,) = result.failures
    assert failure.question_ids == (questions[-1].question_id,)
    assert failure.reason.startswith(FailureKind.SCHEMA.value)
    # 2回目の呼び出しには1回目のラベルが渡され、バッチ間でラベルが揃う。
    assert "S3" in client.requests[1]["messages"][0]["content"]


def test_anthropic_adapter_shares_the_id_completeness_contract() -> None:
    # 旧パーサはアダプタから消え、共有の契約へ移っている(AR-67)。
    for name in ("_proposals", "_string", "_schema"):
        assert not hasattr(llm_anthropic, name), name
    source = (ADAPTERS / "llm_anthropic.py").read_text(encoding="utf-8")
    assert "from quiz_sift.adapters.llm_contract import" in source

    # 旧挙動では「0 を欠いた応答」は 1 だけの割当として通っていた。今は INCOMPLETE_IDS の失敗になる。
    questions = [make_question(n) for n in range(2)]
    deck = Deck(DeckId("deck"), Language.JA, None, tuple(questions))
    missing = response({"groups": [{"label": "S3", "question_ids": ["1"]}], "unassigned": []})
    result = make_llm(FakeClient(missing), Recorder()).classify_clusters(deck)
    assert result.assignments == ()
    (failure,) = result.failures
    assert failure.question_ids == tuple(q.question_id for q in questions)
    assert failure.reason.startswith(FailureKind.INCOMPLETE_IDS.value)

    # 判定語のパースも共有: 未知の判定語は CONSISTENT にならず失敗する。
    llm = make_llm(FakeClient(response({"verdict": "probably"})), Recorder())
    with pytest.raises(LlmCallError, match=FailureKind.SCHEMA.value):
        llm.check_entailment("正解は A", "A である")


def test_recall_generation_returns_typed_draft() -> None:
    reply = response({"terms": ["S3", "ライフサイクル"], "answer": "移行する", "discrimination": "削除とは違う"})
    draft = make_llm(FakeClient(reply), Recorder()).generate_recall(make_question(0), Language.JA)
    assert draft.key.terms == ("S3", "ライフサイクル")
    assert (draft.answer, draft.discrimination) == ("移行する", "削除とは違う")
