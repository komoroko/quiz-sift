"""識別子。すべて `(deck_id, local_id)` の複合キーにする(R-11)。

素の `str` を ID に使うと、別デッキの同じ通し番号どうしが `==` で等しくなり、
クラスタや学習記録に別デッキの問題が混入する経路がそのまま開く。ここで
`deck_id` を型の中へ畳み込んでおくと、その経路がコード上に存在しなくなる。
`OptionId` は `QuestionId` を内包することで同じ保証を受け取る。
"""

from dataclasses import dataclass
from typing import NewType

DeckId = NewType("DeckId", str)
"""デッキ名前空間の名前。`data/<deck_id>/` と `out/<deck_id>/` の名前でもある。

**不変**として扱う(D-059)。変更はディレクトリと学習記録の移行を伴う。
"""


@dataclass(frozen=True, slots=True, order=True)
class QuestionId:
    """1デッキ内で一意な問題 ID。"""

    deck_id: DeckId
    local_id: str


@dataclass(frozen=True, slots=True, order=True)
class ClusterId:
    """1デッキ内で一意なクラスタ ID。"""

    deck_id: DeckId
    local_id: str


@dataclass(frozen=True, slots=True, order=True)
class OptionId:
    """1問内で一意な選択肢 ID。

    採点は `correct_option_ids` との比較で行う(R-8)。`local_id` だけの比較だと
    別の問題の「A」どうしが等しくなってしまうため、所属する問題を内包する。
    """

    question_id: QuestionId
    local_id: str

    @property
    def deck_id(self) -> DeckId:
        return self.question_id.deck_id


@dataclass(frozen=True, slots=True, order=True)
class SessionId:
    """出題セッション ID。学習記録はデッキ単位で分離する(R-11)ので deck_id を持つ。"""

    deck_id: DeckId
    local_id: str
