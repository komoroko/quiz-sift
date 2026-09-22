"""台本のセグメント(R-6)。

1問分のセグメント列は `Prompt, Pause, Answer, Discrimination?` で、台本の分割は
この単位でしか行わない(AR-8 / S-3)。`Prompt | Pause | Answer` を最小単位にすると
最後の `Answer` の直後が正当な境界になり、`Pause` を1つも含まないパートが作れてしまう。
"""

from dataclasses import dataclass


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


@dataclass(frozen=True, slots=True, repr=False)
class Answer:
    """答え。"""

    text: str


@dataclass(frozen=True, slots=True, repr=False)
class Discrimination:
    """識別ポイント — 似た選択肢との切り分け。1問につき任意。"""

    text: str


ScriptSegment = Prompt | Pause | Answer | Discrimination
"""台本を構成する1セグメント。"""
