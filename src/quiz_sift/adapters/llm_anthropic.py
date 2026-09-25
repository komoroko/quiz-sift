"""`ports.llm.Llm` の Anthropic 実装(ADR-002)。

API キーは SDK が環境変数 `ANTHROPIC_API_KEY` から読む(NFR-3)。このモジュールはキーを
受け取りも保持もしない。応答は structured outputs(`output_config.format`)で JSON に縛る。
"""

import json

import anthropic

from quiz_sift.adapters import llm_prompts as prompts
from quiz_sift.adapters.llm_contract import parse_entailment, parse_proposals, parse_recall, parse_string
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
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

MAX_TOKENS = 16000


def _usage(response: anthropic.types.Message) -> TokenUsage:
    usage = response.usage
    return TokenUsage(
        input_tokens=usage.input_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        output_tokens=usage.output_tokens,
    )


class AnthropicLlm:
    """モデルはメソッドごとに `llm_prompts.MODELS` から選ぶ(ADR-015)。"""

    def __init__(self, client: anthropic.Anthropic, recorder: UsageRecorder) -> None:
        self._client = client
        self._recorder = recorder

    def _call(self, method: str, system: str, payload: object, schema: dict[str, object]) -> dict[str, object]:
        model = prompts.MODELS[method]
        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                thinking={"type": "adaptive"},
                system=system,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APIError as exc:
            raise LlmCallError(f"{type(exc).__name__}: {exc}") from exc
        # 拒否や打ち切りでも消費したトークンは課金されるので、判定より先に記録する。
        self._recorder.record(model, _usage(response))
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
        for start in range(0, len(deck.questions), prompts.CLASSIFY_BATCH_SIZE):
            batch = deck.questions[start : start + prompts.CLASSIFY_BATCH_SIZE]
            # 応答のキーは入力のバッチへ写し戻すので、バッチ内の通し番号で渡す。
            keyed = {str(i): q for i, q in enumerate(batch)}
            payload = {
                "existing_labels": sorted({g.label for g in groups}),
                "questions": [{"key": k, **prompts.render_question(q)} for k, q in keyed.items()],
            }
            try:
                data = self._call("classify_clusters", prompts.CLASSIFY_SYSTEM, payload, prompts.CLASSIFY_SCHEMA)
                proposals = parse_proposals(data, keyed)
            except LlmCallError as exc:
                failures.append(CallFailure(tuple(q.question_id for q in batch), str(exc)))
                continue
            groups.extend(proposals)
        return ClassificationResult(assignments=tuple(groups), failures=tuple(failures))

    def summarize_cluster(self, members: Deck) -> str:
        payload = {
            "language": prompts.LANGUAGE_NAMES[members.language],
            "questions": [prompts.render_question(q) for q in members.questions],
        }
        data = self._call("summarize_cluster", prompts.SUMMARIZE_SYSTEM, payload, prompts.SUMMARIZE_SCHEMA)
        return parse_string(data, "summary")

    def generate_recall(self, question: Question, language: Language) -> RecallDraft:
        payload = {"language": prompts.LANGUAGE_NAMES[language], "question": prompts.render_question(question)}
        return parse_recall(self._call("generate_recall", prompts.RECALL_SYSTEM, payload, prompts.RECALL_SCHEMA))

    def describe_term(self, term: str, language: Language) -> str:
        payload = {"language": prompts.LANGUAGE_NAMES[language], "term": term}
        data = self._call("describe_term", prompts.DESCRIBE_SYSTEM, payload, prompts.DESCRIBE_SCHEMA)
        return parse_string(data, "description")

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        payload = {"reference": reference, "statement": statement}
        return parse_entailment(
            self._call("check_entailment", prompts.ENTAILMENT_SYSTEM, payload, prompts.ENTAILMENT_SCHEMA)
        )
