"""台本の含意判定の途中結果からの再開(C-028 / ADR-028)。"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, OptionId, QuestionId
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt
from quiz_sift.pipeline.stage import Progress
from quiz_sift.ports.llm import Entailment, EntailmentStatement, LlmCallError
from quiz_sift.safety.entailment import EntailmentRejected, reference_for
from quiz_sift.script.generate import _asks, generate_scripts
from quiz_sift.script.segments import QuestionScript, Track

from .conftest import DECK, GOOD, StubChecker, Writer, recall_set

# トラックは c001(q01, q02)と c002(q03)の2本。主張は各問の答えと識別ポイントで、トラック順に並ぶ。
CLAIMS = [
    statement
    for qid in ("q01", "q02", "q03")
    for statement in (GOOD[qid].answers[0].text, GOOD[qid].discrimination.text)
]
TRACK_CLAIMS = (CLAIMS[:4], CLAIMS[4:])


@dataclass
class StopsAt(StubChecker):
    """`stop_at` 回目(1 始まり)の判定の呼び出しを、枠切れの失敗(判定値なし)にする。"""

    stop_at: int = 0

    def check_entailments(self, reference: str, statements: Sequence[EntailmentStatement]) -> tuple[Entailment, ...]:
        if len(self.calls) + 1 == self.stop_at:
            self.calls.append((reference, tuple(statements)))
            self.checked.extend((reference, s.statement) for s in statements)
            raise LlmCallError("合成の枠切れ")
        return super().check_entailments(reference, statements)


def statements(checker: StubChecker) -> list[str]:
    return [statement for _, statement in checker.checked]


def run(checker: StubChecker, deck: Deck, clusters: tuple[Cluster, ...], out: Path, progress: Progress) -> Writer:
    writer = Writer(out)
    generate_scripts(checker, deck, clusters, recall_set(GOOD), writer, progress=progress)
    return writer


def test_entailment_is_one_call_per_question(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    """T-084 A-3: トラックの中の1問ぶん(答えと識別ポイント)を1回で聞き、判定値を途中結果に残す。"""
    progress = Progress(tmp_path / "script.progress")
    checker = StubChecker()
    run(checker, deck, clusters, tmp_path / "script.tsv", progress)
    questions = {q.question_id.local_id: q for q in deck.questions}
    assert len(checker.calls) == 3
    for qid, (reference, asked) in zip(("q01", "q02", "q03"), checker.calls, strict=True):
        assert reference == reference_for(questions[qid])
        answer, discrimination = asked
        assert (answer.statement, answer.focus, answer.distractors) == (GOOD[qid].answers[0].text, None, ())
        texts = {o.option_id: o.text for o in questions[qid].options}
        expected = tuple(texts[d] for d in GOOD[qid].discrimination.distractor_ids)
        assert (discrimination.statement, discrimination.distractors) == (GOOD[qid].discrimination.text, expected)
    # 判定値はトラックごとに、主張の順で途中結果に残る。
    saved = {index: json.loads(raw)["verdicts"] for index, raw in progress.load().items()}
    assert saved == {0: ["consistent"] * len(TRACK_CLAIMS[0]), 1: ["consistent"] * len(TRACK_CLAIMS[1])}

    # 1件でも contradicted か uncertain なら EntailmentRejected。再開しても止まり、何も聞き直さない。
    q02_discrimination = CLAIMS[3]
    for verdict in ("contradicted", "uncertain"):
        progress, out = Progress(tmp_path / f"{verdict}.progress"), tmp_path / f"{verdict}.tsv"
        flagged = frozenset({q02_discrimination})
        rejecting = StubChecker(contradicted=flagged) if verdict == "contradicted" else StubChecker(uncertain=flagged)
        with pytest.raises(EntailmentRejected) as exc:
            run(rejecting, deck, clusters, out, progress)
        assert exc.value.verdict.value == verdict
        resumed = StubChecker()
        with pytest.raises(EntailmentRejected) as exc:
            run(resumed, deck, clusters, out, progress)
        assert exc.value.verdict.value == verdict
        assert resumed.calls == []
        assert not out.exists()

    # 問の途中で切れた判定値の列(旧形式)は読まない。
    track = json.loads(Progress(tmp_path / "script.progress").load()[0])
    progress = Progress(tmp_path / "partial.progress")
    progress.save(0, json.dumps({**track, "verdicts": ["consistent"]}).encode())
    with pytest.raises(ValueError, match="問の境界"):
        run(StubChecker(), deck, clusters, tmp_path / "partial.tsv", progress)


def test_resume_skips_tracks_that_were_judged_consistent(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A-1: 判定の途中で枠切れになって止まった後の再実行は、consistent だったトラックの主張を判定しない。"""
    caplog.set_level(logging.INFO, logger="quiz_sift.script.generate")
    progress, out = Progress(tmp_path / "script.progress"), tmp_path / "script.tsv"

    # 2本目のトラックの最初の問(3回目の呼び出し)で枠切れ。
    first = StopsAt(stop_at=3)
    with pytest.raises(LlmCallError):
        run(first, deck, clusters, out, progress)
    assert statements(first) == CLAIMS
    assert not out.exists()

    second = StubChecker()
    writer = run(second, deck, clusters, out, progress)
    assert statements(second) == TRACK_CLAIMS[1]
    assert writer.result is not None and len(writer.result.tracks) == 2
    assert out.exists()
    assert "script resume: reused=1 remaining=1" in caplog.messages

    # 1本目の途中(2問目)で止まった場合は、判定値のある問だけを飛ばして続きから判定する。
    progress, out = Progress(tmp_path / "mid.progress"), tmp_path / "mid.tsv"
    first = StopsAt(stop_at=2)
    with pytest.raises(LlmCallError):
        run(first, deck, clusters, out, progress)
    second = StubChecker()
    run(second, deck, clusters, out, progress)
    assert statements(second) == CLAIMS[2:]
    assert out.exists()


def test_rejected_track_stops_the_stage_across_resumes(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path
) -> None:
    """A-2: contradicted / uncertain のトラックは、1回の実行でも再開でも段を止め、成果物を書かない。

    再開ではどれも consistent を返す判定器を使う。判定値のある主張を判定し直す実装はここで赤になる。
    """
    q01_answer, q03_answer = CLAIMS[0], CLAIMS[4]

    # 1回の実行: contradicted はその問の後を問い合わせずに止め、uncertain は残りを判定したうえで止める。
    contradicting = StubChecker(contradicted=frozenset({q01_answer}))
    with pytest.raises(EntailmentRejected) as exc:
        run(contradicting, deck, clusters, tmp_path / "c.tsv", Progress(tmp_path / "c.progress"))
    assert exc.value.verdict is Entailment.CONTRADICTED
    assert statements(contradicting) == CLAIMS[:2]
    uncertain = StubChecker(uncertain=frozenset({q01_answer}))
    with pytest.raises(EntailmentRejected) as exc:
        run(uncertain, deck, clusters, tmp_path / "u.tsv", Progress(tmp_path / "u.progress"))
    assert exc.value.verdict is Entailment.UNCERTAIN
    assert statements(uncertain) == CLAIMS
    assert not (tmp_path / "c.tsv").exists() and not (tmp_path / "u.tsv").exists()

    # 再開で戻った contradicted は、何も問い合わせずに止める。
    resumed = StubChecker()
    with pytest.raises(EntailmentRejected) as exc:
        run(resumed, deck, clusters, tmp_path / "c.tsv", Progress(tmp_path / "c.progress"))
    assert exc.value.verdict is Entailment.CONTRADICTED
    assert resumed.checked == []

    # (1) uncertain のトラックの後で枠切れ。再開は残りのトラックだけを判定して止まる。
    progress, out = Progress(tmp_path / "1.progress"), tmp_path / "1.tsv"
    first = StopsAt(uncertain=frozenset({q01_answer}), stop_at=3)
    with pytest.raises(LlmCallError):
        run(first, deck, clusters, out, progress)
    second = StubChecker()
    with pytest.raises(EntailmentRejected) as exc:
        run(second, deck, clusters, out, progress)
    assert exc.value.verdict is Entailment.UNCERTAIN
    assert statements(second) == TRACK_CLAIMS[1]
    assert not out.exists()

    # (2) 1問のトラックで uncertain が返った後の再開は、その問を聞き直さずに止まる。
    progress, out = Progress(tmp_path / "2.progress"), tmp_path / "2.tsv"
    judged = StubChecker(uncertain=frozenset({q03_answer}))
    with pytest.raises(EntailmentRejected):
        run(judged, deck, clusters, out, progress)
    assert statements(judged) == CLAIMS
    second = StubChecker()
    with pytest.raises(EntailmentRejected) as exc:
        run(second, deck, clusters, out, progress)
    assert exc.value.verdict is Entailment.UNCERTAIN
    assert second.checked == []
    assert not out.exists()


def test_asks_refuse_a_distractor_that_is_not_a_wrong_option(deck: Deck) -> None:
    """識別ポイントの distractors は同じ問題の誤答の選択肢に限る(正解や別の問題の選択肢は通さない)。"""
    questions = {q.question_id: q for q in deck.questions}
    q01 = QuestionId(DECK, "q01")  # 正解は B

    def track(*distractors: OptionId) -> Track:
        script = QuestionScript(
            q01, Prompt("問い"), Pause(3.3), Answer(("S3 標準-IA",)), Discrimination("識別", distractors)
        )
        return Track(ClusterId(DECK, "c001"), 0, (script,))

    [asked] = _asks(track(OptionId(q01, "C")), questions)
    assert [s.distractors for s in asked.statements] == [(), ("S3 Glacier Deep Archive",)]
    for bad in ((OptionId(q01, "B"),), (OptionId(QuestionId(DECK, "q02"), "B"),)):
        with pytest.raises(ValueError, match="^distractors はこの問題の誤答の選択肢でなければならない$"):
            _asks(track(*bad), questions)
