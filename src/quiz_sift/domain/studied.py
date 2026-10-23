"""既習フラグの解決(R-1 / R-11)。

このモジュールが公開するのは `StudiedFlag` と `resolve_studied` の2つだけで、
**`unknown` を `studied` / `unseen` へ変換する関数は置かない**。未指定を既定値で
埋めると、R-8 のレポートが「実際には分からない群」を既習/初見として数えることに
なり、D-015 が分けたかった当のものが混ざる。
"""

from enum import Enum


class StudiedFlag(Enum):
    """既習かどうか。`UNKNOWN` は「指定が無かった」という答えであって、欠損値ではない。"""

    STUDIED = "studied"
    UNSEEN = "unseen"
    UNKNOWN = "unknown"


def resolve_studied(per_question: StudiedFlag | None, deck_default: StudiedFlag | None) -> StudiedFlag:
    """個別指定 > デッキ既定 > `UNKNOWN` の順で解決する。

    `None` は「その水準では指定が無い」を意味する。個別に `UNKNOWN` と**書かれて**
    いる場合はそれが指定なので、デッキ既定へは落ちない。
    """
    if per_question is not None:
        return per_question
    if deck_default is not None:
        return deck_default
    return StudiedFlag.UNKNOWN
