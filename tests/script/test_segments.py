"""台本のセグメント順序と含意判定の結線(C-006 / C-013 / R-6 / NFR-1 / A-1 / A-6)。"""

from dataclasses import replace
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import AnswerElement
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt, ScriptSegment
from quiz_sift.recall.verify import RecallMaterial
from quiz_sift.safety.entailment import EntailmentRejected
from quiz_sift.script.generate import MissingAnswerElement, generate_scripts, question_script
from quiz_sift.script.segments import QuestionScript, ScriptOrderError, parse_questions

from .conftest import DECK, GOOD, WRONG, StubChecker, Writer, answered, recall_set

# 語句はすべて実在するが、正解・解説(標準-IA はミリ秒単位で取り出せる)と矛盾する答え。
CONTRADICTING = "S3 標準-IA の取り出しには数時間かかる"


def test_segment_order_is_enforced_by_type(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    writer = Writer(tmp_path / "script.tsv")
    generate_scripts(StubChecker(), deck, clusters, recall_set(GOOD), writer, progress=None)
    assert writer.result is not None
    tracks = writer.result.tracks
    assert [(t.cluster_id.local_id, [q.question_id.local_id for q in t.questions]) for t in tracks] == [
        ("c001", ["q01", "q02"]),
        ("c002", ["q03"]),
    ]
    # 生成された台本は1問ごとに Prompt → Pause → Answer → Discrimination で、間は Pause でしか表れない。
    for track in tracks:
        kinds = [type(s) for s in track.segments]
        assert kinds == [Prompt, Pause, Answer, Discrimination] * len(track.questions)
        assert all(s.seconds >= 3.0 for s in track.segments if isinstance(s, Pause))
        assert len(parse_questions(track.segments)) == len(track.questions)
    # 答えと識別ポイントは想起素材のまま。
    q01 = tracks[0].questions[0]
    assert (q01.answer.text, q01.discrimination) == (
        GOOD["q01"].answers[0].text,
        Discrimination(GOOD["q01"].discrimination.text, GOOD["q01"].discrimination.distractor_ids),
    )

    # 1問分の各位置は型で縛られる。地の文の「間」を Pause の位置に置くことはできない。
    qid = QuestionId(DECK, "q01")
    with pytest.raises(TypeError):
        QuestionScript(qid, Prompt("問い"), Prompt("【間】"), Answer(("答え",)), None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        QuestionScript(qid, Answer(("答え",)), Pause(3.3), Answer(("答え",)), None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        QuestionScript(qid, Prompt("問い"), Pause(3.3), Answer(("答え",)), Answer(("識別",)))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Pause(0)

    # 平たいセグメント列も文法で検証する。「間」をテキストに書いた台本は Pause を欠くので落ちる。
    bad: list[list[ScriptSegment]] = [
        [Prompt("問い【間】"), Answer(("答え",)), Discrimination("識別", (WRONG,))],
        [Prompt("問い"), Answer(("(3秒の間)答え",))],
        [Discrimination("識別", (WRONG,)), Prompt("問い"), Pause(3.3), Answer(("答え",))],
        [
            Prompt("問い"),
            Pause(3.3),
            Answer(("答え",)),
            Discrimination("識別", (WRONG,)),
            Discrimination("識別", (WRONG,)),
        ],
        [Prompt("問い"), Pause(3.3), Answer(("答え",)), Pause(3.3)],
        [Prompt("問い"), Pause(3.3)],
        [],
    ]
    for segments in bad:
        with pytest.raises(ScriptOrderError):
            parse_questions(segments)
    good: list[ScriptSegment] = [
        Prompt("問い1"),
        Pause(3.3),
        Answer(("答え1",)),
        Prompt("問い2"),
        Pause(3.3),
        Answer(("答え2",)),
        Discrimination("識別2", (WRONG,)),
    ]
    assert [len(u) for u in parse_questions(good)] == [3, 4]


def test_script_goes_through_entailment(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    # 矛盾の無い台本は、全問の答えと識別ポイントがその問題の正解・解説と照合されたうえで書かれる。
    ok = StubChecker()
    generate_scripts(ok, deck, clusters, recall_set(GOOD), Writer(tmp_path / "ok.tsv"), progress=None)
    assert (tmp_path / "ok.tsv").exists()
    statements = [s for _, s in ok.checked]
    for draft in GOOD.values():
        assert draft.answers[0].text in statements
        assert draft.discrimination.text in statements
    reference = next(r for r, s in ok.checked if s == GOOD["q01"].answers[0].text)
    assert "S3 標準-IA" in reference and "ミリ秒単位で取り出せる" in reference

    # 仕込んだ矛盾(contradicted)も、判定できないもの(uncertain)も、成果物を書かずに非0で止める。
    drafts = {**GOOD, "q01": answered(GOOD["q01"], CONTRADICTING)}
    for name, checker in {
        "contradicted": StubChecker(contradicted=frozenset({CONTRADICTING})),
        "uncertain": StubChecker(uncertain=frozenset({CONTRADICTING})),
    }.items():
        out = tmp_path / f"{name}.tsv"
        with pytest.raises(EntailmentRejected) as exc:
            generate_scripts(checker, deck, clusters, recall_set(drafts), Writer(out), progress=None)
        assert isinstance(exc.value, SystemExit) and exc.value.code
        assert exc.value.verdict.value == name
        assert CONTRADICTING in [s for _, s in checker.checked]
        assert not out.exists()


def test_answer_is_built_from_every_answer_element(deck: Deck) -> None:
    """N 要素の答えは要素ごとに Answer に入り、選択肢の順に番号なしで連結して読む。要素数が N と違えば止まる。"""
    single = next(q for q in deck.questions if q.question_id.local_id == "q01")
    question_id = single.question_id
    # q01 の選択肢で、正解を B と D の2件にした問題。
    multi = Question(
        question_id,
        single.stem,
        single.options,
        (OptionId(question_id, "B"), OptionId(question_id, "D")),
        single.explanation,
        single.studied,
    )
    # 要素は D・B の順で来ても、台本は選択肢の順(B・D)に並べる。
    two = replace(
        GOOD["q01"],
        answers=(
            AnswerElement(OptionId(question_id, "D"), "S3 One Zone-IA"),
            AnswerElement(OptionId(question_id, "B"), "S3 標準-IA"),
        ),
    )
    material = RecallMaterial(ClusterId(DECK, "c001"), question_id, two)
    script = question_script(material, multi, Language.JA)
    assert script.answer.texts == ("S3 標準-IA", "S3 One Zone-IA")
    # 連結は番号(「1.」「(1)」「1番」)を挟まない。
    assert script.answer.text == "S3 標準-IA S3 One Zone-IA"
    # 識別ポイントは本文と参照する誤答の ID を引き継ぐ。
    assert script.discrimination == Discrimination(
        GOOD["q01"].discrimination.text, GOOD["q01"].discrimination.distractor_ids
    )
    single_script = question_script(replace(material, draft=answered(GOOD["q01"], "単独")), single, Language.JA)
    assert single_script.answer.texts == ("単独",)

    # 要素が正解と1対1に対応しなければ、問題 ID と件数だけを載せて止まる。
    # 要素数は合っていても、正解(D)の要素が欠けて同じ正解(B)が重複しているものも止まる。
    duplicated = replace(two, answers=(two.answers[1], two.answers[1]))
    for question, draft in ((multi, GOOD["q01"]), (single, two), (multi, duplicated)):
        with pytest.raises(MissingAnswerElement) as exc:
            question_script(replace(material, draft=draft), question, Language.JA)
        assert exc.value.code and exc.value.question_id == question_id
        assert "S3" not in str(exc.value.code)


def test_segment_types_reject_malformed_bodies() -> None:
    """答えは1要素以上の文字列の tuple、識別ポイントは誤答の ID を1件以上持つ。"""
    for texts in ((), "答え", ("答え", 1)):
        with pytest.raises(TypeError):
            Answer(texts)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Discrimination("識別", ())
    assert Answer(("答え",)).text == "答え"
    assert Discrimination("識別", (WRONG,)).distractor_ids == (WRONG,)
