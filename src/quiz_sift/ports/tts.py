"""音声合成(R-7 / ADR-004)。

「間」は TTS に頼らず自前の無音 PCM で挟むので(ADR-004)、この Protocol はテキスト1片を
PCM にするだけでよい。無音の挿入と mp3 の書き出しは `audio/` が行う。
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True, repr=False)
class PcmAudio:
    """符号付き 16bit リトルエンディアン・モノラルの PCM。"""

    samples: bytes
    sample_rate: int

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("サンプルレートは正でなければならない")
        if len(self.samples) % 2:
            raise ValueError("16bit PCM のバイト列が奇数長になっている")


class Tts(Protocol):
    def synthesize(self, text: str, voice_id: str) -> PcmAudio:
        """`text` を `voice_id` の声で読み上げた PCM を返す。"""
        ...
