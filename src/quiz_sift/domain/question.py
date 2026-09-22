"""問題と選択肢(R-1)。

本文を持つ dataclass は `repr=False` を指定する(AR-17)。素の例外トレースバックには
`safety/log_filter.py` が効かないので、repr に教材本文が載ると NFR-2 の
「教材本文がログ出力のいずれにも含まれない」が破れる。
"""

from dataclasses import dataclass

from quiz_sift.domain.ids import OptionId, QuestionId
from quiz_sift.domain.studied import StudiedFlag

OPTION_COUNT = 4
"""1問が持つ選択肢の数。4択問題集だけを扱う(R-1)。"""


@dataclass(frozen=True, slots=True, repr=False)
class Option:
    """選択肢1件。"""

    option_id: OptionId
    text: str


@dataclass(frozen=True, slots=True, repr=False)
class Question:
    """4択問題1件。

    不変条件は `__post_init__` で強制する。「テストで確かめる性質」ではなく
    「壊すと構築できない構造」にしておくと、下流が壊れた問題を組み立てられない。
    """

    question_id: QuestionId
    stem: str
    options: tuple[Option, ...]
    correct_option_id: OptionId
    explanation: str | None
    """公式解説。無い問題があるので `None` を許す。件数は `IngestReport` が報告する(AR-10)。"""
    studied: StudiedFlag

    def __post_init__(self) -> None:
        if len(self.options) != OPTION_COUNT:
            raise ValueError(f"選択肢は {OPTION_COUNT} 件でなければならない: {len(self.options)} 件")
        ids = [option.option_id for option in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("選択肢 ID が重複している")
        foreign = [option_id for option_id in ids if option_id.question_id != self.question_id]
        if foreign:
            raise ValueError("他の問題に属する選択肢が混ざっている")
        if self.correct_option_id not in ids:
            raise ValueError("correct_option_id がこの問題の選択肢に無い")
