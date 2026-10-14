"""LLM のプロンプト・JSON Schema・メソッド別のモデル。SDK に触らない。

どのアダプタも同じ定数を使い、`cli` はこれを `inputs_digest` に入れる(AR-21)。ここを直すと
digest が動き、該当する段のキャッシュが1回無効化される。
"""

from typing import Final

from quiz_sift.adapters.llm_contract import object_schema
from quiz_sift.domain.ids import OptionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.grounding.targets import RECALL_KEY_FIELDS, QuestionField
from quiz_sift.ports.llm import Entailment

_OPUS = "claude-opus-5-5"
_SONNET = "claude-sonnet-5-5"
_HAIKU = "claude-haiku-4-5"
MODELS: Final = {
    "classify_clusters": _OPUS,
    "summarize_cluster": _OPUS,
    "generate_recall": _HAIKU,
    "describe_term": _OPUS,
    "check_entailment": _SONNET,
}
"""`ports.llm.Llm` のメソッドごとのモデル ID(ADR-027)。"""

CLASSIFY_BATCH_SIZE: Final = 25
"""分類の分割呼び出し1回あたりの問題数。`unassigned` の分だけ応答が増えるので 50 から下げた(AR-62)。"""

LANGUAGE_NAMES: Final = {Language.JA: "日本語", Language.EN: "English"}

CLASSIFY_SYSTEM: Final = (
    "あなたは資格試験の多肢選択問題を、問われている論点ごとにグループ分けする。"
    "各問題はちょうど1つのグループに入れる。どこにも当てはまらない問題は unassigned に入れる。"
    "groups[].question_ids と unassigned を合わせて、すべての問題キーがちょうど1回現れること。"
    "label は論点を短く言い表す。既存のラベルが与えられた場合、同じ論点にはそのラベルを再利用する。"
    "各問題は問題文(stem)・選択肢(options)・正解(correct)で与えられる。"
)
SUMMARIZE_SYSTEM: Final = (
    "あなたは同じ論点に属する多肢選択問題群を読み、その論点を要約する。問題文に無い語句を持ち込まない。"
    "見出し・箇条書き・記号の装飾を使わない平文で、3文以内・200字以内に書く。"
    "専門用語は問題文の表記のまま使う。問題文に無い専門用語や見出し語を作らない。"
)
_FIELD_PATHS: Final[dict[QuestionField, str]] = {
    "stem": "question.stem",
    "options": "question.options[].text",
    "explanation": "question.explanation",
}
"""照合先の各部分が、想起素材の payload(`recall_payload`)のどこにあるか。"""


def render_recall_system(fields: tuple[QuestionField, ...]) -> str:
    """想起素材の生成プロンプト。terms を写す元は、検査の照合先と同じ `fields` から組み立てる(ADR-022)。"""
    sources = " または ".join(_FIELD_PATHS[field] for field in fields)
    return (
        "あなたは多肢選択問題1問から、耳だけで復習するための想起素材を作る。"
        "terms は答えを思い出す手がかりになる語・状況・条件の組。"
        f"terms の各要素は {sources} から連続した部分をそのまま写す。言い換え・要約・表記の変更をしない。"
        "answers は question.correct の各キーにちょうど1要素ずつ対応させ、"
        "option にそのキー、text にその正解の内容を書く。"
        "discrimination.text は正解と紛らわしい誤答との切り分け。"
        "discrimination.distractors には、切り分けた誤答選択肢のキー(question.correct に無いキー)を1件以上書く。"
        "本文では選択肢のラベル(A/B/C/D など)やキーで言及しない。"
    )


RECALL_SYSTEM: Final = render_recall_system(RECALL_KEY_FIELDS)
DESCRIBE_SYSTEM: Final = "あなたは資格試験に出る専門用語を、耳で聞いて分かる短い説明にする。"
ENTAILMENT_SYSTEM: Final = (
    "あなたは reference を正しい情報として、statement がそれと矛盾するかを判定する。"
    "reference に「誤答選択肢(この問題では正しくない記述)」の節があれば、"
    "それはこの問題の答えとして正しくない記述の一覧である。"
    "statement が誤答選択肢を正しくないと述べることは矛盾ではない。"
    "reference に「この文が説明する正解」の節があれば、statement はその正解を説明している。"
    "矛盾しなければ consistent、矛盾すれば contradicted、reference から判断できなければ uncertain。"
    "statement は reference の複数の問題をまとめた要約でありうる。"
    "reference の一部を省略していること、一般化していること、"
    "reference に書かれていない事柄に触れていないことは矛盾ではない。"
    "statement の主張が reference の正解・解説と両立しないときだけ contradicted とする。"
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
RECALL_SCHEMA: Final = object_schema(
    {
        "terms": _STRINGS,
        "answers": {"type": "array", "items": object_schema({"option": _STRING, "text": _STRING})},
        "discrimination": object_schema({"text": _STRING, "distractors": _STRINGS}),
    }
)
DESCRIBE_SCHEMA: Final = object_schema({"description": _STRING})
ENTAILMENT_SCHEMA: Final = object_schema({"verdict": {"type": "string", "enum": [e.value for e in Entailment]}})


def classify_question(question: Question) -> dict[str, object]:
    """分類に渡す問題。論点は問題文・選択肢・正解で決まるので、解説は渡さない(ADR-029)。"""
    correct = "、".join(o.text for o in question.options if o.option_id in question.correct_option_ids)
    return {"stem": question.stem, "options": [o.text for o in question.options], "correct": correct}


def render_question(question: Question) -> dict[str, object]:
    """要約に渡す問題。分類の形に解説を足す。"""
    return {**classify_question(question), "explanation": question.explanation}


def recall_payload(question: Question) -> tuple[dict[str, object], dict[str, OptionId]]:
    """想起素材の生成に渡す問題と、応答のキーを選択肢 ID へ写し戻す対応(D-206)。

    選択肢は通し番号の不透明なキーで渡す。元のラベル(A/B…)を渡すと、本文でラベルに言及させる経路が開く。
    """
    keyed = {f"o{i}": option.option_id for i, option in enumerate(question.options, start=1)}
    keys = {option_id: key for key, option_id in keyed.items()}
    payload: dict[str, object] = {
        "stem": question.stem,
        "options": [{"key": keys[o.option_id], "text": o.text} for o in question.options],
        "correct": [keys[option_id] for option_id in question.correct_option_ids],
        "explanation": question.explanation,
    }
    return payload, keyed
