"""台本のセグメント順序と含意判定の結線(C-006 / C-013 / R-6 / NFR-1 / A-1 / A-6)。"""

from dataclasses import replace
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.recall import AnswerElement
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt, ScriptSegment
from quiz_sift.recall.verify import RecallMaterial
from quiz_sift.safety.entailment import EntailmentRejected
from quiz_sift.script.generate import generate_scripts, question_script
from quiz_sift.script.segments import QuestionScript, ScriptOrderError, parse_questions

from .conftest import DECK, GOOD, StubChecker, Writer, answered, recall_set

# 語句はすべて実在するが、正解・解説(標準-IA はミリ秒単位で取り出せる)と矛盾する答え。
CONTRADICTING = "S3 標準-IA の取り出しには数時間かかる"


def test_segment_order_is_enforced_by_type(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    writer = Writer(tmp_path / "script.tsv")
    generate_scripts(StubChecker(), deck, clusters, recall_set(GOOD), writer)
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
        Discrimination(GOOD["q01"].discrimination.text),
    )

    # 1問分の各位置は型で縛られる。地の文の「間」を Pause の位置に置くことはできない。
    qid = QuestionId(DECK, "q01")
    with pytest.raises(TypeError):
        QuestionScript(qid, Prompt("問い"), Prompt("【間】"), Answer("答え"), None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        QuestionScript(qid, Answer("答え"), Pause(3.3), Answer("答え"), None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        QuestionScript(qid, Prompt("問い"), Pause(3.3), Answer("答え"), Answer("識別"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Pause(0)

    # 平たいセグメント列も文法で検証する。「間」をテキストに書いた台本は Pause を欠くので落ちる。
    bad: list[list[ScriptSegment]] = [
        [Prompt("問い【間】"), Answer("答え"), Discrimination("識別")],
        [Prompt("問い"), Answer("(3秒の間)答え")],
        [Discrimination("識別"), Prompt("問い"), Pause(3.3), Answer("答え")],
        [Prompt("問い"), Pause(3.3), Answer("答え"), Discrimination("識別"), Discrimination("識別")],
        [Prompt("問い"), Pause(3.3), Answer("答え"), Pause(3.3)],
        [Prompt("問い"), Pause(3.3)],
        [],
    ]
    for segments in bad:
        with pytest.raises(ScriptOrderError):
            parse_questions(segments)
    good: list[ScriptSegment] = [
        Prompt("問い1"),
        Pause(3.3),
        Answer("答え1"),
        Prompt("問い2"),
        Pause(3.3),
        Answer("答え2"),
        Discrimination("識別2"),
    ]
    assert [len(u) for u in parse_questions(good)] == [3, 4]


def test_script_goes_through_entailment(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    # 矛盾の無い台本は、全問の答えと識別ポイントがその問題の正解・解説と照合されたうえで書かれる。
    ok = StubChecker()
    generate_scripts(ok, deck, clusters, recall_set(GOOD), Writer(tmp_path / "ok.tsv"))
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
            generate_scripts(checker, deck, clusters, recall_set(drafts), Writer(out))
        assert isinstance(exc.value, SystemExit) and exc.value.code
        assert exc.value.verdict.value == name
        assert CONTRADICTING in [s for _, s in checker.checked]
        assert not out.exists()


def test_answer_elements_are_joined_into_one_answer() -> None:
    """N 要素の答えは、要素の順に空白で連結した1つの Answer になる。識別ポイントの本文はそのまま。"""
    question_id = QuestionId(DECK, "q01")
    two = replace(
        GOOD["q01"],
        answers=(
            AnswerElement(OptionId(question_id, "B"), "S3 標準-IA"),
            AnswerElement(OptionId(question_id, "D"), "S3 One Zone-IA"),
        ),
    )
    material = RecallMaterial(ClusterId(DECK, "c001"), question_id, two)
    script = question_script(material, Language.JA)
    assert script.answer.text == "S3 標準-IA S3 One Zone-IA"
    assert script.discrimination == Discrimination(GOOD["q01"].discrimination.text)
    assert question_script(replace(material, draft=answered(GOOD["q01"], "単独")), Language.JA).answer.text == "単独"
