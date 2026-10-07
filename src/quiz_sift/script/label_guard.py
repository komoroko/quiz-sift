"""選択肢ラベルの列挙判定(R-6 / D-006)。純粋関数で、LLM を呼ばない。

**判定単位は1トラック分のセグメント列を連結したもの**(AR-13)。セグメント内に閉じると、
Prompt「アの場合とイの場合」/ Answer「ウが正解」/ Discrimination「エは別」のように
セグメントを跨いで4件が並ぶ形を素通しする。耳で聞けばこれは4択のラベル列挙そのもの。

ラベルの系列は記号(A〜F)・番号(1〜6、全角と丸数字を含む)・かな(ア〜カ)の3つで、1つの系列の
**先頭から M 件**がすべて並ぶ形を列挙とする(D-201)。M は選択肢の数で、判定はトラック単位なので、
そのトラックに含まれる問題の M の最小値を渡す(D-211)。それより少ないラベルの言及は許す。

- **日本語**: 語の一部でない単独ラベル(「アクセス」のア、「S3」の数字は数えない)を系列ごとに集め、
  先頭から M 件すべてがトラック内に出現すれば列挙。番号は「1番」「①」「(1)」「1.」の形だけをラベルとみなす。
- **英語**: 冠詞の "a" や "Availability Zone A" が正当に現れるので、単独トークンの有無では判定せず
  **列挙構造**を見る。(1) 区切り(`,` `;` `/` and or、"option" 等)だけを挟んで M 件が連なる形、
  (2) `A)` `(A)` `A:` `A.` のようなリスト記号として M 件すべてが現れる形。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from quiz_sift.domain.language import Language
from quiz_sift.domain.question import OPTION_COUNTS
from quiz_sift.domain.script import Pause, ScriptSegment

_SERIES = {
    "letter": "ABCDEF",
    "number": "123456",
    "kana": "アイウエオカ",
}

_FULLWIDTH = str.maketrans("ＡＢＣＤＥＦ１２３４５６①②③④⑤⑥", "ABCDEF123456123456")

# 日本語: 系列ごとの単独ラベル。
_JA_LABELS = {
    "letter": re.compile(r"(?<![A-Za-zＡ-Ｚａ-ｚ0-9０-９_\-])([A-FＡ-Ｆ])(?![A-Za-zＡ-Ｚａ-ｚ0-9０-９_\-])"),
    "number": re.compile(
        r"([①-⑥])"
        r"|(?<![0-9０-９.．])([1-6１-６])(?=番)"
        r"|(?<=[(（])([1-6１-６])(?=[)）])"
        r"|(?<![0-9０-９.．])([1-6１-６])(?=[.．:：)）](?![0-9０-９]))"
    ),
    "kana": re.compile(r"(?<![ァ-ヺー])([アイウエオカ])(?![ァ-ヺー])"),
}

# 英語: 語の一部でない大文字1字と数字1字。小文字の冠詞 a は最初から候補にならない。
_EN_LABELS = {
    "letter": re.compile(r"(?<![\w'’\-])([A-F])(?![\w'’\-])"),
    "number": re.compile(r"(?<![\w'’.,\-])([1-6])(?![\w'’%\-]|[.,]\d)"),
}
_EN_GAP = re.compile(r"(?:[\s,;/&()\[\]]|\band\b|\bor\b|\boptions?\b|\bchoices?\b|\banswers?\b)*", re.IGNORECASE)
_EN_MARKER_AFTER = re.compile(r"[):.]")
_EN_MARKER_BEFORE = re.compile(r"\($")


@dataclass(frozen=True, slots=True)
class Enumeration:
    """検出した列挙1件。教材本文は持たない(NFR-2)。"""

    series: str
    """`letter` / `number` / `kana`。"""
    form: str
    """`present`(日本語: 先頭から M 件がトラック内に揃う)/ `chain`(英語: 区切りで連なる)/
    `markers`(英語: リスト記号)。"""


def track_text(segments: Sequence[ScriptSegment]) -> str:
    """1トラック分のテキストセグメントを連結する。`Pause` は区切りの改行になる。"""
    return "\n".join("" if isinstance(s, Pause) else s.text for s in segments)


def _labels(pattern: re.Pattern[str], text: str) -> list[tuple[int, int, str]]:
    found = []
    for m in pattern.finditer(text):
        group = next(i for i in range(1, len(m.groups()) + 1) if m.group(i) is not None)
        found.append((m.start(group), m.end(group), m.group(group).translate(_FULLWIDTH)))
    return found


def _ja(text: str, m: int) -> list[Enumeration]:
    return [
        Enumeration(series, "present")
        for series, pattern in _JA_LABELS.items()
        if set(_SERIES[series][:m]) <= {label for _, _, label in _labels(pattern, text)}
    ]


def _has_chain(labels: Sequence[tuple[int, int, str]], text: str, full: set[str]) -> bool:
    run: list[str] = []
    prev_end = -1
    for start, end, label in labels:
        if run and _EN_GAP.fullmatch(text, prev_end, start) and label not in run:
            run.append(label)
        else:
            run = [label]
        prev_end = end
        if full <= set(run):
            return True
    return False


def _is_marker(start: int, end: int, text: str) -> bool:
    return bool(_EN_MARKER_AFTER.match(text, end)) or bool(_EN_MARKER_BEFORE.search(text, 0, start))


def _en(text: str, m: int) -> list[Enumeration]:
    found = []
    for series, pattern in _EN_LABELS.items():
        labels = _labels(pattern, text)
        full = set(_SERIES[series][:m])
        if _has_chain(labels, text, full):
            found.append(Enumeration(series, "chain"))
        elif full <= {label for start, end, label in labels if _is_marker(start, end, text)}:
            found.append(Enumeration(series, "markers"))
    return found


def find_enumerations(
    segments: Sequence[ScriptSegment], language: Language, option_count: int
) -> tuple[Enumeration, ...]:
    """1トラック分のセグメント列に含まれるラベル列挙。空なら列挙は無い。

    `option_count` は M(トラック内の問題の選択肢数の最小値)。系列の先頭から M 件が揃う形を列挙とする。
    """
    if option_count not in OPTION_COUNTS:
        raise ValueError(f"選択肢の数 M は {sorted(OPTION_COUNTS)} のいずれか: {option_count}")
    text = track_text(segments)
    return tuple(_en(text, option_count) if language is Language.EN else _ja(text, option_count))
