"""LLM 応答の純粋パーサ(AR-67)。SDK に触らず、どのアダプタも同じ契約で応答を読む。

分類応答は **ID 完全性**を満たすときだけ割当を返す(R-3): `flatten(groups[].question_ids) +
unassigned` が渡したキーと多重集合として一致しなければ `INCOMPLETE_IDS`。キーを黙って落とす
経路を持たないので、2つのアダプタの意味が分岐しない。
"""

from collections import Counter
from collections.abc import Mapping

from quiz_sift.domain.ids import OptionId
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.ports.llm import Entailment, GroupProposal, LlmCallError, RecallDraft
from quiz_sift.ports.metering import FailureKind


class ContractError(LlmCallError):
    """種別を持つ呼び出しの失敗。パーサは `SCHEMA` / `INCOMPLETE_IDS` で送出する。メッセージは種別の値で始まる。

    `detail` には子の出力から作った値(判定語・キー・ツール名など)を入れない。種別と件数だけを書く(NFR-2、ADR-024)。
    """

    def __init__(self, kind: FailureKind, detail: str) -> None:
        super().__init__(f"{kind.value}: {detail}")
        self.kind = kind
        self.detail = detail


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
        missing = (expected - seen).total()
        extra = (seen - expected).total()
        raise ContractError(FailureKind.INCOMPLETE_IDS, f"欠落 {missing} 件 重複または未知 {extra} 件")
    return [
        GroupProposal(label=label, question_ids=tuple(keyed[k].question_id for k in keys)) for label, keys in parsed
    ]


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ContractError(FailureKind.SCHEMA, f"{name} がオブジェクトでない")
    return value


def parse_recall(data: Mapping[str, object], keyed: Mapping[str, OptionId]) -> RecallDraft:
    """想起素材の応答を読む。`keyed` は呼び出しで渡した選択肢のキーと ID の対応(D-206)。

    渡していないキーは `SCHEMA` で落とし、例外文には件数だけを載せる(NFR-2)。キーの集合が正解と
    一致するかは、ここではなく `recall/verify` の検査が判定する(再生成の対象にするため)。
    """
    terms = _strings(data.get("terms"), "terms")
    try:
        key = RecallKey(terms=tuple(terms))
    except ValueError:
        raise ContractError(FailureKind.SCHEMA, "terms が想起キーにならない") from None
    answers = data.get("answers")
    if not isinstance(answers, list):
        raise ContractError(FailureKind.SCHEMA, "answers が配列でない")
    elements = [_object(element, "answers の要素") for element in answers]
    discrimination = _object(data.get("discrimination"), "discrimination")
    distractors = _strings(discrimination.get("distractors"), "discrimination の distractors")
    options = [parse_string(element, "option") for element in elements]
    unknown = sum(k not in keyed for k in (*options, *distractors))
    if unknown:
        raise ContractError(FailureKind.SCHEMA, f"未知の選択肢キー {unknown} 件")
    return RecallDraft(
        key=key,
        answers=tuple(
            AnswerElement(keyed[option], parse_string(element, "text"))
            for option, element in zip(options, elements, strict=True)
        ),
        discrimination=DiscriminationPoint(parse_string(discrimination, "text"), tuple(keyed[k] for k in distractors)),
    )


def parse_entailment(data: Mapping[str, object]) -> Entailment:
    """判定語を読む。未知の語は `SCHEMA` で落とし、`CONSISTENT` に読み替えない(fail-closed)。"""
    verdict = parse_string(data, "verdict")
    try:
        return Entailment(verdict)
    except ValueError:
        raise ContractError(FailureKind.SCHEMA, "未知の判定語") from None
