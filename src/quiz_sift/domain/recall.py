"""想起素材の型(R-4)。"""

from dataclasses import dataclass

from quiz_sift.domain.ids import OptionId


@dataclass(frozen=True, slots=True, repr=False)
class RecallKey:
    """想起キー — 語・状況・条件の**組**(D-005)。単一文字列にはしない。

    重複判定は `term_set` で行う(AR-25)。tuple の等価比較にすると
    `("AWS", "料金", "設定")` と `("料金", "設定", "AWS")` が別物になり、同じ組を
    並べ替えただけのキーが重複禁止をすり抜ける。組に順序の意味は無いが、
    表示用の順序は `terms` のまま保持する。
    """

    terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.terms:
            raise ValueError("想起キーは1語以上の組でなければならない")

    @property
    def term_set(self) -> frozenset[str]:
        """重複判定に使う、順序を落とした表現。"""
        return frozenset(self.terms)


@dataclass(frozen=True, slots=True, repr=False)
class AnswerElement:
    """答えの1要素。どの正解の選択肢を述べたものかを `option_id` で持つ(ADR-022)。"""

    option_id: OptionId
    text: str


@dataclass(frozen=True, slots=True, repr=False)
class DiscriminationPoint:
    """識別ポイント — 正解と紛らわしい誤答との切り分け。参照する誤答選択肢を `distractor_ids` で持つ(D-177)。

    本文中の括弧を参照とみなさない。参照先は ID だけで決まる。
    """

    text: str
    distractor_ids: tuple[OptionId, ...]
