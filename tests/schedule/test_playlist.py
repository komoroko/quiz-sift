"""復習系列の書き出しとクラスタ集計の永続化(C-010 / R-10 / A-3 / A-4)。"""

from pathlib import Path

from quiz_sift.audio.naming import part_suffix, track_path
from quiz_sift.audio.playlist import PlaylistEntry, write_playlist
from quiz_sift.domain.attempt import ClusterStat
from quiz_sift.domain.ids import ClusterId, DeckId
from quiz_sift.schedule.playlist import record_and_rank, write_review_series
from quiz_sift.schedule.rank import NoTargets, rank_clusters
from quiz_sift.store.deck_scope import DeckScopedStore

from .conftest import DECK, T0, attempt, cluster


def _audio_series(audio_dir: Path) -> None:
    """音声ステージと同じ命名・m3u で、クラスタ順(c1 は2パート)の系列を置く。"""
    audio_dir.mkdir(parents=True)
    entries = []
    for seq, (local_id, parts) in enumerate([("c1", 2), ("c2", 1), ("c3", 1)], start=1):
        for i in range(parts):
            path = track_path(audio_dir, seq, f"{DECK}_{local_id}", suffix=part_suffix(i, parts))
            path.write_bytes(f"{local_id}-{i}".encode())
            entries.append(PlaylistEntry(path, 60.0 + seq))
    write_playlist(audio_dir, "playlist", entries)


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def test_review_series_does_not_rename_audio(tmp_path: Path) -> None:
    audio_dir = tmp_path / "out" / DECK / "audio"
    review_dir = tmp_path / "out" / DECK / "review"
    _audio_series(audio_dir)
    before = _snapshot(audio_dir)
    assert list(before) == [
        "001a_aws-saa_c1.mp3",
        "001b_aws-saa_c1.mp3",
        "002_aws-saa_c2.mp3",
        "003_aws-saa_c3.mp3",
        "playlist.m3u",
    ]

    ranks = rank_clusters(
        [
            ClusterStat(ClusterId(DECK, "c1"), 4, 1, T0),
            ClusterStat(ClusterId(DECK, "c2"), 3, 0, T0),
            ClusterStat(ClusterId(DECK, "c3"), 2, 2, T0),
        ]
    )
    m3u = write_review_series(ranks, audio_dir, review_dir)

    assert _snapshot(audio_dir) == before
    assert m3u == review_dir / "review.m3u"
    names = ["001_aws-saa_c3.mp3", "002a_aws-saa_c1.mp3", "002b_aws-saa_c1.mp3"]
    assert sorted(p.name for p in review_dir.glob("*.mp3")) == names
    assert [(review_dir / n).read_bytes() for n in names] == [b"c3-0", b"c1-0", b"c1-1"]
    assert m3u.read_text(encoding="utf-8").splitlines() == [
        "#EXTM3U",
        "#EXTINF:63,001_aws-saa_c3",
        "001_aws-saa_c3.mp3",
        "#EXTINF:61,002a_aws-saa_c1",
        "002a_aws-saa_c1.mp3",
        "#EXTINF:61,002b_aws-saa_c1",
        "002b_aws-saa_c1.mp3",
    ]

    # 順序が変われば振り直し、前回の連番は残さない。
    rerank = rank_clusters([ClusterStat(ClusterId(DECK, "c2"), 1, 1, T0), ClusterStat(ClusterId(DECK, "c3"), 2, 1, T0)])
    write_review_series(rerank, audio_dir, review_dir)
    assert sorted(p.name for p in review_dir.glob("*.mp3")) == ["001_aws-saa_c2.mp3", "002_aws-saa_c3.mp3"]
    assert _snapshot(audio_dir) == before

    # 該当なしなら系列を消し、再生リストも書かない。
    assert write_review_series(NoTargets(), audio_dir, review_dir) is None
    assert list(review_dir.iterdir()) == []
    assert _snapshot(audio_dir) == before


def test_cluster_stats_persist(tmp_path: Path) -> None:
    clusters = [cluster("c1", "q1a", "q1b"), cluster("c2", "q2a"), cluster("c3", "q3a")]
    attempts = [
        attempt("q1a", False, 10),
        attempt("q1b", True, 12),
        attempt("q2a", False, 30),
        attempt("q2a", True, 31),
        attempt("q3a", True, 5),
    ]
    ranks = record_and_rank(DeckScopedStore(DECK, tmp_path), clusters, attempts)
    assert not isinstance(ranks, NoTargets)

    # 別デッキの同じ local_id の集計は混ざらない(R-11)。
    other = DeckId("gcp-ace")
    record_and_rank(
        DeckScopedStore(other, tmp_path), [cluster("c3", "q3a", deck=other)], [attempt("q3a", False, 99, deck=other)]
    )

    # 別インスタンス(別プロセス相当)から読み戻した集計が順序の根拠になる。
    reread = DeckScopedStore(DECK, tmp_path).cluster_stats()
    assert [(s.cluster_id.local_id, s.presented, s.wrong, s.last_studied_at) for s in reread] == [
        ("c1", 2, 1, T0.replace(minute=12)),
        ("c2", 2, 1, T0.replace(minute=31)),
        ("c3", 1, 0, T0.replace(minute=5)),
    ]
    assert rank_clusters(reread) == ranks
    assert [r.cluster_id.local_id for r in ranks] == ["c1", "c2"]
    assert [r.stat for r in ranks] == [reread[0], reread[1]]
