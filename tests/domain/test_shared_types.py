"""下流のタスクが自分の scope 内で使える共有型が揃っていること(受入基準 A-4)。

`domain/` は T-001 の scope なので、ここに無い型は下流が自分では足せない。
「名前がある」だけでは足りないため、**下流が実際に読むフィールド**を名指しで検査する。
"""

import ast
import dataclasses
import json
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quiz_sift.domain.attempt import AttemptRecord, ClusterStat
from quiz_sift.domain.audio_meta import AudioMeta
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId, SessionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import OPTION_COUNTS, Option, Question
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
        correct_option_ids=(OPTIONS[0].option_id,),
        explanation=explanation,
        studied=studied,
    )


def field_names(datatype: type) -> list[str]:
    return [f.name for f in fields(datatype)]


def test_shared_types_expose_downstream_fields() -> None:
    """受入基準 A-4: 列挙された共有型がフィールドまで揃っていること。"""
    # OptionId — R-8 の採点と web からの送信はこの ID だけを使う。
    assert field_names(OptionId) == ["question_id", "local_id"]

    # Question — 選択肢 M 件を OptionId 付きで保持し、正解の ID の組・解説・既習フラグを持つ。
    question = make_question()
    assert len(question.options) in OPTION_COUNTS
    assert [option.option_id for option in question.options] == [OptionId(Q1, label) for label in "ABCD"]
    assert all(isinstance(option.option_id, OptionId) for option in question.options)
    assert question.explanation == "公式解説"
    assert make_question(explanation=None).explanation is None
    assert question.studied is StudiedFlag.UNKNOWN
    assert question.correct_option_ids == (OPTIONS[0].option_id,)
    assert question.answer_count == 1

    # IngestReport — R-1。保存則と解説の内訳。
    report = IngestReport(
        input_total=3,
        accepted=2,
        rejected=(IngestRejected(raw_id="raw-9", reason=RejectionReason.OPTION_COUNT_UNSUPPORTED),),
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
    with pytest.raises(ValueError, match="選択肢は"):
        Question(
            question_id=Q1,
            stem="問題文",
            options=OPTIONS[:3],
            correct_option_ids=(OPTIONS[0].option_id,),
            explanation=None,
            studied=StudiedFlag.UNKNOWN,
        )
    with pytest.raises(ValueError, match="correct_option_ids"):
        Question(
            question_id=Q1,
            stem="問題文",
            options=OPTIONS,
            correct_option_ids=(OptionId(QuestionId(DECK, "q2"), "A"),),
            explanation=None,
            studied=StudiedFlag.UNKNOWN,
        )
    with pytest.raises(ValueError, match="他の問題"):
        Question(
            question_id=Q1,
            stem="問題文",
            options=(*OPTIONS[:3], Option(OptionId(QuestionId(DECK, "q2"), "D"), "選択肢 D")),
            correct_option_ids=(OPTIONS[0].option_id,),
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
        "option_count_unsupported",
        "answer_unresolved",
        "duplicate_id",
    }


def _question(question_id: QuestionId, option_count: int, answers: tuple[int, ...]) -> Question:
    options = tuple(Option(OptionId(question_id, label), f"選択肢 {label}") for label in "ABCDEFG"[:option_count])
    return Question(
        question_id=question_id,
        stem="問題文",
        options=options,
        correct_option_ids=tuple(options[i].option_id for i in answers),
        explanation=None,
        studied=StudiedFlag.UNKNOWN,
    )


def test_question_holds_answer_ids_and_rejects_out_of_range() -> None:
    """受入基準 A-4: 正解は ID の組(選択肢順・重複なし・1 ≤ N < M)で、構築時に強制される。"""
    assert OPTION_COUNTS == frozenset({4, 5, 6})
    for m in sorted(OPTION_COUNTS):
        for n in range(1, m):
            question = _question(Q1, m, tuple(range(m - n, m)))
            assert len(question.options) == m
            assert question.answer_count == n
            assert question.correct_option_ids == tuple(o.option_id for o in question.options[m - n :])

    for m in (3, 7):
        with pytest.raises(ValueError, match="選択肢は"):
            _question(Q1, m, (0,))
    for answers, message in (
        ((), "正解は 1 件以上"),
        ((0, 1, 2, 3), "正解は 1 件以上"),
        ((1, 0), "選択肢順"),
        ((0, 0), "重複なく"),
    ):
        with pytest.raises(ValueError, match=message):
            _question(Q1, 4, answers)
    with pytest.raises(ValueError, match="この問題の選択肢"):
        Question(
            question_id=Q1,
            stem="問題文",
            options=OPTIONS,
            correct_option_ids=(OPTIONS[0].option_id, OptionId(QuestionId(DECK, "q2"), "B")),
            explanation=None,
            studied=StudiedFlag.UNKNOWN,
        )


_REPO = Path(__file__).resolve().parents[2]
_GONE = frozenset({"correct" + "_option_id", "OPTION" + "_COUNT", "OPTION" + "_COUNT" + "_NOT_4"})
"""単一正解の時代の識別子。組み立てで作り、このファイル自身が数えられないようにする。"""
_OLD_KEY = "answ" + "er"


def _identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.update(part for part in (*node.name.split("."), node.asname) if part)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            names.add(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
    return names


def test_identifiers_include_definitions() -> None:
    """互換エイリアスとして残りやすい定義(メソッド・プロパティ・クラス)の名前も A-5 の検査に掛かる。"""
    gone = "correct" + "_option_id"
    for source in (
        f"class Q:\n    @property\n    def {gone}(self): return self.ids[0]\n",
        f"async def {gone}(): pass\n",
        f"class {gone}: pass\n",
    ):
        assert gone in _identifiers(ast.parse(source)), source


def _old_key_rows(tree: ast.AST) -> list[int]:
    """旧キーを持つ取り込み用の行の行番号。

    行は dict リテラル(`"stem"` と `"options"` を持つもの)か、`question_row(...)` のキーワード引数で書かれる。
    """
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {key.value for key in node.keys if isinstance(key, ast.Constant)}
            if {"stem", "options"} <= keys and _OLD_KEY in keys:
                lines.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "question_row":
            if any(keyword.arg == _OLD_KEY for keyword in node.keywords):
                lines.append(node.lineno)
    return lines


def test_single_answer_names_are_gone() -> None:
    """受入基準 A-5: 単一正解の識別子と、取り込みの入力の旧キーが残っていない。"""
    sources = sorted((_REPO / "src").rglob("*.py")) + sorted((_REPO / "tests").rglob("*.py"))
    assert len(sources) > 50
    leftovers = []
    old_key_rows = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        leftovers += [f"{path.relative_to(_REPO)}: {name}" for name in sorted(_identifiers(tree) & _GONE)]
        old_key_rows += [f"{path.relative_to(_REPO)}:{line}" for line in _old_key_rows(tree)]
    assert leftovers == []
    assert old_key_rows == []

    fixtures = sorted((_REPO / "tests" / "ingest" / "fixtures").glob("*.jsonl")) + sorted(
        (_REPO / "tests" / "fixtures" / "ingest").glob("*.jsonl")
    )
    assert fixtures
    for path in fixtures:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip():
                assert _OLD_KEY not in json.loads(line), f"{path.relative_to(_REPO)}:{line_no}"
