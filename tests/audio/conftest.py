"""スタブ TTS と合成トラック(R-7)。本文は自分で書いた合成フィクスチャで、NFR-2 の対象外。

実合成(CosyVoice)はゲートに入れない(ADR-012)。スタブはテキスト1片につき一定長の正弦波を返し、
渡されたテキストとボイスを記録する。
"""

import math
from array import array

from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt
from quiz_sift.ports.tts import PcmAudio
from quiz_sift.script.segments import QuestionScript, Track

DECK = DeckId("aws-saa")
WRONG = OptionId(QuestionId(DECK, "q01"), "C")
"""テキストを直接与えて組む識別ポイントが参照する誤答の ID。"""
RATE = 16000


def tone(seconds: float, rate: int = RATE, amplitude: float = 0.3) -> bytes:
    n = round(seconds * rate)
    return array("h", (round(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n))).tobytes()


def quiet(seconds: float, rate: int = RATE) -> bytes:
    return bytes(2 * round(seconds * rate))


class StubTts:
    def __init__(self, seconds: float = 0.5, fail_on: str | None = None) -> None:
        self.seconds = seconds
        self.fail_on = fail_on
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, voice_id: str) -> PcmAudio:
        self.calls.append((text, voice_id))
        if self.fail_on is not None and self.fail_on in text:
            raise RuntimeError("synthesis failed")
        return PcmAudio(tone(self.seconds), RATE)


def question(local_id: str, prompt: str = "S3 のストレージクラスを思い出してください") -> QuestionScript:
    return QuestionScript(
        QuestionId(DECK, local_id),
        Prompt(prompt),
        Pause(3.3),
        Answer(("S3 標準-IA",)),
        Discrimination("Glacier は取り出しに時間がかかる", (WRONG,)),
    )


def track(cluster: str, part: int = 0, *questions: QuestionScript) -> Track:
    return Track(ClusterId(DECK, cluster), part, questions or (question(f"{cluster}-q{part}"),))
