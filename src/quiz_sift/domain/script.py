"""台本のセグメント(R-6)。

1問分のセグメント列は `Prompt, Pause, Answer, Discrimination?` で、台本の分割は
この単位でしか行わない(AR-8 / S-3)。`Prompt | Pause | Answer` を最小単位にすると
最後の `Answer` の直後が正当な境界になり、`Pause` を1つも含まないパートが作れてしまう。
"""

from dataclasses import dataclass

from quiz_sift.domain.ids import OptionId


@dataclass(frozen=True, slots=True, repr=False)
class Prompt:
    """問いかけ。"""

    text: str


@dataclass(frozen=True, slots=True)
class Pause:
    """考える「間」。TTS ではなく自前の無音 PCM として実現する(ADR-004)。"""

    seconds: float

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ValueError("間の長さは正でなければならない")


ANSWER_SEPARATOR = " "
"""N 要素の答えを読み上げの1文にするときの区切り。番号(「1.」「(1)」「1番」)を使わない(R-6)。"""


@dataclass(frozen=True, slots=True, repr=False)
class Answer:
    """答え。正解 N 件に1要素ずつ対応する本文を、選択肢の順で持つ(ADR-022)。"""

    texts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.texts, tuple) or not self.texts or not all(isinstance(t, str) for t in self.texts):
            raise TypeError("答えは1要素以上の文字列の tuple でなければならない")

    @property
    def text(self) -> str:
        """読み上げる本文。要素を `ANSWER_SEPARATOR` で連結する。"""
        return ANSWER_SEPARATOR.join(self.texts)


@dataclass(frozen=True, slots=True, repr=False)
class Discrimination:
    """識別ポイント — 似た選択肢との切り分け。1問につき任意。

    参照する誤答選択肢を想起素材から `distractor_ids` で引き継ぐ。含意判定は想起素材の段で、その誤答の本文を
    「正しくない記述」として済ませてあり、台本段は判定しない(NFR-1、D-248)。
    """

    text: str
    distractor_ids: tuple[OptionId, ...]

    def __post_init__(self) -> None:
        if not self.distractor_ids:
            raise ValueError("識別ポイントは参照する誤答選択肢を1件以上持たなければならない")


ScriptSegment = Prompt | Pause | Answer | Discrimination
"""台本を構成する1セグメント。"""
