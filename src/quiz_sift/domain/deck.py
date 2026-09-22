"""デッキ — 1セット分の問題のまとまり(R-11)。

クラスタリングをはじめ、複数の問題をまとめて扱う処理は `Deck` を受け取る。
`Deck` は自分の `deck_id` と一致しない問題を持てないので、**別デッキの問題が
混入する経路がコード上に存在しない**。素の `list[Question]` を受け取る関数を
置かないことが、この保証の要になる。
"""

from dataclasses import dataclass

from quiz_sift.domain.ids import DeckId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.domain.studied import StudiedFlag


@dataclass(frozen=True, slots=True, repr=False)
class Deck:
    """1デッキ分の問題集合。"""

    deck_id: DeckId
    language: Language
    default_studied: StudiedFlag | None
    """デッキ単位の既習指定。未指定は `None`(`resolve_studied` の第2引数になる)。"""
    questions: tuple[Question, ...]

    def __post_init__(self) -> None:
        foreign = [q.question_id for q in self.questions if q.question_id.deck_id != self.deck_id]
        if foreign:
            raise ValueError(f"別デッキの問題が混ざっている: {[q.deck_id for q in foreign]}")
        ids = [q.question_id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("問題 ID が重複している")
