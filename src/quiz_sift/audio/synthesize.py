"""音声合成ステージ(R-7 / ADR-004)。

トラックごとにセグメントを読み上げ、`Pause` は TTS に頼らず自前の無音 PCM として挟み、連結して
mp3 に書き出す。書き出した mp3 はデコードし直して無音構造を判定し、通らなければそのクラスタは失敗に
数える。エンコードで無音が縮むことはこの経路で捕まる(3.000 秒が 2.992 秒になった実測 — D-053)。

日英が混在するテキストは分割せずそのまま TTS へ渡す(D-060)。ボイスはトラックにつき1つで、言語との
一致は書き出す前に `voices.meta_for` で確かめる。

**音声化に失敗したクラスタは `SynthesisReport.failed` に列挙し、`SynthesisFailed` で非0終了させる**
(AR-10)。失敗したクラスタのファイルは残さず、再生リストは全クラスタが揃ったときだけ書く。下流
(R-10 の再生リスト)が欠けたトラックを参照しないようにするため。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

from quiz_sift.audio.codec import decode_mp3, encode_mp3
from quiz_sift.audio.naming import part_suffix, track_path
from quiz_sift.audio.playlist import PlaylistEntry, write_playlist
from quiz_sift.audio.silence import detect_structure, has_pause_structure
from quiz_sift.audio.voices import meta_for, sidecar_path, write_meta
from quiz_sift.domain.audio_meta import AudioMeta
from quiz_sift.domain.ids import ClusterId
from quiz_sift.domain.language import Language
from quiz_sift.domain.script import Pause
from quiz_sift.ports.tts import PcmAudio, Tts
from quiz_sift.script.segments import Track

logger = logging.getLogger(__name__)

PLAYLIST_NAME = "playlist"


class MissingPauseStructure(ValueError):
    """書き出した mp3 に、前後を有音に挟まれた 3.0 秒以上の無音が無い。"""


@dataclass(frozen=True, slots=True)
class SynthesizedTrack:
    cluster_id: ClusterId
    path: Path
    seconds: float


@dataclass(frozen=True, slots=True)
class SynthesisFailure:
    """音声化できなかった1クラスタ。

    `reason` は例外の型とメッセージ。TTS の例外は読み上げたテキストを含み得るので、ログにも終了
    メッセージにも載せない(NFR-2)。
    """

    cluster_id: ClusterId
    reason: str


@dataclass(frozen=True, slots=True)
class SynthesisReport:
    succeeded: tuple[SynthesizedTrack, ...]
    failed: tuple[SynthesisFailure, ...]
    playlist: Path | None
    """全クラスタが揃ったときだけ書く。"""

    @property
    def cluster_seconds(self) -> dict[ClusterId, float]:
        """クラスタごとの再生時間。割ったクラスタはパートの合計。"""
        seconds: dict[ClusterId, float] = {}
        for track in self.succeeded:
            seconds[track.cluster_id] = seconds.get(track.cluster_id, 0.0) + track.seconds
        return seconds

    @property
    def total_seconds(self) -> float:
        return sum(t.seconds for t in self.succeeded)


class SynthesisFailed(SystemExit):
    """音声化に失敗したクラスタがある。捕まえずに上げれば非0で終了する。"""

    def __init__(self, report: SynthesisReport) -> None:
        super().__init__(f"音声化に失敗したクラスタ: {[f.cluster_id.local_id for f in report.failed]}")
        self.report = report


def silence(seconds: float, sample_rate: int) -> bytes:
    """`seconds` 秒分の無音 PCM(16bit モノラル)。"""
    return bytes(2 * round(seconds * sample_rate))


def render_track(tts: Tts, track: Track, voice_id: str) -> PcmAudio:
    """トラックのセグメントを順に PCM にして連結する。`Pause` は無音 PCM、ほかは TTS。

    無音はそれまでの音声のサンプルレートで作るので、TTS の出力はすべて同じレートでなければならない。
    """
    pieces: list[bytes] = []
    sample_rate: int | None = None
    pending_silence: list[float] = []
    for segment in track.segments:
        if isinstance(segment, Pause):
            pending_silence.append(segment.seconds)
            continue
        audio = tts.synthesize(segment.text, voice_id)
        if sample_rate is None:
            sample_rate = audio.sample_rate
        elif audio.sample_rate != sample_rate:
            raise ValueError(f"TTS のサンプルレートが途中で変わった: {sample_rate} → {audio.sample_rate}")
        pieces.extend(silence(s, sample_rate) for s in pending_silence)
        pending_silence.clear()
        pieces.append(audio.samples)
    if sample_rate is None:
        raise ValueError("読み上げるセグメントが無い")
    pieces.extend(silence(s, sample_rate) for s in pending_silence)
    return PcmAudio(b"".join(pieces), sample_rate)


def _write_track(audio: PcmAudio, path: Path, meta: AudioMeta) -> float:
    encode_mp3(audio, path)
    write_meta(path, meta)
    decoded = decode_mp3(path, audio.sample_rate)
    if not has_pause_structure(detect_structure(decoded.samples, decoded.sample_rate)):
        raise MissingPauseStructure(f"{path.name} に前後を有音に挟まれた無音区間が無い")
    return len(decoded.samples) / 2 / decoded.sample_rate


def synthesize_tracks(
    tts: Tts,
    tracks: Sequence[Track],
    *,
    language: Language,
    voice_id: str,
    out_dir: Path,
) -> SynthesisReport:
    """`tracks` をクラスタ順の連番で `out_dir` に書き出す。失敗したクラスタがあれば `SynthesisFailed`。

    `tracks` は台本ステージの順(クラスタ順、クラスタ内はパート順)のまま渡す。
    """
    meta = meta_for(language, voice_id)
    succeeded: list[SynthesizedTrack] = []
    failed: list[SynthesisFailure] = []
    clusters = [(cid, list(parts)) for cid, parts in groupby(tracks, key=lambda t: t.cluster_id)]
    if len({cid for cid, _ in clusters}) != len(clusters):
        raise ValueError("同じクラスタのトラックが連続していない")
    out_dir.mkdir(parents=True, exist_ok=True)
    for seq, (cluster_id, parts) in enumerate(clusters, start=1):
        identifier = f"{cluster_id.deck_id}_{cluster_id.local_id}"
        written: list[SynthesizedTrack] = []
        try:
            for i, track in enumerate(parts):
                path = track_path(out_dir, seq, identifier, suffix=part_suffix(i, len(parts)))
                seconds = _write_track(render_track(tts, track, voice_id), path, meta)
                written.append(SynthesizedTrack(cluster_id, path, seconds))
        except Exception as exc:  # TTS ポートは例外型を宣言しない。何であれ列挙して非0終了に回す。
            for i in range(len(parts)):
                path = track_path(out_dir, seq, identifier, suffix=part_suffix(i, len(parts)))
                path.unlink(missing_ok=True)
                sidecar_path(path).unlink(missing_ok=True)
            failed.append(SynthesisFailure(cluster_id, f"{type(exc).__name__}: {exc}"))
            continue
        succeeded.extend(written)

    playlist = None
    if not failed:
        playlist = write_playlist(out_dir, PLAYLIST_NAME, [PlaylistEntry(t.path, t.seconds) for t in succeeded])
    report = SynthesisReport(tuple(succeeded), tuple(failed), playlist)
    logger.info("audio: %d tracks, %.1f s total, %d clusters failed", len(succeeded), report.total_seconds, len(failed))
    if failed:
        raise SynthesisFailed(report)
    return report
