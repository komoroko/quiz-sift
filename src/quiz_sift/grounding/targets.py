"""G-1 の照合先(R-4 / ADR-022)。どの検査も、照合先の本文をこの定数と `texts_for` からしか作らない。

生成プロンプト(`adapters/llm_prompts.render_recall_system`)も同じ定数から組み立てる。照合先と
プロンプトが別々に定義されていると、片方だけが動いて検査が全件落ちる(DEF-1)。

- 想起キーは問題文だけ(`RECALL_KEY_FIELDS`)。選択肢や解説にだけある語は手がかりにならない。
- 他クラスタ出現率とクラスタ要約は、問題文・選択肢・解説のすべて(D-198)。
"""

from typing import Final, Literal

from quiz_sift.domain.question import Question

type QuestionField = Literal["stem", "options", "explanation"]
"""問題の本文のうち照合先にできる部分。"""

RECALL_KEY_FIELDS: Final[tuple[QuestionField, ...]] = ("stem",)
"""想起キーの照合先。"""
OTHER_CLUSTER_FIELDS: Final[tuple[QuestionField, ...]] = ("stem", "options", "explanation")
"""他クラスタ出現率の照合先(D-198)。"""
SUMMARY_FIELDS: Final[tuple[QuestionField, ...]] = ("stem", "options", "explanation")
"""クラスタ要約の照合先(D-198)。"""


def texts_for(question: Question, fields: tuple[QuestionField, ...]) -> list[str]:
    """`question` の本文のうち `fields` の部分を、`fields` の順に返す。解説の無い問題は解説を返さない。"""
    texts: list[str] = []
    for field in fields:
        match field:
            case "stem":
                texts.append(question.stem)
            case "options":
                texts.extend(option.text for option in question.options)
            case "explanation":
                if question.explanation is not None:
                    texts.append(question.explanation)
    return texts
