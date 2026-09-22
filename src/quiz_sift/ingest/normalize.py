"""重複検出のための文面の正規化(R-1)。

NFKC で全角半角や合成文字の揺れを畳み、大文字小文字を畳み、記号と空白を落とす。空白は1つに
畳むのではなく**落とす** — 日本語は語の間に空白を置かないので、「企業は、オンプレミス」と
「企業は　オンプレミス」と「Amazon S3を使う」「Amazon S3 を使う」を同じ鍵にするには、
区切りの有無そのものを消すしかない。

保持する問題文そのものは書き換えない。これは比較用の鍵を作るだけで、教材の文面を変えると
下流の実在性判定(grounding)が原文と食い違う。
"""

import unicodedata

_DROPPED_CATEGORIES = ("P", "S", "Z")
"""落とす Unicode 一般カテゴリ。P = 句読点・括弧、S = 記号、Z = 空白類。"""


def normalize_text(text: str) -> str:
    """比較用に正規化した文面を返す。"""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if not ch.isspace() and unicodedata.category(ch)[0] not in _DROPPED_CATEGORIES)
