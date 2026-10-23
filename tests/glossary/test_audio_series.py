"""グロッサリ音声の別系列出力(C-005 / R-5 / D-049 / A-1)。"""

import math
from array import array
from pathlib import Path

from quiz_sift.audio.naming import track_path
from quiz_sift.audio.synthesize import synthesize_tracks
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt
from quiz_sift.glossary.audio_series import glossary_dir, write_glossary_audio
from quiz_sift.glossary.describe import GlossaryEntry
from quiz_sift.glossary.verify import Consistent, Glossary
from quiz_sift.ports.tts import PcmAudio
from quiz_sift.script.segments import QuestionScript, Track

DECK = DeckId("aws-saa")
WRONG = OptionId(QuestionId(DECK, "q01"), "C")
"""テキストを直接与えて組む識別ポイントが参照する誤答の ID。"""
RATE = 16000


class StubTts:
    """テキスト1片につき 0.5 秒の正弦波を返し、読み上げたテキストを記録する。"""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def synthesize(self, text: str, voice_id: str) -> PcmAudio:
        self.texts.append(text)
        n = RATE // 2
        return PcmAudio(
            array("h", (round(9000 * math.sin(2 * math.pi * 440 * i / RATE)) for i in range(n))).tobytes(), RATE
        )


def _cluster_track(cluster: str) -> Track:
    qid = QuestionId(DECK, f"{cluster}-q")
    script = QuestionScript(
        qid, Prompt("S3 のストレージクラスは"), Pause(3.3), Answer(("S3 標準-IA",)), Discrimination("違い", (WRONG,))
    )
    return Track(ClusterId(DECK, cluster), 0, (script,))


def test_glossary_series_is_separate(tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    audio_dir = out_root / DECK / "audio"
    recall = synthesize_tracks(
        StubTts(),
        [_cluster_track(f"c{n}") for n in (1, 2, 3)],
        language=Language.JA,
        voice_id="日语男",
        out_dir=audio_dir,
    )
    before = {p.name: p.read_bytes() for p in audio_dir.iterdir()}

    glossary = Glossary(
        (GlossaryEntry("S3", "オブジェクトストレージ"), GlossaryEntry("IAM ロール", "一時的な認証情報を渡す仕組み")),
        Consistent(2),
    )
    tts = StubTts()
    result = write_glossary_audio(
        tts, glossary, deck_id=DECK, language=Language.JA, voice_id="日语男", out_root=out_root
    )

    # 別ディレクトリ out/<deck>/glossary/ に、想起素材とは独立に 1 から振った g 系列で出る。
    gdir = glossary_dir(out_root, DECK)
    assert gdir == out_root / DECK / "glossary"
    names = [t.path.name for t in result.tracks]
    assert names == ["g001_aws-saa_glossary.mp3", "g002_aws-saa_glossary.mp3"]
    assert sorted(p.name for p in gdir.glob("*.mp3")) == names
    assert all(t.path.parent == gdir for t in result.tracks)
    # 命名は audio.naming.track_path を通っている(prefix の付け足しだけ)。
    assert [n.removeprefix("g") for n in names] == [track_path(gdir, i, f"{DECK}_glossary").name for i in (1, 2)]
    # 用語と説明をそれぞれ読み上げる。
    assert tts.texts == ["S3", "オブジェクトストレージ", "IAM ロール", "一時的な認証情報を渡す仕組み"]

    # 専用の m3u はグロッサリのトラックだけを並べる。
    assert result.playlist == gdir / "glossary.m3u"
    entries = [line for line in result.playlist.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    assert entries == names

    # 想起素材の系列(audio/)は1バイトも変わらず、再生リストにもグロッサリは混ざらない。
    assert {p.name: p.read_bytes() for p in audio_dir.iterdir()} == before
    assert not any(n.startswith("g") for n in before)
    assert recall.playlist is not None
    recall_entries = [
        line for line in recall.playlist.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    ]
    assert recall_entries == [t.path.name for t in recall.succeeded]
    assert not set(recall_entries) & set(names)
