"""LLM のプロンプト・JSON Schema・メソッド別のモデル。SDK に触らない。

どのアダプタも同じ定数を使い、`cli` はこれを `inputs_digest` に入れる(AR-21)。ここを直すと
digest が動き、該当する段のキャッシュが1回無効化される。
"""

from typing import Final

from quiz_sift.adapters.llm_contract import object_schema
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.ports.llm import Entailment

_OPUS = "claude-opus-5-5"
_HAIKU = "claude-haiku-4-5"
MODELS: Final = {
    "classify_clusters": _OPUS,
    "summarize_cluster": _OPUS,
    "generate_recall": _HAIKU,
    "describe_term": _HAIKU,
    "check_entailment": _HAIKU,
}
"""`ports.llm.Llm` のメソッドごとのモデル ID(ADR-015)。"""

CLASSIFY_BATCH_SIZE: Final = 25
"""分類の分割呼び出し1回あたりの問題数。`unassigned` の分だけ応答が増えるので 50 から下げた(AR-62)。"""

LANGUAGE_NAMES: Final = {Language.JA: "日本語", Language.EN: "English"}

CLASSIFY_SYSTEM: Final = (
    "あなたは資格試験の4択問題を、問われている論点ごとにグループ分けする。"
    "各問題はちょうど1つのグループに入れる。どこにも当てはまらない問題は unassigned に入れる。"
    "groups[].question_ids と unassigned を合わせて、すべての問題キーがちょうど1回現れること。"
    "label は論点を短く言い表す。既存のラベルが与えられた場合、同じ論点にはそのラベルを再利用する。"
)
SUMMARIZE_SYSTEM: Final = (
    "あなたは同じ論点に属する4択問題群を読み、その論点を要約する。問題文に無い語句を持ち込まない。"
    "見出し・箇条書き・記号の装飾を使わない平文で、3文以内・200字以内に書く。"
    "専門用語は問題文の表記のまま使う。問題文に無い専門用語や見出し語を作らない。"
)
RECALL_SYSTEM: Final = (
    "あなたは4択問題1問から、耳だけで復習するための想起素材を作る。"
    "terms は答えを思い出す手がかりになる語・状況・条件の組。answer は正解の内容。"
    "discrimination は正解と紛らわしい誤答との切り分け。選択肢のラベル(A/B/C/D など)で言及しない。"
)
DESCRIBE_SYSTEM: Final = "あなたは資格試験に出る専門用語を、耳で聞いて分かる短い説明にする。"
ENTAILMENT_SYSTEM: Final = (
    "あなたは reference を正しい情報として、statement がそれと矛盾するかを判定する。"
    "矛盾しなければ consistent、矛盾すれば contradicted、reference から判断できなければ uncertain。"
)

_STRING = {"type": "string"}
_STRINGS = {"type": "array", "items": _STRING}
CLASSIFY_SCHEMA: Final = object_schema(
    {
        "groups": {"type": "array", "items": object_schema({"label": _STRING, "question_ids": _STRINGS})},
        "unassigned": _STRINGS,
    }
)
SUMMARIZE_SCHEMA: Final = object_schema({"summary": _STRING})
RECALL_SCHEMA: Final = object_schema({"terms": _STRINGS, "answer": _STRING, "discrimination": _STRING})
DESCRIBE_SCHEMA: Final = object_schema({"description": _STRING})
ENTAILMENT_SCHEMA: Final = object_schema({"verdict": {"type": "string", "enum": [e.value for e in Entailment]}})


def render_question(question: Question) -> dict[str, object]:
    correct = next(o.text for o in question.options if o.option_id == question.correct_option_id)
    return {
        "stem": question.stem,
        "options": [o.text for o in question.options],
        "correct": correct,
        "explanation": question.explanation,
    }
