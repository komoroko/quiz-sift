"""グロッサリ音声の別系列出力(R-5 / D-049)。

想起素材(`out/<deck>/audio/`)とは別のディレクトリ `out/<deck>/glossary/` に、`g001_` から始まる
別の連番で書き、専用の m3u をもう1本出す。想起素材の再生リストには混ぜない。台本に解説が混ざるのと
同じ交絡を避けるため(D-049)。

命名と再生リストは `audio.naming` / `audio.playlist` をそのまま呼ぶ。`g` は `track_path` が返した
名前の先頭に付けるだけで、連番の桁数と識別子の検査は `track_path` に任せる。
"""

from dataclasses import dataclass
from pathlib import Path

from quiz_sift.audio.codec import encode_mp3
from quiz_sift.audio.naming import track_path
from quiz_sift.audio.playlist import PlaylistEntry, write_playlist
from quiz_sift.audio.synthesize import silence
from quiz_sift.audio.voices import meta_for, write_meta
from quiz_sift.decks.registry import deck_out_dir
from quiz_sift.domain.ids import DeckId
from quiz_sift.domain.language import Language
from quiz_sift.glossary.describe import GlossaryEntry
from quiz_sift.glossary.verify import Glossary
from quiz_sift.ports.tts import PcmAudio, Tts

GLOSSARY_DIRNAME = "glossary"
PLAYLIST_NAME = "glossary"
SERIES_PREFIX = "g"
TERM_GAP_SECONDS = 1.0
"""用語と説明のあいだに挟む無音。"""


@dataclass(frozen=True, slots=True)
class GlossaryAudio:
    tracks: tuple[PlaylistEntry, ...]
    playlist: Path


def glossary_dir(out_root: Path, deck_id: DeckId) -> Path:
    """`out/<deck>/glossary/`。"""
    return deck_out_dir(out_root, deck_id) / GLOSSARY_DIRNAME


def glossary_track_path(out_dir: Path, seq: int, deck_id: DeckId) -> Path:
    """`out_dir/g<ゼロ埋め連番>_<deck>_glossary.mp3`。"""
    path = track_path(out_dir, seq, f"{deck_id}_{GLOSSARY_DIRNAME}")
    return path.with_name(SERIES_PREFIX + path.name)


def _render(tts: Tts, entry: GlossaryEntry, voice_id: str) -> PcmAudio:
    term = tts.synthesize(entry.term, voice_id)
    description = tts.synthesize(entry.description, voice_id)
    if description.sample_rate != term.sample_rate:
        raise ValueError(f"TTS のサンプルレートが途中で変わった: {term.sample_rate} → {description.sample_rate}")
    gap = silence(TERM_GAP_SECONDS, term.sample_rate)
    return PcmAudio(term.samples + gap + description.samples, term.sample_rate)


def write_glossary_audio(
    tts: Tts,
    glossary: Glossary,
    *,
    deck_id: DeckId,
    language: Language,
    voice_id: str,
    out_root: Path,
) -> GlossaryAudio:
    """用語1件を1トラックとして `out/<deck>/glossary/` に書き、`glossary.m3u` を出す。"""
    if not glossary.entries:
        raise ValueError("グロッサリが空")
    meta = meta_for(language, voice_id)
    out_dir = glossary_dir(out_root, deck_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    tracks: list[PlaylistEntry] = []
    for seq, entry in enumerate(glossary.entries, start=1):
        path = glossary_track_path(out_dir, seq, deck_id)
        audio = _render(tts, entry, voice_id)
        encode_mp3(audio, path)
        write_meta(path, meta)
        tracks.append(PlaylistEntry(path, len(audio.samples) / 2 / audio.sample_rate))
    return GlossaryAudio(tuple(tracks), write_playlist(out_dir, PLAYLIST_NAME, tracks))
