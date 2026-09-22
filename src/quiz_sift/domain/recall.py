"""想起素材の型(R-4)。"""

from dataclasses import dataclass


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
