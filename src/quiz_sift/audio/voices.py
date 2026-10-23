"""ボイスと言語の対応表、および音声の sidecar メタデータ(R-7 / ADR-004)。

対応表はコード内定数にする。プロバイダ API を引くとネットワーク遮断のゲートで判定できない。
ボイス ID は CosyVoice-300M-SFT の組み込み話者名。
"""

import json
from dataclasses import dataclass
from pathlib import Path

from quiz_sift.domain.audio_meta import AudioMeta
from quiz_sift.domain.language import Language


@dataclass(frozen=True, slots=True)
class Voice:
    language: Language


VOICE_TABLE: dict[str, Voice] = {
    "日语男": Voice(Language.JA),
    "英文女": Voice(Language.EN),
    "英文男": Voice(Language.EN),
}


class VoiceLanguageMismatch(ValueError):
    """記録しようとした言語と、使うボイスの言語が一致しない。"""


def meta_for(language: Language, voice_id: str) -> AudioMeta:
    """`voice_id` が `language` のボイスであるときだけ `AudioMeta` を作る。"""
    voice = VOICE_TABLE.get(voice_id)
    if voice is None:
        raise VoiceLanguageMismatch(f"VOICE_TABLE に無いボイス: {voice_id!r}")
    if voice.language != language:
        raise VoiceLanguageMismatch(f"ボイス {voice_id!r} は {voice.language.value} で、{language.value} ではない")
    return AudioMeta(language, voice_id)


def sidecar_path(audio: Path) -> Path:
    return audio.with_suffix(".json")


def write_meta(audio: Path, meta: AudioMeta) -> Path:
    path = sidecar_path(audio)
    path.write_text(json.dumps({"language": meta.language.value, "voice_id": meta.voice_id}), encoding="utf-8")
    return path


def read_meta(audio: Path) -> AudioMeta:
    data = json.loads(sidecar_path(audio).read_text(encoding="utf-8"))
    return AudioMeta(Language(data["language"]), str(data["voice_id"]))
