"""`ports.llm.Llm` の Anthropic 実装(ADR-002)。

API キーは SDK が環境変数 `ANTHROPIC_API_KEY` から読む(NFR-3)。このモジュールはキーを
受け取りも保持もしない。応答は structured outputs(`output_config.format`)で JSON に縛る。
"""

import json

import anthropic

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import RecallKey
from quiz_sift.ports.llm import (
    CallFailure,
    ClassificationResult,
    Entailment,
    GroupProposal,
    LlmCallError,
    RecallDraft,
    TokenUsage,
    UsageRecorder,
)

MODEL = "claude-opus-5"
MAX_TOKENS = 16000
CLASSIFY_BATCH_SIZE = 50
"""分類の分割呼び出し1回あたりの問題数。"""

_LANGUAGE_NAMES = {Language.JA: "日本語", Language.EN: "English"}

_CLASSIFY_SYSTEM = (
    "あなたは資格試験の4択問題を、問われている論点ごとにグループ分けする。"
    "各問題はちょうど1つのグループに入れる。どこにも当てはまらない問題はどのグループにも入れない。"
    "label は論点を短く言い表す。既存のラベルが与えられた場合、同じ論点にはそのラベルを再利用する。"
)
_SUMMARIZE_SYSTEM = "あなたは同じ論点に属する4択問題群を読み、その論点を要約する。問題文に無い語句を持ち込まない。"
_RECALL_SYSTEM = (
    "あなたは4択問題1問から、耳だけで復習するための想起素材を作る。"
    "terms は答えを思い出す手がかりになる語・状況・条件の組。answer は正解の内容。"
    "discrimination は正解と紛らわしい誤答との切り分け。選択肢のラベル(A/B/C/D など)で言及しない。"
)
_DESCRIBE_SYSTEM = "あなたは資格試験に出る専門用語を、耳で聞いて分かる短い説明にする。"
_ENTAILMENT_SYSTEM = (
    "あなたは reference を正しい情報として、statement がそれと矛盾するかを判定する。"
    "矛盾しなければ consistent、矛盾すれば contradicted、reference から判断できなければ uncertain。"
)


def _schema(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_STRINGS = {"type": "array", "items": _STRING}
_CLASSIFY_SCHEMA = _schema(
    {"groups": {"type": "array", "items": _schema({"label": _STRING, "question_keys": _STRINGS})}}
)
_SUMMARIZE_SCHEMA = _schema({"summary": _STRING})
_RECALL_SCHEMA = _schema({"terms": _STRINGS, "answer": _STRING, "discrimination": _STRING})
_DESCRIBE_SCHEMA = _schema({"description": _STRING})
_ENTAILMENT_SCHEMA = _schema({"verdict": {"type": "string", "enum": [e.value for e in Entailment]}})


def _render_question(question: Question) -> dict[str, object]:
    correct = next(o.text for o in question.options if o.option_id == question.correct_option_id)
    return {
        "stem": question.stem,
        "options": [o.text for o in question.options],
        "correct": correct,
        "explanation": question.explanation,
    }


def _usage(response: anthropic.types.Message) -> TokenUsage:
    usage = response.usage
    return TokenUsage(
        input_tokens=usage.input_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        output_tokens=usage.output_tokens,
    )


class AnthropicLlm:
    def __init__(self, client: anthropic.Anthropic, recorder: UsageRecorder, *, model: str = MODEL) -> None:
        self._client = client
        self._recorder = recorder
        self._model = model

    def _call(self, system: str, payload: object, schema: dict[str, object]) -> dict[str, object]:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                thinking={"type": "adaptive"},
                system=system,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APIError as exc:
            raise LlmCallError(f"{type(exc).__name__}: {exc}") from exc
        # 拒否や打ち切りでも消費したトークンは課金されるので、判定より先に記録する。
        self._recorder.record(self._model, _usage(response))
        if response.stop_reason != "end_turn":
            raise LlmCallError(f"stop_reason={response.stop_reason}")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise LlmCallError("応答に text ブロックが無い")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LlmCallError(f"応答が JSON でない: {exc}") from exc
        if not isinstance(data, dict):
            raise LlmCallError("応答が JSON オブジェクトでない")
        return data

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        groups: list[GroupProposal] = []
        failures: list[CallFailure] = []
        for start in range(0, len(deck.questions), CLASSIFY_BATCH_SIZE):
            batch = deck.questions[start : start + CLASSIFY_BATCH_SIZE]
            # 応答のキーは入力のバッチへ写し戻すので、バッチ内の通し番号で渡す。
            keyed = {str(i): q for i, q in enumerate(batch)}
            payload = {
                "existing_labels": sorted({g.label for g in groups}),
                "questions": [{"key": k, **_render_question(q)} for k, q in keyed.items()],
            }
            try:
                proposals = _proposals(self._call(_CLASSIFY_SYSTEM, payload, _CLASSIFY_SCHEMA), keyed)
            except LlmCallError as exc:
                failures.append(CallFailure(tuple(q.question_id for q in batch), f"{type(exc).__name__}: {exc}"))
                continue
            groups.extend(proposals)
        return ClassificationResult(assignments=tuple(groups), failures=tuple(failures))

    def summarize_cluster(self, members: Deck) -> str:
        payload = {
            "language": _LANGUAGE_NAMES[members.language],
            "questions": [_render_question(q) for q in members.questions],
        }
        return _string(self._call(_SUMMARIZE_SYSTEM, payload, _SUMMARIZE_SCHEMA), "summary")

    def generate_recall(self, question: Question, language: Language) -> RecallDraft:
        payload = {"language": _LANGUAGE_NAMES[language], "question": _render_question(question)}
        data = self._call(_RECALL_SYSTEM, payload, _RECALL_SCHEMA)
        terms = data.get("terms")
        if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
            raise LlmCallError("terms が文字列の配列でない")
        try:
            key = RecallKey(terms=tuple(terms))
        except ValueError as exc:
            raise LlmCallError(str(exc)) from exc
        return RecallDraft(key=key, answer=_string(data, "answer"), discrimination=_string(data, "discrimination"))

    def describe_term(self, term: str, language: Language) -> str:
        payload = {"language": _LANGUAGE_NAMES[language], "term": term}
        return _string(self._call(_DESCRIBE_SYSTEM, payload, _DESCRIBE_SCHEMA), "description")

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        data = self._call(_ENTAILMENT_SYSTEM, {"reference": reference, "statement": statement}, _ENTAILMENT_SCHEMA)
        try:
            return Entailment(_string(data, "verdict"))
        except ValueError as exc:
            raise LlmCallError(str(exc)) from exc


def _proposals(data: dict[str, object], keyed: dict[str, Question]) -> list[GroupProposal]:
    groups = data.get("groups")
    if not isinstance(groups, list):
        raise LlmCallError("groups が配列でない")
    proposals = []
    for group in groups:
        if not isinstance(group, dict):
            raise LlmCallError("group がオブジェクトでない")
        label = group.get("label")
        keys = group.get("question_keys")
        if not isinstance(label, str) or not isinstance(keys, list):
            raise LlmCallError("group の label / question_keys の型が違う")
        # バッチに無いキーは写像先が無いので落とす。落ちた問題は cluster/ が「その他」に入れる。
        ids = tuple(keyed[k].question_id for k in keys if isinstance(k, str) and k in keyed)
        proposals.append(GroupProposal(label=label, question_ids=ids))
    return proposals


def _string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise LlmCallError(f"{key} が文字列でない")
    return value
