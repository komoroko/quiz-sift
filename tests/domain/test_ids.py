"""R-11 第1基準: 別デッキの問題が混入する経路がコード上に存在しないこと。

「混入しないことをテストで確かめる」のではなく、**混入したものを構築できない**
ことを見る。前者はテストが網羅した経路の話にしかならない。
"""

import ast
import dataclasses
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId, SessionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag

DECK_A = DeckId("deck-a")
DECK_B = DeckId("deck-b")

SRC = Path(__file__).resolve().parents[2] / "src" / "quiz_sift"

_COLLECTIONS = {"list", "tuple", "set", "frozenset", "Iterable", "Sequence", "Collection", "Iterator", "AbstractSet"}
_QUESTION_TYPES = {"Question", "QuestionId"}


def make_question(deck_id: DeckId, local_id: str) -> Question:
    question_id = QuestionId(deck_id, local_id)
    options = tuple(Option(OptionId(question_id, label), f"選択肢 {label}") for label in "ABCD")
    return Question(
        question_id=question_id,
        stem="問題文",
        options=options,
        correct_option_ids=(options[0].option_id,),
        explanation=None,
        studied=StudiedFlag.UNKNOWN,
    )


def _is_bare_question_collection(node: ast.expr | None) -> bool:
    """`list[Question]` のように、デッキの外で問題を束ねる注釈か。"""
    if not isinstance(node, ast.Subscript):
        return False
    container = node.value
    name = container.attr if isinstance(container, ast.Attribute) else getattr(container, "id", None)
    if name not in _COLLECTIONS:
        return False
    return any(isinstance(leaf, ast.Name) and leaf.id in _QUESTION_TYPES for leaf in ast.walk(node.slice))


def _functions_taking_loose_questions() -> list[str]:
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg is not None and _is_bare_question_collection(arg.annotation):
                    found.append(f"{path.relative_to(SRC)}::{node.name}({arg.arg})")
    return found


def test_ids_carry_deck_and_block_cross_deck_calls() -> None:
    """受入基準 A-1。

    - `QuestionId` / `ClusterId` は `deck_id` を含む frozen dataclass であること
      (`str` に置き換えると、別デッキの同じ通し番号どうしが等しくなって赤くなる)
    - 問題をまとめて扱う型は `Deck` / `Cluster` だけで、どちらも自分の `deck_id` と
      一致しない問題を拒むこと
    - 問題の集合を素の `list[Question]` で受け取る関数が製品コードのどこにも無いこと
      (`cluster()` を複数デッキ分の問題を受け取れるシグネチャにすると赤くなる)
    """
    for id_type in (QuestionId, ClusterId, SessionId):
        assert dataclasses.is_dataclass(id_type), f"{id_type.__name__} が dataclass でない"
        assert [f.name for f in dataclasses.fields(id_type)] == ["deck_id", "local_id"], (
            f"{id_type.__name__} が (deck_id, local_id) の複合キーでない"
        )
        assert not issubclass(id_type, str), f"{id_type.__name__} が str になっている"
        with pytest.raises(dataclasses.FrozenInstanceError):
            id_type(DECK_A, "x").local_id = "y"  # type: ignore[misc]

    # 別デッキの同じ通し番号は別物。str にすると等しくなる。
    assert QuestionId(DECK_A, "q1") != QuestionId(DECK_B, "q1")
    assert ClusterId(DECK_A, "c1") != ClusterId(DECK_B, "c1")
    assert SessionId(DECK_A, "s1") != SessionId(DECK_B, "s1")

    # OptionId は問題を内包するので、別の問題の「A」とは等しくならない(R-8 の採点の前提)。
    assert OptionId(QuestionId(DECK_A, "q1"), "A") != OptionId(QuestionId(DECK_A, "q2"), "A")
    assert OptionId(QuestionId(DECK_A, "q1"), "A").deck_id == DECK_A

    # 問題をまとめる型は、別デッキのものを混ぜた瞬間に構築できない。
    with pytest.raises(ValueError, match="別デッキ"):
        Deck(
            deck_id=DECK_A,
            language=Language.JA,
            default_studied=None,
            questions=(make_question(DECK_A, "q1"), make_question(DECK_B, "q1")),
        )
    with pytest.raises(ValueError, match="別デッキ"):
        Cluster(
            cluster_id=ClusterId(DECK_A, "c1"),
            summary="まとめ",
            question_ids=(QuestionId(DECK_A, "q1"), QuestionId(DECK_B, "q1")),
        )

    # 束ねられた問題を受け取る入口は `Deck` だけ。ここに `Iterable[Question]` を
    # 取る関数が生えたら、それが混入の経路になる。
    loose = _functions_taking_loose_questions()
    assert not loose, f"デッキの外で問題を束ねて受け取る関数がある: {loose}"


def test_a_deck_accepts_its_own_questions() -> None:
    """拒否側だけでは「常に例外」の実装でも緑になる。受け入れ側も固定する。"""
    deck = Deck(
        deck_id=DECK_A,
        language=Language.JA,
        default_studied=StudiedFlag.UNSEEN,
        questions=(make_question(DECK_A, "q1"), make_question(DECK_A, "q2")),
    )
    assert len(deck.questions) == 2
    cluster = Cluster(
        cluster_id=ClusterId(DECK_A, "c1"),
        summary="まとめ",
        question_ids=(QuestionId(DECK_A, "q1"), QuestionId(DECK_A, "q2")),
    )
    assert len(cluster.question_ids) == 2


def test_the_source_scan_can_go_red() -> None:
    """`_functions_taking_loose_questions` の negative control。

    走査が何も見つけないのは「そういう関数が無い」からであって、走査が壊れて
    いるからではないこと。壊れた走査は上のテストを永久に緑にする。
    """

    def annotation_of(source: str) -> ast.expr | None:
        function = ast.parse(source).body[0]
        assert isinstance(function, ast.FunctionDef)
        return function.args.args[0].annotation

    assert _is_bare_question_collection(annotation_of("def cluster(questions: list[Question]) -> None: ..."))
    assert _is_bare_question_collection(annotation_of("def cluster(ids: Iterable[QuestionId]) -> None: ..."))
    assert not _is_bare_question_collection(annotation_of("def cluster(deck: Deck) -> None: ..."))
    assert not _is_bare_question_collection(annotation_of("def render(question: Question) -> None: ..."))


def test_duplicate_members_are_refused() -> None:
    with pytest.raises(ValueError, match="重複"):
        Deck(
            deck_id=DECK_A,
            language=Language.JA,
            default_studied=None,
            questions=(make_question(DECK_A, "q1"), make_question(DECK_A, "q1")),
        )
    with pytest.raises(ValueError, match="重複"):
        Cluster(
            cluster_id=ClusterId(DECK_A, "c1"),
            summary="まとめ",
            question_ids=(QuestionId(DECK_A, "q1"), QuestionId(DECK_A, "q1")),
        )


def test_an_empty_cluster_is_refused() -> None:
    with pytest.raises(ValueError, match="空のクラスタ"):
        Cluster(cluster_id=ClusterId(DECK_A, "c1"), summary="まとめ", question_ids=())


def test_ids_are_hashable_and_usable_as_dict_keys() -> None:
    """ID は集計の辞書キーになる。frozen なので hash が効くこと。"""
    counts = {QuestionId(DECK_A, "q1"): 1, QuestionId(DECK_B, "q1"): 2}
    assert counts[QuestionId(DECK_A, "q1")] == 1
    assert len(counts) == 2
