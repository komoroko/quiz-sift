"""台本の含意判定の途中結果からの再開(C-028 / ADR-028)。"""

import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.pipeline.stage import Progress
from quiz_sift.ports.llm import Entailment, LlmCallError
from quiz_sift.safety.entailment import EntailmentRejected
from quiz_sift.script.generate import generate_scripts

from .conftest import GOOD, StubChecker, Writer, recall_set

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

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        if len(self.checked) + 1 == self.stop_at:
            self.checked.append((reference, statement))
            raise LlmCallError("合成の枠切れ")
        return super().check_entailment(reference, statement)


def statements(checker: StubChecker) -> list[str]:
    return [statement for _, statement in checker.checked]


def run(checker: StubChecker, deck: Deck, clusters: tuple[Cluster, ...], out: Path, progress: Progress) -> Writer:
    writer = Writer(out)
    generate_scripts(checker, deck, clusters, recall_set(GOOD), writer, progress=progress)
    return writer


def test_resume_skips_tracks_that_were_judged_consistent(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A-1: 判定の途中で枠切れになって止まった後の再実行は、consistent だったトラックの主張を判定しない。"""
    caplog.set_level(logging.INFO, logger="quiz_sift.script.generate")
    progress, out = Progress(tmp_path / "script.progress"), tmp_path / "script.tsv"

    # 2本目のトラックの最初の主張で枠切れ。
    first = StopsAt(stop_at=len(TRACK_CLAIMS[0]) + 1)
    with pytest.raises(LlmCallError):
        run(first, deck, clusters, out, progress)
    assert statements(first) == CLAIMS[: len(TRACK_CLAIMS[0]) + 1]
    assert not out.exists()

    second = StubChecker()
    writer = run(second, deck, clusters, out, progress)
    assert statements(second) == TRACK_CLAIMS[1]
    assert writer.result is not None and len(writer.result.tracks) == 2
    assert out.exists()
    assert "script resume: reused=1 remaining=1" in caplog.messages

    # 1本目の途中で止まった場合は、判定値のある主張だけを飛ばして続きから判定する。
    progress, out = Progress(tmp_path / "mid.progress"), tmp_path / "mid.tsv"
    first = StopsAt(stop_at=3)
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
    q01_answer, q03_answer, q03_discrimination = CLAIMS[0], CLAIMS[4], CLAIMS[5]

    # 1回の実行: contradicted は残りを問い合わせずに止め、uncertain は残りを判定したうえで止める。
    contradicting = StubChecker(contradicted=frozenset({q01_answer}))
    with pytest.raises(EntailmentRejected) as exc:
        run(contradicting, deck, clusters, tmp_path / "c.tsv", Progress(tmp_path / "c.progress"))
    assert exc.value.verdict is Entailment.CONTRADICTED
    assert statements(contradicting) == [q01_answer]
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
    first = StopsAt(uncertain=frozenset({q01_answer}), stop_at=len(TRACK_CLAIMS[0]) + 1)
    with pytest.raises(LlmCallError):
        run(first, deck, clusters, out, progress)
    second = StubChecker()
    with pytest.raises(EntailmentRejected) as exc:
        run(second, deck, clusters, out, progress)
    assert exc.value.verdict is Entailment.UNCERTAIN
    assert statements(second) == TRACK_CLAIMS[1]
    assert not out.exists()

    # (2) 1問のトラック(主張2件)で、主張1に uncertain、主張2で枠切れ。再開は主張2だけを判定して止まる。
    progress, out = Progress(tmp_path / "2.progress"), tmp_path / "2.tsv"
    first = StopsAt(uncertain=frozenset({q03_answer}), stop_at=len(CLAIMS))
    with pytest.raises(LlmCallError):
        run(first, deck, clusters, out, progress)
    assert statements(first) == CLAIMS
    second = StubChecker()
    with pytest.raises(EntailmentRejected) as exc:
        run(second, deck, clusters, out, progress)
    assert exc.value.verdict is Entailment.UNCERTAIN
    assert statements(second) == [q03_discrimination]
    assert not out.exists()
