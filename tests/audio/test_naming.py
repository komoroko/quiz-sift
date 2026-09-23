"""連番命名と m3u(C-007 / R-7 / A-3 / A-8)。"""

from pathlib import Path

import pytest

from quiz_sift.audio.naming import part_suffix, track_path
from quiz_sift.audio.playlist import PlaylistEntry, write_playlist
from quiz_sift.audio.synthesize import synthesize_tracks
from quiz_sift.domain.language import Language

from .conftest import StubTts, track


def test_sorted_filenames_match_intended_order(tmp_path: Path) -> None:
    # 12 クラスタで連番が2桁に届き、ゼロ埋めしないと 10 が 2 の前に来る。3番目のクラスタは2パートに割る。
    tracks = []
    for n in range(1, 13):
        tracks.append(track(f"c{n}", 0))
        if n == 3:
            tracks.append(track(f"c{n}", 1))
    report = synthesize_tracks(StubTts(), tracks, language=Language.JA, voice_id="日语男", out_dir=tmp_path)

    intended = [t.path.name for t in report.succeeded]
    assert sorted(intended) == intended
    assert sorted(p.name for p in tmp_path.glob("*.mp3")) == intended
    assert intended[:4] == [
        "001_aws-saa_c1.mp3",
        "002_aws-saa_c2.mp3",
        "003a_aws-saa_c3.mp3",
        "003b_aws-saa_c3.mp3",
    ]
    assert intended[-1] == "012_aws-saa_c12.mp3"

    assert report.playlist == tmp_path / "playlist.m3u"
    lines = report.playlist.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#EXTM3U"
    assert [line for line in lines if not line.startswith("#")] == intended
    assert all(line.startswith("#EXTINF:") for line in lines[1::2])


def test_naming_and_playlist_are_parameterised(tmp_path: Path) -> None:
    # audio/ を編集せずに、別ディレクトリ・別識別子の系列(グロッサリ・復習)を出せる。
    glossary = tmp_path / "glossary"
    review = tmp_path / "review"
    for d in (glossary, review):
        d.mkdir()

    g = [track_path(glossary, i, f"aws-saa_glossary_{term}") for i, term in enumerate(["s3", "iam"], start=1)]
    assert [p.name for p in g] == ["001_aws-saa_glossary_s3.mp3", "002_aws-saa_glossary_iam.mp3"]
    assert all(p.parent == glossary for p in g)

    # 復習系列は誤答率順に振り直す: 連番と識別子の組は呼び出し側が決める。
    r = [track_path(review, 1, "aws-saa_c7", suffix=part_suffix(0, 2)), track_path(review, 2, "aws-saa_c2")]
    assert [p.name for p in r] == ["001a_aws-saa_c7.mp3", "002_aws-saa_c2.mp3"]

    for p in g:
        p.write_bytes(b"")
    m3u = write_playlist(glossary, "glossary", [PlaylistEntry(p, 12.4) for p in g])
    assert m3u == glossary / "glossary.m3u"
    assert m3u.read_text(encoding="utf-8").splitlines() == [
        "#EXTM3U",
        "#EXTINF:12,001_aws-saa_glossary_s3",
        "001_aws-saa_glossary_s3.mp3",
        "#EXTINF:12,002_aws-saa_glossary_iam",
        "002_aws-saa_glossary_iam.mp3",
    ]

    # 再生リストと別ディレクトリのファイルは相対名で書けないので拒む。
    with pytest.raises(ValueError):
        write_playlist(review, "review", [PlaylistEntry(g[0], 1.0)])


def test_sequence_that_breaks_sort_order_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        track_path(tmp_path, 1000, "aws-saa_c1")
    with pytest.raises(ValueError):
        part_suffix(0, 27)
    assert part_suffix(0, 1) == ""
