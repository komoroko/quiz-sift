"""語句の実在性判定(R-3 / R-4 / ADR-010)。

語句がテキスト集合のどれかに現れるかを判定する。日本語と英語で同じ規則を使う:

- 両辺を NFKC で正規化し、大文字小文字を区別せず、空白の連続を1つに畳んでから部分一致を取る。
  日本語は分かち書きされないので、語境界を要求すると複合語の中の語句を取りこぼす。
- ただし語句の端が英数字なら、その外側が英数字であってはならない。`S3` は `S30` に、`cat` は
  `category` に実在しない。
"""

import re
import unicodedata
from collections.abc import Iterable

_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip().casefold()


def _is_alnum(char: str) -> bool:
    return char.isascii() and char.isalnum()


def _occurs(term: str, text: str) -> bool:
    start = text.find(term)
    while start != -1:
        end = start + len(term)
        before_ok = not (_is_alnum(term[0]) and start > 0 and _is_alnum(text[start - 1]))
        after_ok = not (_is_alnum(term[-1]) and end < len(text) and _is_alnum(text[end]))
        if before_ok and after_ok:
            return True
        start = text.find(term, start + 1)
    return False


def is_present(term: str, texts: Iterable[str]) -> bool:
    """`term` が `texts` のどれかに実在するか。空の語句は何にも実在しない。"""
    needle = _normalize(term)
    if not needle:
        return False
    return any(_occurs(needle, _normalize(text)) for text in texts)
