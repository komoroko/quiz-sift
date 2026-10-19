"""共有のプロンプト・スキーマと応答パーサ(R-3 の ID 完全性、ADR-027、AR-67、T-040)。

期待値は要件と設計からリテラルで書き写し、製品の定数を読まない(AR-30)。
"""

import ast
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from support.claude_fake import SENTINEL, exited, fake_stdout, sentinel_fragments

from quiz_sift import cli
from quiz_sift.adapters import llm_prompts
from quiz_sift.adapters.claude_failure import classify
from quiz_sift.adapters.llm_contract import ContractError, parse_entailment, parse_proposals, parse_recall
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ports.llm import Entailment, GroupProposal, Problem, RecallDraft, Retry
from quiz_sift.ports.metering import FailureKind

ADAPTERS = Path(__file__).resolve().parents[2] / "src" / "quiz_sift" / "adapters"


def make_question(n: int) -> Question:
    qid = QuestionId(DeckId("deck"), f"q{n}")
    options = tuple(Option(OptionId(qid, label), f"選択肢{label}") for label in "ABCD")
    return Question(qid, f"問題{n}", options, (options[0].option_id,), None, StudiedFlag.UNKNOWN)


KEYED = {str(n): make_question(n) for n in range(3)}
RECALL_KEYED = {f"o{i}": option.option_id for i, option in enumerate(make_question(0).options, start=1)}
"""想起素材の呼び出しで渡す選択肢のキー(`llm_prompts.recall_payload` と同じ形)。"""


def recall_reply(terms: list[str], option: str, distractor: str, text: str = "合成の文") -> dict[str, object]:
    return {
        "terms": terms,
        "answers": [{"option": option, "text": text}],
        "discrimination": {"text": text, "distractors": [distractor]},
    }


def reply(groups: list[list[str]], unassigned: list[str]) -> dict[str, object]:
    return {
        "groups": [{"label": f"L{i}", "question_ids": ids} for i, ids in enumerate(groups)],
        "unassigned": unassigned,
    }


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(reply([["0"]], ["1"]), id="missing"),
        pytest.param(reply([["0", "1"]], ["2", "1"]), id="duplicate"),
        pytest.param(reply([["0", "1"]], ["2", "9"]), id="unknown"),
    ],
)
def test_classification_requires_exact_id_multiset(data: dict[str, object]) -> None:
    with pytest.raises(ContractError) as caught:
        parse_proposals(data, KEYED)
    assert caught.value.kind is FailureKind.INCOMPLETE_IDS
    assert str(caught.value).startswith("incomplete_ids")

    # 多重集合として一致する応答だけが割当を返す。unassigned の問題は割当に入らない。
    ok = parse_proposals(reply([["0", "2"]], ["1"]), KEYED)
    assert ok == [GroupProposal("L0", (KEYED["0"].question_id, KEYED["2"].question_id))]


def test_classification_shape_errors_are_schema() -> None:
    for data in ({"groups": [{"label": "L", "question_ids": ["0", "1", "2"]}]}, {"groups": "x", "unassigned": []}):
        with pytest.raises(ContractError) as caught:
            parse_proposals(data, KEYED)
        assert caught.value.kind is FailureKind.SCHEMA


def test_schema_requires_unassigned_and_batch_is_25() -> None:
    schema = llm_prompts.CLASSIFY_SCHEMA
    assert "unassigned" in schema["required"]  # type: ignore[operator]
    assert schema["properties"]["unassigned"] == {"type": "array", "items": {"type": "string"}}  # type: ignore[index]
    group = schema["properties"]["groups"]["items"]  # type: ignore[index]
    assert group["required"] == ["label", "question_ids"]

    system = llm_prompts.CLASSIFY_SYSTEM
    assert "どこにも当てはまらない問題は unassigned に入れる" in system
    assert "すべての問題キーがちょうど1回現れること" in system
    assert llm_prompts.CLASSIFY_BATCH_SIZE == 25


def test_model_per_method_follows_adr_027() -> None:
    assert llm_prompts.MODELS == {
        "classify_clusters": "claude-opus-5-5",
        "summarize_cluster": "claude-opus-5-5",
        "generate_recall": "claude-haiku-4-5",
        "describe_term": "claude-opus-5-5",
        "check_entailments": "claude-sonnet-5-5",
    }


def test_entailment_prompt_does_not_treat_omission_or_generalization_as_contradiction() -> None:
    """元の2文を残し、要約でありうること・省略と一般化は矛盾でないこと・contradicted の条件を足す(D-163)。"""
    for sentence in (
        "あなたは reference を正しい情報として、statement がそれと矛盾するかを判定する。",
        "矛盾しなければ consistent、矛盾すれば contradicted、reference から判断できなければ uncertain。",
        "statement は reference の複数の問題をまとめた要約でありうる。",
        "reference の一部を省略していること、一般化していること、"
        "reference に書かれていない事柄に触れていないことは矛盾ではない。",
        "statement の主張が reference の正解・解説と両立しないときだけ contradicted とする。",
    ):
        assert sentence in llm_prompts.ENTAILMENT_SYSTEM


def test_unknown_verdict_is_not_consistent() -> None:
    assert parse_entailment({"verdict": "contradicted"}) is Entailment.CONTRADICTED
    for data in ({"verdict": "Consistent"}, {"verdict": ""}, {}):
        with pytest.raises(ContractError) as caught:
            parse_entailment(data)
        assert caught.value.kind is FailureKind.SCHEMA


def imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_shared_modules_do_not_import_the_sdk() -> None:
    for name in ("llm_prompts.py", "llm_contract.py"):
        assert "anthropic" not in imported_roots((ADAPTERS / name).read_text(encoding="utf-8")), name
    # 検査器が空振りしていないこと。
    assert "anthropic" in imported_roots("import anthropic.types as t\n")


OTHER = "YwvK別の番兵Rr7m"
"""番兵と別の値。例外文が子の値から作られていなければ、どちらを渡しても同じ文になる。"""


def _contract_error(parse: Callable[[], object]) -> ContractError:
    with pytest.raises(ContractError) as caught:
        parse()
    return caught.value


def _judged(**stdout: Any) -> tuple[FailureKind | None, str]:
    judgement = classify(exited(fake_stdout(**stdout)), parse_entailment)
    return judgement.failure, judgement.detail


# 子の出力から作る値(判定語・未知のキー・ツール名・subtype・stop_reason)を `value` として受け、例外か判定を作る。
PARSERS: dict[str, tuple[FailureKind, Callable[[str], str]]] = {
    "unknown_verdict": (FailureKind.SCHEMA, lambda v: str(_contract_error(lambda: parse_entailment({"verdict": v})))),
    "unknown_key": (
        FailureKind.INCOMPLETE_IDS,
        lambda v: str(_contract_error(lambda: parse_proposals(reply([["0", "1", "2"]], [v]), KEYED))),
    ),
    "unknown_key_in_group": (
        FailureKind.INCOMPLETE_IDS,
        lambda v: str(_contract_error(lambda: parse_proposals(reply([["0", v]], ["2"]), KEYED))),
    ),
    "empty_terms": (
        FailureKind.SCHEMA,
        lambda v: str(_contract_error(lambda: parse_recall(recall_reply([], "o1", "o2", v), RECALL_KEYED))),
    ),
    "unknown_answer_option": (
        FailureKind.SCHEMA,
        lambda v: str(_contract_error(lambda: parse_recall(recall_reply(["語"], v, "o2"), RECALL_KEYED))),
    ),
    "unknown_distractor": (
        FailureKind.SCHEMA,
        lambda v: str(_contract_error(lambda: parse_recall(recall_reply(["語"], "o1", v), RECALL_KEYED))),
    ),
    "init_tools": (FailureKind.PROVIDER_ERROR, lambda v: repr(_judged(tools=["StructuredOutput", v]))),
    "tool_use_name": (FailureKind.PROVIDER_ERROR, lambda v: repr(_judged(tool_use_name=v))),
    "truncated_stop_reason": (
        FailureKind.TRUNCATED,
        lambda v: repr(_judged(subtype="error_max_turns", is_error=True, stop_reason=v, structured_output=None)),
    ),
    "is_error_subtype": (
        FailureKind.PROVIDER_ERROR,
        lambda v: repr(_judged(subtype=v, is_error=True, structured_output=None)),
    ),
    "parse_junk_line": (FailureKind.PARSE, lambda v: repr(_judged(result_count=0, junk_line=v))),
    "verdict_through_classify": (FailureKind.SCHEMA, lambda v: repr(_judged(structured_output={"verdict": v}))),
}


@pytest.mark.parametrize("name", list(PARSERS))
def test_parse_errors_carry_kind_and_count_only(name: str) -> None:
    """どのパーサの例外文(と判定の説明)にも子の出力から作った値が載らず、種別と件数だけになる(C-014、ADR-024)。"""
    kind, shown = PARSERS[name]
    message = shown(SENTINEL)
    assert kind.value in message
    for fragment in sentinel_fragments():
        assert fragment not in message, (name, fragment)
    # 値を差し替えても文が変わらない: 例外文は値から作られていない。
    assert shown(OTHER) == message


@pytest.mark.parametrize(
    "parse",
    [
        pytest.param(lambda: parse_entailment({"verdict": SENTINEL}), id="verdict"),
        pytest.param(lambda: parse_recall(recall_reply([], "o1", "o2", SENTINEL), RECALL_KEYED), id="terms"),
        pytest.param(lambda: parse_recall(recall_reply(["語"], SENTINEL, "o2"), RECALL_KEYED), id="recall_key"),
    ],
)
def test_parse_errors_do_not_chain_the_value(parse: Callable[[], object]) -> None:
    """トレースバックに出る連鎖例外(`__cause__` / `__context__`)にも値が載らない。"""
    shown = "".join(traceback.format_exception(_contract_error(parse)))
    for fragment in sentinel_fragments():
        assert fragment not in shown, fragment


def test_recall_keys_map_back_to_option_ids() -> None:
    """想起素材の応答は、渡した不透明なキーを選択肢 ID に写し戻す(D-206)。"""
    question = make_question(0)
    payload, keyed = llm_prompts.recall_payload(question)
    assert keyed == RECALL_KEYED
    # 元のラベル(A/B…)ではなく通し番号のキーで渡す。
    assert payload["options"] == [{"key": key, "text": o.text} for key, o in zip(keyed, question.options, strict=True)]
    assert payload["correct"] == ["o1"]

    draft = parse_recall(recall_reply(["語"], "o1", "o3"), keyed)
    assert [(e.option_id, e.text) for e in draft.answers] == [(question.options[0].option_id, "合成の文")]
    assert draft.discrimination.distractor_ids == (question.options[2].option_id,)
    # 未知のキーは件数だけを載せた SCHEMA。答えと識別ポイントの両方を数える。
    data = recall_reply(["語"], "x", "y")
    error = _contract_error(lambda: parse_recall(data, keyed))
    assert error.kind is FailureKind.SCHEMA and "2 件" in str(error)
    for broken in ({**data, "answers": "x"}, {**data, "answers": ["x"]}, {**data, "discrimination": []}):
        with pytest.raises(ContractError) as caught:
            parse_recall(broken, keyed)
        assert caught.value.kind is FailureKind.SCHEMA


def test_recall_prompt_explains_option_key() -> None:
    """T-087 A-2: 想起素材のプロンプトが option_key の意味を書く。段の prompt_template に入るので recall 段の鍵が動く。"""
    system = llm_prompts.RECALL_SYSTEM
    assert "option_key は" in system
    explained = system[system.index("option_key は") :].split("。")[0]
    assert "キー" in explained and "o1" in explained
    assert "本文では選択肢のラベル(A/B/C/D など)やキーで言及しない。" in system
    assert system in cli.RECALL.prompt_template


def test_prompts_explain_retry_and_check_names() -> None:
    """T-086 A-4: 要約と想起素材のプロンプトが retry の扱いと各検査の名前の意味を書く(D-245)。

    段の prompt_template はこのプロンプトを含むので、書き足したことで cluster・recall 段のダイジェストが動き、
    retry を持たない旧形式の途中結果は使われない。`Retry` と `Problem` は repr に本文を出さない。
    """

    summary_checks = ("ungrounded_terms", "entailment")
    recall_checks = (
        "empty",
        "ungrounded_key",
        "answer_mismatch",
        "missing_options",
        "generic",
        "verbatim",
        "option_key",
        "duplicate_key",
        "entailment",
    )
    for system, checks in ((llm_prompts.SUMMARIZE_SYSTEM, summary_checks), (llm_prompts.RECALL_SYSTEM, recall_checks)):
        assert "retry.previous" in system and "retry.problems" in system
        for check in checks:
            assert f"{check} は" in system, check
    assert llm_prompts.RECALL_SYSTEM == llm_prompts.render_recall_system(("stem",))
    assert llm_prompts.SUMMARIZE_SYSTEM in cli.CLUSTER.prompt_template
    assert llm_prompts.RECALL_SYSTEM in cli.RECALL.prompt_template

    option = OptionId(QuestionId(DeckId("d"), "q"), "A")
    problem = Problem("entailment", ("語句本文",), (option,), ("terms[0]",), (("主張本文", Entailment.UNCERTAIN),))
    draft = RecallDraft(
        RecallKey(("キー本文",)), (AnswerElement(option, "答え本文"),), DiscriminationPoint("識別本文", ())
    )
    for value in (problem, Retry("要約本文", (problem,)), Retry(draft, (problem,))):
        assert not any(
            text in repr(value) for text in ("語句本文", "主張本文", "要約本文", "キー本文", "答え本文", "識別本文")
        )
