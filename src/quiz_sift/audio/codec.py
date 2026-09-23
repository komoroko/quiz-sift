"""mp3 のエンコードとデコード(R-7 / AR-27)。どちらも ffmpeg で行う。

`wave` は WAV しか書けない。エンコードは CBR にし、先頭のエンコーダ遅延 / パディングが
無音判定に与える影響を固定する。入出力はどちらも符号付き 16bit リトルエンディアン・モノラル。
"""

import subprocess
from pathlib import Path

from quiz_sift.ports.tts import PcmAudio

BITRATE = "128k"


def encode_mp3(audio: PcmAudio, path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "s16le",
            "-ar",
            str(audio.sample_rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            BITRATE,
            str(path),
        ],
        input=audio.samples,
        check=True,
        capture_output=True,
    )


def decode_mp3(path: Path, sample_rate: int) -> PcmAudio:
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    return PcmAudio(result.stdout, sample_rate)
