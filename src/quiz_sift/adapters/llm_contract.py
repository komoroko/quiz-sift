"""LLM 応答の純粋パーサ(AR-67)。SDK に触らず、どのアダプタも同じ契約で応答を読む。

分類応答は **ID 完全性**を満たすときだけ割当を返す(R-3): `flatten(groups[].question_ids) +
unassigned` が渡したキーと多重集合として一致しなければ `INCOMPLETE_IDS`。キーを黙って落とす
経路を持たないので、2つのアダプタの意味が分岐しない。
"""

from collections import Counter
from collections.abc import Mapping

from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import RecallKey
from quiz_sift.ports.llm import Entailment, GroupProposal, LlmCallError, RecallDraft
from quiz_sift.ports.metering import FailureKind


class ContractError(LlmCallError):
    """応答が契約に合わない(`SCHEMA` / `INCOMPLETE_IDS`)。メッセージは種別の値で始まる。"""

    def __init__(self, kind: FailureKind, detail: str) -> None:
        super().__init__(f"{kind.value}: {detail}")
        self.kind = kind


def object_schema(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def parse_string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ContractError(FailureKind.SCHEMA, f"{key} が文字列でない")
    return value


def _strings(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ContractError(FailureKind.SCHEMA, f"{name} が文字列の配列でない")
    return value


def parse_proposals(data: Mapping[str, object], keyed: Mapping[str, Question]) -> list[GroupProposal]:
    """分類応答を割当に写す。`keyed` は呼び出しで渡したキーと問題の対応。"""
    groups = data.get("groups")
    if not isinstance(groups, list):
        raise ContractError(FailureKind.SCHEMA, "groups が配列でない")
    parsed: list[tuple[str, list[str]]] = []
    for group in groups:
        if not isinstance(group, dict):
            raise ContractError(FailureKind.SCHEMA, "group がオブジェクトでない")
        label = group.get("label")
        if not isinstance(label, str):
            raise ContractError(FailureKind.SCHEMA, "group の label が文字列でない")
        parsed.append((label, _strings(group.get("question_ids"), "group の question_ids")))
    unassigned = _strings(data.get("unassigned"), "unassigned")

    seen = Counter(k for _, keys in parsed for k in keys) + Counter(unassigned)
    expected = Counter(list(keyed))
    if seen != expected:
        missing = sorted(expected - seen)
        extra = sorted(seen - expected)
        raise ContractError(FailureKind.INCOMPLETE_IDS, f"欠落={missing} 重複または未知={extra}")
    return [
        GroupProposal(label=label, question_ids=tuple(keyed[k].question_id for k in keys)) for label, keys in parsed
    ]


def parse_recall(data: Mapping[str, object]) -> RecallDraft:
    terms = _strings(data.get("terms"), "terms")
    try:
        key = RecallKey(terms=tuple(terms))
    except ValueError as exc:
        raise ContractError(FailureKind.SCHEMA, str(exc)) from exc
    return RecallDraft(
        key=key, answer=parse_string(data, "answer"), discrimination=parse_string(data, "discrimination")
    )


def parse_entailment(data: Mapping[str, object]) -> Entailment:
    """判定語を読む。未知の語は `SCHEMA` で落とし、`CONSISTENT` に読み替えない(fail-closed)。"""
    verdict = parse_string(data, "verdict")
    try:
        return Entailment(verdict)
    except ValueError as exc:
        raise ContractError(FailureKind.SCHEMA, f"未知の判定語: {verdict!r}") from exc
