"""1問分のセグメント列と、トラック(R-6)。

台本は文字列ではなく `Prompt | Pause | Answer | Discrimination` のセグメント列として持つ。
1問分は `Prompt, Pause, Answer, Discrimination?` の順で、`QuestionScript` はこの順を型で持ち、
構築時に各要素の型を検査する。平たいセグメント列は `parse_questions` がこの文法で切り分け、
外れれば `ScriptOrderError`。「間」は `Pause` のインスタンスでしか表せないので、`【間】` のような
地の文をテキストに書いても間にはならず、順序検証で落ちる。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from quiz_sift.domain.ids import ClusterId, QuestionId
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt, ScriptSegment


class ScriptOrderError(ValueError):
    """セグメント列が `Prompt, Pause, Answer, Discrimination?` の繰り返しになっていない。"""


@dataclass(frozen=True, slots=True, repr=False)
class QuestionScript:
    """1問分の台本。分割の最小単位(AR-8 / S-3)。"""

    question_id: QuestionId
    prompt: Prompt
    pause: Pause
    answer: Answer
    discrimination: Discrimination | None

    def __post_init__(self) -> None:
        expected: tuple[tuple[str, object, type | tuple[type, ...]], ...] = (
            ("prompt", self.prompt, Prompt),
            ("pause", self.pause, Pause),
            ("answer", self.answer, Answer),
            ("discrimination", self.discrimination, (Discrimination, type(None))),
        )
        for name, value, kind in expected:
            if not isinstance(value, kind):
                raise TypeError(f"{name} の型が違う: {type(value).__name__}")

    @property
    def segments(self) -> tuple[ScriptSegment, ...]:
        head: tuple[ScriptSegment, ...] = (self.prompt, self.pause, self.answer)
        return head if self.discrimination is None else (*head, self.discrimination)


def parse_questions(segments: Sequence[ScriptSegment]) -> tuple[tuple[ScriptSegment, ...], ...]:
    """平たいセグメント列を1問分ずつに切り分ける。文法に合わなければ `ScriptOrderError`。"""
    units: list[tuple[ScriptSegment, ...]] = []
    i = 0
    while i < len(segments):
        unit = tuple(segments[i : i + 3])
        kinds = tuple(type(s) for s in unit)
        if kinds != (Prompt, Pause, Answer):
            names = ", ".join(k.__name__ for k in kinds)
            raise ScriptOrderError(f"位置 {i} の1問分が Prompt, Pause, Answer で始まらない: {names}")
        i += 3
        if i < len(segments) and isinstance(segments[i], Discrimination):
            unit = (*unit, segments[i])
            i += 1
        units.append(unit)
    if not units:
        raise ScriptOrderError("セグメント列が空")
    return tuple(units)


@dataclass(frozen=True, slots=True, repr=False)
class Track:
    """音声1ファイル分の台本。3分を超えたクラスタは複数トラックになる(ADR-008)。

    `part` はクラスタ内の 0 始まりの通し番号。ファイル名のパート記号は音声側が決める。
    """

    cluster_id: ClusterId
    part: int
    questions: tuple[QuestionScript, ...]

    def __post_init__(self) -> None:
        if not self.questions:
            raise ValueError("問題を持たないトラックは作れない")

    @property
    def segments(self) -> tuple[ScriptSegment, ...]:
        return tuple(s for q in self.questions for s in q.segments)
