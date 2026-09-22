"""クラスタ要約の語句実在性と含意判定(C-003 / C-013 / R-3 / NFR-1 / A-5 / A-6)。"""

from pathlib import Path

import pytest

from quiz_sift.cluster.run import Clustering, cluster_deck
from quiz_sift.cluster.summary import UngroundedSummary, ungrounded_terms
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import QuestionId
from quiz_sift.ports.llm import ClassificationResult, Entailment, GroupProposal
from quiz_sift.safety.entailment import EntailmentRejected

type Groups = dict[str, tuple[QuestionId, ...]]

# 各グループの問題文に語句が実在する要約。
GROUNDED = {
    "s3": "S3 のストレージクラスはアクセス頻度で選ぶ",
    "ec2": "EC2 の購入オプション",
    "iam": "IAM ポリシーでは明示的な拒否が優先される",
    "rds": "RDS のマルチ AZ 配置とリードレプリカ",
    "eventbridge": "EventBridge のルールで Lambda を起動する",
}
# Lambda は eventbridge クラスタにはあるが s3 クラスタの問題には無い。
UNGROUNDED_S3 = "S3 のストレージクラスは Lambda で選ぶ"
# 語句はすべて s3 クラスタに実在するが、正解・解説(標準-IA はミリ秒単位で取り出せる)と矛盾する。
CONTRADICTING_S3 = "S3 標準-IA の取り出しには数時間かかる"


class StubLlm:
    def __init__(self, groups: Groups, summaries: dict[str, str], contradicted: frozenset[str] = frozenset()) -> None:
        self.groups = groups
        self.summaries = summaries
        self.contradicted = contradicted
        self.checked: list[tuple[str, str]] = []

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        return ClassificationResult(tuple(GroupProposal(k, v) for k, v in self.groups.items()), ())

    def summarize_cluster(self, members: Deck) -> str:
        (label,) = {k for k, ids in self.groups.items() if members.questions[0].question_id in ids}
        return self.summaries[label]

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        self.checked.append((reference, statement))
        return Entailment.CONTRADICTED if statement in self.contradicted else Entailment.CONSISTENT


class Writer:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, result: Clustering) -> Path:
        self.path.write_text("\n".join(c.summary for c in result.clusters), encoding="utf-8")
        return self.path


def test_summary_terms_must_exist_in_the_cluster(known5: tuple[Deck, Groups], tmp_path: Path) -> None:
    deck, groups = known5
    s3_members = Deck(
        deck.deck_id, deck.language, None, tuple(q for q in deck.questions if q.question_id in groups["s3"])
    )
    assert ungrounded_terms(GROUNDED["s3"], s3_members) == ()
    # 他のクラスタにある語でも、このクラスタの問題に無ければ実在しない。
    assert ungrounded_terms(UNGROUNDED_S3, s3_members) == ("Lambda",)

    # 実在する要約だけなら書き出される。
    ok = tmp_path / "ok.txt"
    cluster_deck(StubLlm(groups, GROUNDED), deck, Writer(ok))
    assert ok.read_text(encoding="utf-8").splitlines() == list(GROUNDED.values())

    # 実在しない語を含む要約があれば、含意判定より前に止まり、何も書かれない。
    bad = tmp_path / "bad.txt"
    llm = StubLlm(groups, {**GROUNDED, "s3": UNGROUNDED_S3})
    with pytest.raises(UngroundedSummary) as exc:
        cluster_deck(llm, deck, Writer(bad))
    assert exc.value.code not in (None, 0)
    assert "Lambda" not in str(exc.value)
    assert not bad.exists()
    assert llm.checked == []


def test_summary_goes_through_entailment(known5: tuple[Deck, Groups], tmp_path: Path) -> None:
    deck, groups = known5
    s3_members = Deck(
        deck.deck_id, deck.language, None, tuple(q for q in deck.questions if q.question_id in groups["s3"])
    )
    # 仕込んだ矛盾は語句の実在性では止まらない。止めるのは含意判定だけ。
    assert ungrounded_terms(CONTRADICTING_S3, s3_members) == ()

    out = tmp_path / "clusters.txt"
    llm = StubLlm(groups, {**GROUNDED, "s3": CONTRADICTING_S3}, contradicted=frozenset({CONTRADICTING_S3}))
    with pytest.raises(EntailmentRejected) as exc:
        cluster_deck(llm, deck, Writer(out))
    assert exc.value.verdict is Entailment.CONTRADICTED
    assert exc.value.code not in (None, 0)
    assert not out.exists()
    # 要約はそのクラスタの正解・公式解説を照合先として判定されている。
    (reference, statement) = next(pair for pair in llm.checked if pair[1] == CONTRADICTING_S3)
    assert "S3 標準-IA" in reference and "ミリ秒単位で取り出せる" in reference
    assert "Lambda" not in reference

    # 矛盾が無ければ、全クラスタの要約が1件ずつ判定されてから書き出される。
    llm = StubLlm(groups, GROUNDED)
    cluster_deck(llm, deck, Writer(out))
    assert out.exists()
    assert sorted(statement for _, statement in llm.checked) == sorted(GROUNDED.values())
