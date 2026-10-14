"""LLM のプロンプト・JSON Schema・メソッド別のモデル。SDK に触らない。

どのアダプタも同じ定数を使い、`cli` はこれを `inputs_digest` に入れる(AR-21)。ここを直すと
digest が動き、該当する段のキャッシュが1回無効化される。
"""

from collections.abc import Mapping
from typing import Final

from quiz_sift.adapters.llm_contract import object_schema
from quiz_sift.domain.ids import OptionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.grounding.targets import RECALL_KEY_FIELDS, QuestionField
from quiz_sift.ports.llm import Entailment, Problem, RecallDraft

_OPUS = "claude-opus-5-5"
_SONNET = "claude-sonnet-5-5"
_HAIKU = "claude-haiku-4-5"
MODELS: Final = {
    "classify_clusters": _OPUS,
    "summarize_cluster": _OPUS,
    "generate_recall": _HAIKU,
    "describe_term": _OPUS,
    "check_entailments": _SONNET,
}
"""`ports.llm.Llm` のメソッドごとのモデル ID(ADR-027)。"""

CHILD_ENV: Final[dict[str, dict[str, str]]] = {
    "classify_clusters": {},
    "summarize_cluster": {},
    "generate_recall": {"MAX_THINKING_TOKENS": "0"},
    "describe_term": {},
    "check_entailments": {},
}
"""メソッドごとに子プロセスの環境へ足す変数(D-243、ADR-029)。思考は argv でなく環境で止め、argv のダイジェストを
動かさない。"""


def model_env(model: str) -> dict[str, str]:
    """そのモデルを使うメソッドの追加の環境。表に無いモデルは追加なし。

    `SubprocessRunner` はメソッドを知らないので、argv の `--model` からこれを引いて環境を検査する(NFR-8 (b2))。
    同じモデルを使うメソッドどうしで表が違えば、モデルからは決まらないので落とす。
    """
    envs = {tuple(sorted(CHILD_ENV[method].items())) for method, used in MODELS.items() if used == model}
    if len(envs) > 1:
        raise ValueError(f"{model} を使うメソッドどうしで追加の環境が違う: {sorted(envs)}")
    return dict(envs.pop()) if envs else {}


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
    "retry があれば作り直しの依頼で、retry.previous は前回の要約、retry.problems はこれまでに落ちた検査の一覧。"
    "各 problem の問題を直して書き直し、同じ問題を繰り返さない。検査の名前(check)の意味: "
    "ungrounded_terms は terms の語句が問題文に無かった(その語句を使わない)。"
    "entailment は statements の文が正解・解説と矛盾した(contradicted)か判断できなかった(uncertain)。"
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
        "retry があれば作り直しの依頼で、retry.previous は前回の出力、retry.problems はこれまでに落ちた検査の一覧。"
        "各 problem の問題を直して書き直し、同じ問題を繰り返さない。選択肢は question と同じキーで示す。"
        "検査の名前(check)の意味: "
        "empty は elements の要素が空だった。"
        f"ungrounded_key は terms の語句が {sources} に無かった。"
        "answer_mismatch は answers の option が question.correct と重複なく一致しなかった(options は食い違ったキー)。"
        "missing_options は discrimination.distractors が誤答選択肢を1件も参照しないか、"
        "誤答選択肢でないキー(options)を参照した。"
        "generic は terms のすべてが他の論点の問題にも現れ、答えの手がかりにならなかった。"
        "verbatim は discrimination.text が誤答選択肢の丸写しだった。"
        "duplicate_key は terms の組が別の論点の問題の想起素材と同じだった。"
        "entailment は statements の文が正解・解説と矛盾した(contradicted)か判断できなかった(uncertain)。"
    )


RECALL_SYSTEM: Final = render_recall_system(RECALL_KEY_FIELDS)
DESCRIBE_SYSTEM: Final = "あなたは資格試験に出る専門用語を、耳で聞いて分かる短い説明にする。"
ENTAILMENT_SYSTEM: Final = (
    "あなたは reference を正しい情報として、statement がそれと矛盾するかを判定する。"
    "statements の各要素の statement を1つずつ判定し、その要素の id で判定を返す。"
    "要素に focus があれば、その statement は focus の正解を説明している。"
    "要素に distractors があれば、それはこの問題の答えとして正しくない記述(誤答選択肢)の一覧である。"
    "statement が distractors を正しくないと述べることは矛盾ではない。"
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
ENTAILMENT_SCHEMA: Final = object_schema(
    {
        "verdicts": {
            "type": "array",
            "items": object_schema(
                {"id": _STRING, "verdict": {"type": "string", "enum": [e.value for e in Entailment]}}
            ),
        }
    }
)


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


def problem_json(problem: Problem, keys: Mapping[OptionId, str]) -> dict[str, object]:
    """作り直しに渡す検査1件。選択肢は `keys` の不透明キーにし、元のラベルを渡さない(D-206)。空の欄は載せない。"""
    data: dict[str, object] = {"check": problem.check}
    if problem.terms:
        data["terms"] = list(problem.terms)
    if problem.options:
        data["options"] = [keys[option_id] for option_id in problem.options]
    if problem.elements:
        data["elements"] = list(problem.elements)
    if problem.statements:
        data["statements"] = [{"statement": text, "verdict": verdict.value} for text, verdict in problem.statements]
    return data


def recall_draft_json(draft: RecallDraft, keys: Mapping[OptionId, str]) -> dict[str, object]:
    """作り直しに渡す前回の想起素材。`RECALL_SCHEMA` の応答と同じ形で、選択肢は `keys` の不透明キー(D-206)。"""
    return {
        "terms": list(draft.key.terms),
        "answers": [{"option": keys[e.option_id], "text": e.text} for e in draft.answers],
        "discrimination": {
            "text": draft.discrimination.text,
            "distractors": [keys[option_id] for option_id in draft.discrimination.distractor_ids],
        },
    }
