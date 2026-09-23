"""音声合成ステージ(C-007 / R-7 / ADR-004 / A-1 / A-5 / A-6 / A-7)。

TTS はスタブ、mp3 のエンコードとデコードは実際の ffmpeg で行う(サンドボックスイメージに入っている)。
"""

from pathlib import Path

import pytest

from quiz_sift.audio.codec import decode_mp3, encode_mp3
from quiz_sift.audio.silence import detect_structure, has_pause_structure
from quiz_sift.audio.synthesize import SynthesisFailed, render_track, synthesize_tracks
from quiz_sift.domain.ids import ClusterId
from quiz_sift.domain.language import Language
from quiz_sift.domain.script import Pause
from quiz_sift.ports.tts import PcmAudio
from quiz_sift.script.segments import QuestionScript

from .conftest import DECK, RATE, StubTts, question, tone, track


def test_inserted_pause_survives_mp3_encoding(tmp_path: Path) -> None:
    pcm = render_track(StubTts(seconds=0.5), track("c1"), "日语男")

    # PCM の段階で、Pause は 3.3 秒ちょうどの無音として Prompt と Answer の間に入っている。
    runs = detect_structure(pcm.samples, pcm.sample_rate)
    assert [(r.voiced, round(r.seconds, 3)) for r in runs] == [(True, 0.5), (False, 3.3), (True, 1.0)]

    # mp3 に通しても 3.0 秒以上残る。3.0 秒ちょうど挿入する実装は 2.99 秒程度に縮んで落ちる。
    path = tmp_path / "t.mp3"
    encode_mp3(pcm, path)
    decoded = decode_mp3(path, RATE)
    silent = [r for r in detect_structure(decoded.samples, decoded.sample_rate) if not r.voiced]
    assert len(silent) == 1
    assert silent[0].seconds >= 3.0
    assert has_pause_structure(detect_structure(decoded.samples, decoded.sample_rate))


def test_stage_refuses_a_track_whose_pause_does_not_survive(tmp_path: Path) -> None:
    # 無音がエンコード後に 3.0 秒を割るトラックは、書き出し後の判定で失敗クラスタに数えられる。
    q = question("q1")
    short = track("c1", 0, QuestionScript(q.question_id, q.prompt, Pause(2.5), q.answer, q.discrimination))
    with pytest.raises(SynthesisFailed) as exc:
        synthesize_tracks(StubTts(), [short], language=Language.JA, voice_id="日语男", out_dir=tmp_path)
    assert exc.value.report.failed[0].reason.startswith("MissingPauseStructure")
    assert not list(tmp_path.glob("*.mp3"))


def test_partial_failure_exits_nonzero(tmp_path: Path) -> None:
    tracks = [
        track("c1"),
        track("c2", 0, question("q2a")),
        track("c2", 1, question("q2b", prompt="壊れる問いかけ")),
        track("c3"),
    ]
    with pytest.raises(SystemExit) as exc:
        synthesize_tracks(StubTts(fail_on="壊れる"), tracks, language=Language.JA, voice_id="日语男", out_dir=tmp_path)

    # 非0で終了する(SystemExit にメッセージを載せると終了ステータスは 1)。
    assert isinstance(exc.value, SynthesisFailed)
    assert exc.value.code not in (None, 0)
    report = exc.value.report
    assert [f.cluster_id for f in report.failed] == [ClusterId(DECK, "c2")]
    assert [t.cluster_id.local_id for t in report.succeeded] == ["c1", "c3"]
    # 失敗クラスタのファイルは先に書けたパートも残さず、欠けた再生リストは書かない。
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "001_aws-saa_c1.json",
        "001_aws-saa_c1.mp3",
        "003_aws-saa_c3.json",
        "003_aws-saa_c3.mp3",
    ]
    assert report.playlist is None
    # 終了メッセージにはクラスタ ID だけが載り、読み上げテキストは載らない(NFR-2)。
    assert "c2" in str(exc.value.code)
    assert "壊れる" not in str(exc.value.code)


def test_durations_are_recorded(tmp_path: Path) -> None:
    tracks = [track("c1", 0), track("c1", 1), track("c2")]
    report = synthesize_tracks(StubTts(seconds=0.5), tracks, language=Language.JA, voice_id="日语男", out_dir=tmp_path)

    # 1トラック = Prompt 0.5 + Pause 3.3 + Answer 0.5 + Discrimination 0.5 = 4.8 秒(mp3 の遅延分だけ伸びる)。
    for t in report.succeeded:
        assert t.seconds == pytest.approx(4.8, abs=0.1)
    per_cluster = report.cluster_seconds
    assert per_cluster.keys() == {ClusterId(DECK, "c1"), ClusterId(DECK, "c2")}
    assert per_cluster[ClusterId(DECK, "c1")] == pytest.approx(9.6, abs=0.2)
    assert per_cluster[ClusterId(DECK, "c2")] == pytest.approx(4.8, abs=0.1)
    assert report.total_seconds == pytest.approx(sum(per_cluster.values()))
    assert report.playlist is not None
    assert "#EXTINF:5,001a_aws-saa_c1" in report.playlist.read_text(encoding="utf-8")


def test_mixed_language_text_is_passed_through(tmp_path: Path) -> None:
    mixed = "Amazon S3 の Lifecycle ルールで Glacier Deep Archive へ移行するには何を設定しますか"
    tts = StubTts()
    report = synthesize_tracks(
        tts, [track("c1", 0, question("q1", prompt=mixed))], language=Language.JA, voice_id="日语男", out_dir=tmp_path
    )

    assert len(report.succeeded) == 1
    # 言語で分割せず、1片のまま同じボイスで渡している。
    assert tts.calls[0] == (mixed, "日语男")
    assert [text for text, _ in tts.calls] == [mixed, "S3 標準-IA", "Glacier は取り出しに時間がかかる"]


def test_sample_rate_change_is_a_failure() -> None:
    class Drifting(StubTts):
        def synthesize(self, text: str, voice_id: str) -> PcmAudio:
            rate = RATE if not self.calls else RATE * 2
            self.calls.append((text, voice_id))
            return PcmAudio(tone(0.5, rate), rate)

    with pytest.raises(ValueError):
        render_track(Drifting(), track("c1"), "日语男")
