"""言語とボイスの一致(C-007 / R-7 / AR-23 / A-4)。"""

from pathlib import Path

import pytest

from quiz_sift.audio.synthesize import synthesize_tracks
from quiz_sift.audio.voices import VOICE_TABLE, VoiceLanguageMismatch, read_meta
from quiz_sift.domain.language import Language

from .conftest import StubTts, track


@pytest.mark.parametrize(("language", "voice_id"), [(Language.JA, "日语男"), (Language.EN, "英文女")])
def test_recorded_language_matches_voice(tmp_path: Path, language: Language, voice_id: str) -> None:
    tts = StubTts()
    report = synthesize_tracks(tts, [track("c1")], language=language, voice_id=voice_id, out_dir=tmp_path)

    meta = read_meta(report.succeeded[0].path)
    assert meta.language == language
    assert VOICE_TABLE[meta.voice_id].language == meta.language
    # 記録したボイスが実際に TTS へ渡したボイスである(常に日本語ボイスを使う実装はここで落ちる)。
    assert {voice for _, voice in tts.calls} == {meta.voice_id}


def test_voice_of_another_language_is_refused(tmp_path: Path) -> None:
    tts = StubTts()
    with pytest.raises(VoiceLanguageMismatch):
        synthesize_tracks(tts, [track("c1")], language=Language.EN, voice_id="日语男", out_dir=tmp_path)
    with pytest.raises(VoiceLanguageMismatch):
        synthesize_tracks(tts, [track("c1")], language=Language.JA, voice_id="unknown", out_dir=tmp_path)
    assert tts.calls == []
    assert not list(tmp_path.glob("*"))
