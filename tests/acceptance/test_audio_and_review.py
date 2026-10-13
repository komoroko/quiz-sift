"""音声と復習リストの受入テスト(C-007 / C-010)。claim ごとに1本。

`cli.main` を conftest の実行ヘルパ越しに走らせ、`out/<deck>/audio/` と `out/<deck>/review/` を読む。
本文はすべて合成で、実教材は含まない(NFR-2)。StubLlm は1問1クラスタに分けるので、11問のデッキは
11クラスタ(連番が2桁に届き、ゼロ埋めが効かなければファイル名の昇順が崩れる数)になる。

無音区間は製品の判定器ではなく ffmpeg の silencedetect で測る。実 TTS の実測は T-047 が担う。
"""

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quiz_sift.audio.synthesize import SynthesisFailed
from quiz_sift.audio.voices import VOICE_TABLE, VoiceLanguageMismatch
from quiz_sift.domain.attempt import AttemptRecord
from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId, SessionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.ports.llm import RecallDraft
from quiz_sift.ports.tts import PcmAudio
from quiz_sift.schedule.playlist import record_and_rank, write_review_series
from quiz_sift.schedule.rank import NoTargets
from quiz_sift.store.deck_scope import DeckScopedStore

from .conftest import CliRunner, StubLlm, StubTts, write_deck

QUESTIONS = 11
PAUSE_THRESHOLD = 3.0
T0 = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


def _qid(n: int) -> str:
    return f"q{n:02d}"


def _row(n: int) -> dict[str, Any]:
    word = f"語{n:02d}番"
    return {
        "id": _qid(n),
        "stem": f"合成問題: {word}に対応する値はどれか。",
        "options": [{"id": label, "text": f"値{n:02d}{label}"} for label in "ABCD"],
        "answers": ["A"],
        "explanation": f"{word}には値{n:02d}Aが対応する。",
    }


def _draft(deck_id: DeckId, n: int) -> RecallDraft:
    question_id = QuestionId(deck_id, _qid(n))
    return RecallDraft(
        RecallKey((f"語{n:02d}番",)),
        (AnswerElement(OptionId(question_id, "A"), ANSWERS[_qid(n)]),),
        DiscriminationPoint(
            f"この語には先頭の値が結びつき、「値{n:02d}B」は別の語に結びつく", (OptionId(question_id, "B"),)
        ),
    )


ANSWERS = {_qid(n): f"値{n:02d}A" for n in range(1, QUESTIONS + 1)}
"""各問の答えの本文。"""


def _drafts(deck_id: DeckId) -> dict[str, RecallDraft]:
    """選択肢 ID はデッキに属するので、下書きはデッキごとに作る。"""
    return {_qid(n): _draft(deck_id, n) for n in range(1, QUESTIONS + 1)}


def _deck(workspace: Path, name: str) -> DeckId:
    deck_id = DeckId(name)
    write_deck(workspace, deck_id, [_row(n) for n in range(1, QUESTIONS + 1)])
    return deck_id


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _m3u_entries(m3u: Path) -> list[str]:
    lines = m3u.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#EXTM3U"
    return [line for line in lines[1:] if not line.startswith("#")]


def _silences(mp3: Path) -> tuple[list[tuple[float, float]], float]:
    """ffmpeg の silencedetect が見つけた3秒以上の無音区間(開始, 終了)と、ファイルの長さ。"""
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-i",
            str(mp3),
            "-af",
            "silencedetect=noise=-45dB:d=3",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    log = result.stderr
    starts = [float(s) for s in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(e) for e in re.findall(r"silence_end: (-?[\d.]+)", log)]
    times = re.findall(r"time=(\d+):(\d+):([\d.]+)", log)
    h, m, s = times[-1]
    duration = int(h) * 3600 + int(m) * 60 + float(s)
    # 末尾まで続く無音は silence_end が出ないので、ファイルの終わりで閉じる。
    ends += [duration] * (len(starts) - len(ends))
    return list(zip(starts, ends, strict=True)), duration


def _has_inner_silence(mp3: Path) -> bool:
    """前後を有音に挟まれた(先頭でも末尾でもない)3秒以上の無音区間があるか。"""
    silences, duration = _silences(mp3)
    return any(start > 0.05 and end < duration - 0.05 and end - start >= PAUSE_THRESHOLD for start, end in silences)


# ---- C-007 ----


class FailingTts(StubTts):
    """`poisoned` を含むテキストの合成で例外を上げる。"""

    def __init__(self, poisoned: tuple[str, ...]) -> None:
        super().__init__()
        self.poisoned = poisoned

    def synthesize(self, text: str, voice_id: str) -> PcmAudio:
        if any(p in text for p in self.poisoned):
            raise RuntimeError("synthesis failed")
        return super().synthesize(text, voice_id)


def test_c007_tracks_are_ordered_with_inner_silence(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    deck_id = _deck(workspace, "audio")
    tts = StubTts()
    run = CliRunner(workspace, capsys, StubLlm(drafts=_drafts(deck_id)), tts)

    clusters = _load(run("cluster", deck_id))["clusters"]
    assert len(clusters) == QUESTIONS
    audio_dir = Path(run("audio", deck_id))
    assert audio_dir == workspace / "out" / deck_id / "audio"

    # 全クラスタ分の mp3 が、ゼロ埋め連番のファイル名で書かれる。
    tracks = sorted(audio_dir.glob("*.mp3"))
    expected = [f"{seq:03d}_{deck_id}_{c['id']}.mp3" for seq, c in enumerate(clusters, start=1)]
    assert [p.name for p in tracks] == expected
    # ファイル名の昇順も m3u の順も、クラスタ順(台本の順)に一致する。
    assert _m3u_entries(audio_dir / "playlist.m3u") == expected
    # audio 段は台本のトラックだけを読む(グロッサリは glossary-audio 段。D-234)。
    spoken_order = [text for text, _ in tts.inputs if text in set(ANSWERS.values())]
    assert spoken_order == [ANSWERS[q] for c in clusters for q in c["questions"]]

    # 各トラックに、先頭と末尾を除く位置の3秒以上の無音区間が機械判定で見つかる。
    for track in tracks:
        assert _has_inner_silence(track), track.name

    # 記録した言語とボイスが、TTS に渡したボイスとボイス表の言語に一致する。
    voices = {voice for _, voice in tts.inputs}
    assert len(voices) == 1
    (voice,) = voices
    for track in tracks:
        meta = json.loads(track.with_suffix(".json").read_text(encoding="utf-8"))
        assert meta == {"language": Language.JA.value, "voice_id": voice}
        assert VOICE_TABLE[meta["voice_id"]].language == Language.JA

    # デッキの言語と合わないボイスは、書き出す前に拒む。
    mismatch = _deck(workspace, "audio-mismatch")
    en_voice = next(v for v, spec in VOICE_TABLE.items() if spec.language == Language.EN)
    with pytest.raises(VoiceLanguageMismatch):
        CliRunner(workspace, capsys, StubLlm(drafts=_drafts(mismatch)), StubTts())(
            "audio", mismatch, "--voice", en_voice
        )
    assert not (workspace / "out" / mismatch / "audio").exists()

    # 合成に失敗したクラスタは列挙されて非0終了し、欠けた再生リストは書かれない。
    partial = _deck(workspace, "audio-partial")
    failing = FailingTts((ANSWERS["q03"], ANSWERS["q10"]))
    with pytest.raises(SynthesisFailed) as exc:
        CliRunner(workspace, capsys, StubLlm(drafts=_drafts(partial)), failing)("audio", partial)
    assert exc.value.code not in (None, 0)
    owner = {q: c["id"] for c in clusters for q in c["questions"]}
    assert [f.cluster_id.local_id for f in exc.value.report.failed] == [owner["q03"], owner["q10"]]
    assert exc.value.report.playlist is None
    assert not (workspace / "out" / partial / "audio").exists()


# ---- C-010 ----


def _attempt(deck_id: DeckId, question: str, correct: bool, minutes: int) -> AttemptRecord:
    qid = QuestionId(deck_id, question)
    return AttemptRecord(
        session_id=SessionId(deck_id, "s1"),
        question_id=qid,
        chosen=frozenset({OptionId(qid, "A" if correct else "B")}),
        correct=correct,
        elapsed_seconds=5.0,
        answered_at=T0 + timedelta(minutes=minutes),
    )


def test_c010_review_list_orders_by_error_rate(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    deck_id = _deck(workspace, "review")
    run = CliRunner(workspace, capsys, StubLlm(drafts=_drafts(deck_id)), StubTts())
    clusters = _load(run("cluster", deck_id))["clusters"]
    owner = {q: c["id"] for c in clusters for q in c["questions"]}
    seq_of = {c["id"]: seq for seq, c in enumerate(clusters, start=1)}
    audio_dir = Path(run("audio", deck_id))
    audio_before = {p.name: p.read_bytes() for p in audio_dir.iterdir()}

    # 誤答率: q02 = 2/2、q07 = 2/3、q05 = 1/3、q09 = 0/1(誤答なしは並べない)。
    store = DeckScopedStore(deck_id, workspace)
    log = [
        ("q05", False, 1),
        ("q05", True, 2),
        ("q05", True, 3),
        ("q07", False, 4),
        ("q07", True, 5),
        ("q07", False, 6),
        ("q02", False, 7),
        ("q02", False, 8),
        ("q09", True, 9),
    ]
    for question, correct, minutes in log:
        store.record_attempt(_attempt(deck_id, question, correct, minutes))

    m3u = Path(run("review", deck_id))
    review_dir = workspace / "out" / deck_id / "review"
    assert m3u == review_dir / "review.m3u"
    order = ["q02", "q07", "q05"]
    expected = [f"{seq:03d}_{deck_id}_{owner[q]}.mp3" for seq, q in enumerate(order, start=1)]
    assert _m3u_entries(m3u) == expected
    assert sorted(p.name for p in review_dir.glob("*.mp3")) == expected
    # 復習系列はクラスタ音声の複製で、配信側の音声は改名しない。
    for seq, q in enumerate(order, start=1):
        source = audio_dir / f"{seq_of[owner[q]]:03d}_{deck_id}_{owner[q]}.mp3"
        assert (review_dir / expected[seq - 1]).read_bytes() == source.read_bytes()
    assert {p.name: p.read_bytes() for p in audio_dir.iterdir()} == audio_before

    # 最終学習日時と誤答回数が、ストアを開き直しても残っている。
    stats = {s.cluster_id.local_id: s for s in DeckScopedStore(deck_id, workspace).cluster_stats()}
    assert {owner[q]: (stats[owner[q]].wrong, stats[owner[q]].presented) for q in ("q02", "q07", "q05", "q09")} == {
        owner["q02"]: (2, 2),
        owner["q07"]: (2, 3),
        owner["q05"]: (1, 3),
        owner["q09"]: (0, 1),
    }
    assert stats[owner["q02"]].last_studied_at == T0 + timedelta(minutes=8)
    assert stats[owner["q09"]].last_studied_at == T0 + timedelta(minutes=9)
    assert stats[owner["q01"]].last_studied_at is None

    # 全問正解の記録なら、空の並びではなく『該当なし』が型で返り、前回の復習系列は消える。
    # (review 段はこのとき標準出力に何も書かないので、実行ヘルパではなく段の中身を直接呼ぶ。)
    perfect = DeckId("review-perfect")
    perfect_store = DeckScopedStore(perfect, workspace)
    for question, _, minutes in log:
        perfect_store.record_attempt(_attempt(perfect, question, True, minutes))
    perfect_clusters = [
        Cluster(ClusterId(perfect, c["id"]), c["summary"], tuple(QuestionId(perfect, q) for q in c["questions"]))
        for c in clusters
    ]
    ranks = record_and_rank(perfect_store, perfect_clusters, perfect_store.attempts())
    assert isinstance(ranks, NoTargets)
    assert all(s.wrong == 0 for s in perfect_store.cluster_stats())
    assert write_review_series(ranks, audio_dir, review_dir) is None
    assert list(review_dir.glob("*.mp3")) == []
    assert not (review_dir / "review.m3u").exists()
