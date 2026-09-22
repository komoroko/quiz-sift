"""排他分割とメトリクス(C-003 / R-3 / A-1)。

LLM の生の応答に、多重所属・分割呼び出しをまたいだ同一ラベル・デッキに無い ID・どこにも
属さない問題を仕込み、分割後に各問題がちょうど1クラスタに属すること、M と M/N が記録される
ことを見る。
"""

import logging

import pytest

from quiz_sift.cluster.assign import partition
from quiz_sift.cluster.metrics import measure
from quiz_sift.cluster.run import Clustering, cluster_deck
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, QuestionId
from quiz_sift.ports.llm import ClassificationResult, Entailment, GroupProposal

type Groups = dict[str, tuple[QuestionId, ...]]
DECK = DeckId("aws-saa")


def q(n: int) -> QuestionId:
    return QuestionId(DECK, f"q{n:02d}")


def raw_proposals(groups: Groups) -> tuple[GroupProposal, ...]:
    s3 = groups["s3"]
    return (
        # s3 は分割呼び出しの2回に割れて返ってくる。
        GroupProposal("s3", s3[:2]),
        # q01 は ec2 にも入っている(多重所属)。デッキに無い ID と別デッキの ID も混ざる。
        GroupProposal("ec2", (q(1), *groups["ec2"], QuestionId(DECK, "q99"), QuestionId(DeckId("gcp"), "q05"))),
        GroupProposal("iam", groups["iam"]),
        GroupProposal("s3", s3[2:]),
        GroupProposal("rds", groups["rds"]),
        # 最後の1問はどのグループにも入らない。
        GroupProposal("eventbridge", groups["eventbridge"][:-1]),
        GroupProposal("empty", ()),
    )


class StubLlm:
    def __init__(self, proposals: tuple[GroupProposal, ...]) -> None:
        self.proposals = proposals

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        return ClassificationResult(assignments=self.proposals, failures=())

    def summarize_cluster(self, members: Deck) -> str:
        # 構成員の問題文の先頭の語句をそのまま使う(実在性を満たす)。
        return members.questions[0].stem.split(" ")[0]

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        return Entailment.CONSISTENT


def test_exclusive_partition_and_metrics(known5: tuple[Deck, Groups], caplog: pytest.LogCaptureFixture) -> None:
    deck, groups = known5
    all_ids = {question.question_id for question in deck.questions}

    split = partition(deck, raw_proposals(groups))
    # 同じラベルは1つに合わさり、空のグループは残らない。多重所属は最初のグループが取る。
    assert [g.label for g in split.groups] == ["s3", "ec2", "iam", "rds", "eventbridge"]
    assert split.groups[0].question_ids == groups["s3"]
    assert split.groups[1].question_ids == groups["ec2"]
    # 分類できなかった問題は「その他」に入る。デッキに無い ID はどこにも現れない。
    assert split.other == (groups["eventbridge"][-1],)

    emitted: list[Clustering] = []
    with caplog.at_level(logging.INFO, logger="quiz_sift.cluster.metrics"):
        cluster_deck(StubLlm(raw_proposals(groups)), deck, emitted.append)
    (result,) = emitted

    # 排他分割: 全問題がちょうど1つのクラスタに属し、各クラスタが構成員の全 ID を持つ。
    members = [qid for cluster in result.clusters for qid in cluster.question_ids]
    assert sorted(members) == sorted(all_ids)
    assert len(members) == len(set(members))
    other = [c for c in result.clusters if c.cluster_id.local_id == "other"]
    assert len(other) == 1 and other[0].question_ids == (groups["eventbridge"][-1],)
    assert other[0].summary == "その他"

    # M と M/N が記録される。「その他」も1クラスタと数える。
    assert result.metrics == measure(split, failure_count=0)
    assert result.metrics.question_count == 16
    assert result.metrics.cluster_count == len(result.clusters) == 6
    assert result.metrics.m_over_n == pytest.approx(6 / 16)
    assert result.metrics.other_ratio == pytest.approx(1 / 16)
    assert result.metrics.max_cluster_ratio == pytest.approx(4 / 16)
    assert "n=16 m=6 m_over_n=0.3750 other_ratio=0.0625" in caplog.text
