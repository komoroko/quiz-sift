"""下流のタスクが自分の scope 内で使える共有型が揃っていること(受入基準 A-4)。

`domain/` は T-001 の scope なので、ここに無い型は下流が自分では足せない。
「名前がある」だけでは足りないため、**下流が実際に読むフィールド**を名指しで検査する。
"""

import dataclasses
from dataclasses import fields
from datetime import UTC, datetime

import pytest

from quiz_sift.domain.attempt import AttemptRecord, ClusterStat
from quiz_sift.domain.audio_meta import AudioMeta
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId, SessionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import OPTION_COUNT, Option, Question
from quiz_sift.domain.recall import RecallKey
from quiz_sift.domain.report import IngestOk, IngestRejected, IngestReport, RejectionReason
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt
from quiz_sift.domain.studied import StudiedFlag

DECK = DeckId("deck-a")
Q1 = QuestionId(DECK, "q1")
OPTIONS = tuple(Option(OptionId(Q1, label), f"選択肢 {label}") for label in "ABCD")


def make_question(*, explanation: str | None = "公式解説", studied: StudiedFlag = StudiedFlag.UNKNOWN) -> Question:
    return Question(
        question_id=Q1,
        stem="問題文",
        options=OPTIONS,
        correct_option_id=OPTIONS[0].option_id,
        explanation=explanation,
        studied=studied,
    )


def field_names(datatype: type) -> list[str]:
    return [f.name for f in fields(datatype)]


def test_shared_types_expose_downstream_fields() -> None:
    """受入基準 A-4: 列挙された共有型がフィールドまで揃っていること。"""
    # OptionId — R-8 の採点と web からの送信はこの ID だけを使う。
    assert field_names(OptionId) == ["question_id", "local_id"]

    # Question — 選択肢4件を OptionId 付きで保持し、解説と既習フラグを持つ。
    question = make_question()
    assert len(question.options) == OPTION_COUNT
    assert [option.option_id for option in question.options] == [OptionId(Q1, label) for label in "ABCD"]
    assert all(isinstance(option.option_id, OptionId) for option in question.options)
    assert question.explanation == "公式解説"
    assert make_question(explanation=None).explanation is None
    assert question.studied is StudiedFlag.UNKNOWN
    assert question.correct_option_id == OPTIONS[0].option_id

    # IngestReport — R-1。保存則と解説の内訳。
    report = IngestReport(
        input_total=3,
        accepted=2,
        rejected=(IngestRejected(raw_id="raw-9", reason=RejectionReason.OPTION_COUNT_NOT_4),),
        with_explanation=1,
        without_explanation=1,
    )
    assert report.accepted + len(report.rejected) == report.input_total
    assert report.with_explanation + report.without_explanation == report.accepted
    assert IngestOk(question=question).question is question

    # AttemptRecord — 選択・正誤・所要時間・セッション ID。
    attempt = AttemptRecord(
        session_id=SessionId(DECK, "s1"),
        question_id=Q1,
        chosen=OPTIONS[0].option_id,
        correct=True,
        elapsed_seconds=4.5,
        answered_at=datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
    )
    assert attempt.chosen == OPTIONS[0].option_id
    assert attempt.correct is True
    assert attempt.elapsed_seconds == 4.5
    assert attempt.session_id == SessionId(DECK, "s1")

    # ClusterStat — 最終学習日時・誤答回数(R-10 の並べ替えの入力)。
    stat = ClusterStat(
        cluster_id=ClusterId(DECK, "c1"),
        presented=10,
        wrong=4,
        last_studied_at=datetime(2026, 9, 21, tzinfo=UTC),
    )
    assert stat.wrong == 4
    assert stat.presented == 10
    assert stat.last_studied_at == datetime(2026, 9, 21, tzinfo=UTC)
    assert ClusterStat(cluster_id=ClusterId(DECK, "c2"), presented=0, wrong=0, last_studied_at=None)

    # ScriptSegment — Prompt / Pause(seconds) / Answer / Discrimination。
    segments = [Prompt("問いかけ"), Pause(3.3), Answer("答え"), Discrimination("識別ポイント")]
    assert [type(segment).__name__ for segment in segments] == ["Prompt", "Pause", "Answer", "Discrimination"]
    assert segments[1] == Pause(seconds=3.3)
    assert field_names(Pause) == ["seconds"]

    # RecallKey — 語の「組」。単一文字列にしない。
    key = RecallKey(terms=("AWS", "料金", "設定"))
    assert key.terms == ("AWS", "料金", "設定")
    assert key.term_set == RecallKey(terms=("料金", "設定", "AWS")).term_set

    # AudioMeta — language と voice_id。
    meta = AudioMeta(language=Language.JA, voice_id="voice-ja-1")
    assert field_names(AudioMeta) == ["language", "voice_id"]
    assert meta.language is Language.JA


def test_text_bearing_types_keep_their_body_out_of_repr() -> None:
    """AR-17: 素の例外トレースバックにはログマスクが効かない(NFR-2)。"""
    secret = "教材本文そのもの"
    bearers: list[object] = [
        make_question(explanation=secret),
        Option(OPTIONS[0].option_id, secret),
        Prompt(secret),
        Answer(secret),
        Discrimination(secret),
        RecallKey(terms=(secret,)),
        IngestOk(question=make_question(explanation=secret)),
    ]
    for bearer in bearers:
        assert secret not in repr(bearer), f"{type(bearer).__name__} の repr に本文が載っている"


def test_shared_types_are_frozen() -> None:
    """共有型は値。下流が受け取ったあとに書き換えられない。"""
    for datatype in (Question, Option, IngestReport, AttemptRecord, ClusterStat, RecallKey, AudioMeta, Pause):
        assert dataclasses.is_dataclass(datatype), f"{datatype.__name__} が dataclass でない"
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_question().stem = "書き換え"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        AudioMeta(language=Language.JA, voice_id="v").voice_id = "w"  # type: ignore[misc]


def test_invariants_refuse_impossible_values() -> None:
    """不変条件が「テストで確かめる性質」ではなく「構築できない構造」であること。"""
    with pytest.raises(ValueError, match="選択肢は 4 件"):
        Question(
            question_id=Q1,
            stem="問題文",
            options=OPTIONS[:3],
            correct_option_id=OPTIONS[0].option_id,
            explanation=None,
            studied=StudiedFlag.UNKNOWN,
        )
    with pytest.raises(ValueError, match="correct_option_id"):
        Question(
            question_id=Q1,
            stem="問題文",
            options=OPTIONS,
            correct_option_id=OptionId(QuestionId(DECK, "q2"), "A"),
            explanation=None,
            studied=StudiedFlag.UNKNOWN,
        )
    with pytest.raises(ValueError, match="他の問題"):
        Question(
            question_id=Q1,
            stem="問題文",
            options=(*OPTIONS[:3], Option(OptionId(QuestionId(DECK, "q2"), "D"), "選択肢 D")),
            correct_option_id=OPTIONS[0].option_id,
            explanation=None,
            studied=StudiedFlag.UNKNOWN,
        )
    with pytest.raises(ValueError, match="保存則"):
        IngestReport(
            input_total=2,
            accepted=2,
            rejected=(IngestRejected(raw_id="raw-9", reason=RejectionReason.DUPLICATE_ID),),
            with_explanation=2,
            without_explanation=0,
        )
    with pytest.raises(ValueError, match="解説の内訳"):
        IngestReport(input_total=3, accepted=3, rejected=(), with_explanation=1, without_explanation=1)
    with pytest.raises(ValueError, match="別の問題の選択肢"):
        AttemptRecord(
            session_id=SessionId(DECK, "s1"),
            question_id=Q1,
            chosen=OptionId(QuestionId(DECK, "q2"), "A"),
            correct=True,
            elapsed_seconds=1.0,
            answered_at=datetime(2026, 9, 22, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="別デッキのセッション"):
        AttemptRecord(
            session_id=SessionId(DeckId("deck-b"), "s1"),
            question_id=Q1,
            chosen=OPTIONS[0].option_id,
            correct=True,
            elapsed_seconds=1.0,
            answered_at=datetime(2026, 9, 22, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="所要時間"):
        AttemptRecord(
            session_id=SessionId(DECK, "s1"),
            question_id=Q1,
            chosen=OPTIONS[0].option_id,
            correct=True,
            elapsed_seconds=-0.1,
            answered_at=datetime(2026, 9, 22, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="誤答回数が出題回数"):
        ClusterStat(cluster_id=ClusterId(DECK, "c1"), presented=1, wrong=2, last_studied_at=None)
    with pytest.raises(ValueError, match="最終学習日時"):
        ClusterStat(
            cluster_id=ClusterId(DECK, "c1"),
            presented=0,
            wrong=0,
            last_studied_at=datetime(2026, 9, 22, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="間の長さ"):
        Pause(seconds=0)
    with pytest.raises(ValueError, match="想起キー"):
        RecallKey(terms=())
    with pytest.raises(ValueError, match="voice_id"):
        AudioMeta(language=Language.JA, voice_id="")


def test_rejection_reasons_cover_the_designed_set() -> None:
    assert {reason.value for reason in RejectionReason} == {
        "option_count_not_4",
        "answer_unresolved",
        "duplicate_id",
    }
