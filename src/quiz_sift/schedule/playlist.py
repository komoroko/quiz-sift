"""復習用再生リスト(R-10 / AR-24)。

`out/<deck>/audio/` はクラスタ順の連番で、そこを改名すると R-7 の「ファイル名昇順で意図した順に
並ぶ」が壊れる。復習系列は `out/<deck>/review/` へ複製し、そこで誤答率順の連番を振り直す。複製は
可能ならハードリンクにする。端末へは review/ だけを転送する。

入力は音声ステージが書いた `audio/playlist.m3u`。これは全クラスタの音声化が成功したときだけ書かれる
ので、欠けたトラックを参照しない(AR-10)。
"""

import os
import re
import shutil
from collections.abc import Sequence
from pathlib import Path

from quiz_sift.audio.naming import part_suffix, track_path
from quiz_sift.audio.playlist import PlaylistEntry, write_playlist
from quiz_sift.audio.synthesize import PLAYLIST_NAME
from quiz_sift.domain.attempt import AttemptRecord
from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.ids import ClusterId
from quiz_sift.schedule.rank import ClusterRank, NoTargets, cluster_stats, rank_clusters
from quiz_sift.store.deck_scope import DeckScopedStore

REVIEW_PLAYLIST_NAME = "review"

_TRACK_NAME = re.compile(r"^\d{3}[a-z]?_(?P<identifier>.+)\.mp3$")
_EXTINF = re.compile(r"^#EXTINF:(?P<seconds>-?\d+),")


def identifier(cluster_id: ClusterId) -> str:
    """音声ステージと同じファイル名上の識別子。"""
    return f"{cluster_id.deck_id}_{cluster_id.local_id}"


def record_and_rank(
    store: DeckScopedStore, clusters: Sequence[Cluster], attempts: Sequence[AttemptRecord]
) -> tuple[ClusterRank, ...] | NoTargets:
    """クラスタ集計をストアへ書き、ストアから読み戻した集計で並べる。順序の根拠はストアに残る。"""
    for stat in cluster_stats(clusters, attempts):
        store.record_cluster_stat(stat)
    return rank_clusters(store.cluster_stats())


def read_audio_playlist(audio_dir: Path) -> dict[str, list[PlaylistEntry]]:
    """音声ステージの m3u を識別子ごとのパート列(再生順)に読む。"""
    lines = (audio_dir / f"{PLAYLIST_NAME}.m3u").read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "#EXTM3U":
        raise ValueError(f"{audio_dir} の再生リストが拡張 m3u ではない")
    tracks: dict[str, list[PlaylistEntry]] = {}
    seconds: float | None = None
    for line in lines[1:]:
        extinf = _EXTINF.match(line)
        if extinf:
            seconds = float(extinf["seconds"])
            continue
        name = _TRACK_NAME.match(line)
        if name is None or seconds is None:
            raise ValueError(f"再生リストの行を読めない: {line!r}")
        tracks.setdefault(name["identifier"], []).append(PlaylistEntry(audio_dir / line, seconds))
        seconds = None
    return tracks


def _clear(review_dir: Path) -> None:
    # 前回の系列が残ると、順序の変わったクラスタが2つの連番で並ぶ。
    for stale in review_dir.glob("*.mp3"):
        stale.unlink()
    (review_dir / f"{REVIEW_PLAYLIST_NAME}.m3u").unlink(missing_ok=True)


def _duplicate(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:  # 別ファイルシステムなどハードリンクできないときは実体を複製する。
        shutil.copy2(source, target)


def write_review_series(ranks: tuple[ClusterRank, ...] | NoTargets, audio_dir: Path, review_dir: Path) -> Path | None:
    """`ranks` の順で `audio_dir` のトラックを `review_dir` へ複製し、連番と m3u を振る。

    `audio_dir` は読むだけで改名しない。`NoTargets` なら前回の系列を消して `None` を返す。
    """
    review_dir.mkdir(parents=True, exist_ok=True)
    _clear(review_dir)
    if isinstance(ranks, NoTargets):
        return None
    tracks = read_audio_playlist(audio_dir)
    missing = [r.cluster_id.local_id for r in ranks if identifier(r.cluster_id) not in tracks]
    if missing:
        raise ValueError(f"音声の無いクラスタ: {missing}")
    entries: list[PlaylistEntry] = []
    for seq, rank in enumerate(ranks, start=1):
        parts = tracks[identifier(rank.cluster_id)]
        for i, part in enumerate(parts):
            target = track_path(review_dir, seq, identifier(rank.cluster_id), suffix=part_suffix(i, len(parts)))
            _duplicate(part.path, target)
            entries.append(PlaylistEntry(target, part.seconds))
    return write_playlist(review_dir, REVIEW_PLAYLIST_NAME, entries)
