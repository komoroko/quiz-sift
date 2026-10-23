"""推定再生時間(R-6 / D-052)。

`estimate_seconds` = Σ(テキストセグメントの長さ ÷ 言語別レート)+ Σ(`Pause` の秒数)。
長さの単位は言語で違い、日本語は空白を除いた文字数、英語は空白区切りの語数。単一レートにすると
同じ文字数でも日本語と英語で再生時間が3倍違う(AR-18)。レートは初回実測後に見直す。
"""

from collections.abc import Sequence

from quiz_sift.domain.language import Language
from quiz_sift.domain.script import Pause, ScriptSegment

RATES = {Language.JA: 7.0, Language.EN: 2.5}
"""1秒あたりの読み上げ量。日本語は文字/秒、英語は語/秒。"""


def _length(text: str, language: Language) -> int:
    if language is Language.EN:
        return len(text.split())
    return sum(not ch.isspace() for ch in text)


def estimate_seconds(segments: Sequence[ScriptSegment], language: Language) -> float:
    """`segments` を `language` で読み上げたときの推定秒数。"""
    total = 0.0
    for segment in segments:
        if isinstance(segment, Pause):
            total += segment.seconds
        else:
            total += _length(segment.text, language) / RATES[language]
    return total
