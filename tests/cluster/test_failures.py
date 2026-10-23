"""外部呼び出しの失敗で止まること(C-003 / R-3 / AR-5 / S-1 / A-4)。

分割呼び出しのうち2回が失敗したスタブを使う。失敗した問題は「その他」に落ちるので排他分割も
M/N も成立するが、その実行は下流へ進まず非0で終了し、サマリの3値が実際の値と一致すること。
"""

import logging
import re
from collections.abc import Sequence

import pytest

from quiz_sift.cluster.run import ClassificationFailed, Clustering, cluster_deck
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import QuestionId
from quiz_sift.ports.llm import CallFailure, ClassificationResult, Entailment, EntailmentStatement, GroupProposal, Retry

type Groups = dict[str, tuple[QuestionId, ...]]


class PartiallyFailingLlm:
    """3グループは返し、残る2グループ分の分割呼び出しは失敗として返す。"""

    def __init__(self, groups: Groups) -> None:
        self.groups = groups
        self.summarized = 0
        self.checked = 0

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        ok = [GroupProposal(label, self.groups[label]) for label in ("s3", "ec2", "iam")]
        failed = [CallFailure(self.groups[label], "LlmCallError: 429") for label in ("rds", "eventbridge")]
        return ClassificationResult(assignments=tuple(ok), failures=tuple(failed))

    def summarize_cluster(self, members: Deck, retry: Retry[str] | None = None) -> str:
        self.summarized += 1
        return members.questions[0].stem.split(" ")[0]

    def check_entailments(self, reference: str, statements: Sequence[EntailmentStatement]) -> tuple[Entailment, ...]:
        return tuple(self._verdict(reference, s.statement) for s in statements)

    def _verdict(self, reference: str, statement: str) -> Entailment:
        self.checked += 1
        return Entailment.CONSISTENT


def test_partial_failure_stops_the_pipeline(known5: tuple[Deck, Groups], caplog: pytest.LogCaptureFixture) -> None:
    deck, groups = known5
    llm = PartiallyFailingLlm(groups)
    emitted: list[Clustering] = []

    with caplog.at_level(logging.INFO, logger="quiz_sift.cluster.metrics"), pytest.raises(SystemExit) as exc:
        cluster_deck(llm, deck, emitted.append)

    # 非0で終了する(SystemExit にメッセージを載せると終了ステータスは 1)。
    assert isinstance(exc.value, ClassificationFailed)
    assert exc.value.code not in (None, 0)
    # 下流(要約・含意判定・書き出し)へ進んでいない。
    assert (llm.summarized, llm.checked, emitted) == (0, 0, [])

    # サマリの3値が実際の値と一致する。失敗した2グループ(6問)は「その他」に落ちている。
    failed_questions = len(groups["rds"]) + len(groups["eventbridge"])
    expected = {
        "other_ratio": failed_questions / len(deck.questions),
        "max_cluster_ratio": max(len(groups["s3"]), failed_questions) / len(deck.questions),
        "failure_count": 2,
    }
    found = re.search(r"other_ratio=([\d.]+) max_cluster_ratio=([\d.]+) failure_count=(\d+)", caplog.text)
    assert found is not None
    assert float(found[1]) == pytest.approx(expected["other_ratio"], abs=1e-4)
    assert float(found[2]) == pytest.approx(expected["max_cluster_ratio"], abs=1e-4)
    assert int(found[3]) == expected["failure_count"]
    assert exc.value.metrics.failure_count == 2
    assert exc.value.metrics.other_ratio == pytest.approx(expected["other_ratio"])
