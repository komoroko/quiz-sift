"""クラスタ要約(R-3 / NFR-1)。要約を作り、2つの検査を通ったときだけ成果物を書く。

1. **語句の実在性**(ADR-010): 要約から切り出した名詞句のすべてが、そのクラスタに属する問題の
   本文(問題文・選択肢・解説)のどれかに実在すること。1語でも実在しなければ止める。
2. **含意判定**(NFR-1 / D-042): 要約が、そのクラスタの問題の正解・公式解説と矛盾しないこと。
   `safety.entailment.emit_if_consistent` 越しに書き出すので、`consistent` 以外で成果物は書かれない。

「その他」クラスタは論点を持たないので要約を生成せず、固定のラベルを置く。知識の主張ではないので
どちらの検査にもかけない。
"""

from collections.abc import Callable
from typing import Protocol

from quiz_sift.cluster.assign import Partition
from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.grounding.presence import is_present
from quiz_sift.grounding.terms import extract_terms
from quiz_sift.ports.llm import GroupProposal
from quiz_sift.safety.entailment import Claim, EntailmentChecker, emit_if_consistent

OTHER_LOCAL_ID = "other"
"""「その他」クラスタの `ClusterId.local_id`。"""
OTHER_SUMMARY = {Language.JA: "その他", Language.EN: "Other"}
"""「その他」クラスタの要約として置く固定のラベル。"""


class SummaryLlm(EntailmentChecker, Protocol):
    """`ports.llm.Llm` のうち要約と含意判定だけを使う。`Llm` の実装はそのまま渡せる。"""

    def summarize_cluster(self, members: Deck) -> str: ...


class UngroundedSummary(SystemExit):
    """要約に、そのクラスタの問題に実在しない語句がある。捕まえずに上げれば非0で終了する。

    メッセージにはクラスタ ID と語句の数だけを載せ、語句そのものは載せない(NFR-2)。
    """

    def __init__(self, cluster_id: ClusterId, missing: int) -> None:
        super().__init__(f"クラスタ {cluster_id.local_id} の要約に、問題文に実在しない語句が {missing} 件ある")
        self.cluster_id = cluster_id


def _texts(question: Question) -> list[str]:
    texts = [question.stem, *(option.text for option in question.options)]
    if question.explanation is not None:
        texts.append(question.explanation)
    return texts


def ungrounded_terms(summary: str, members: Deck) -> tuple[str, ...]:
    """`summary` の名詞句のうち、`members` のどの問題の本文にも実在しないもの。"""
    texts = [text for question in members.questions for text in _texts(question)]
    return tuple(term for term in extract_terms(summary, members.language) if not is_present(term, texts))


def _reference(members: Deck) -> str:
    lines = []
    for question in members.questions:
        correct = next(o.text for o in question.options if o.option_id == question.correct_option_id)
        line = f"問題: {question.stem} 正解: {correct}"
        if question.explanation is not None:
            line += f" 解説: {question.explanation}"
        lines.append(line)
    return "\n".join(lines)


def _members(deck: Deck, group: GroupProposal) -> Deck:
    wanted = set(group.question_ids)
    return Deck(
        deck_id=deck.deck_id,
        language=deck.language,
        default_studied=deck.default_studied,
        questions=tuple(q for q in deck.questions if q.question_id in wanted),
    )


def emit_summarized[T](
    llm: SummaryLlm, deck: Deck, partition: Partition, emit: Callable[[tuple[Cluster, ...]], T]
) -> T:
    """各クラスタを要約し、実在性と含意判定を通ったときだけ `emit` にクラスタ列を渡して書かせる。

    実在しない語句があれば `UngroundedSummary`、含意判定が `consistent` でなければ
    `EntailmentRejected`。どちらも `emit` を呼ぶ前に上がる。
    """
    clusters: list[Cluster] = []
    claims: list[Claim] = []
    for index, group in enumerate(partition.groups, start=1):
        cluster_id = ClusterId(deck.deck_id, f"c{index:03d}")
        members = _members(deck, group)
        summary = llm.summarize_cluster(members)
        missing = ungrounded_terms(summary, members)
        if missing:
            raise UngroundedSummary(cluster_id, len(missing))
        clusters.append(Cluster(cluster_id=cluster_id, summary=summary, question_ids=group.question_ids))
        claims.append(Claim(reference=_reference(members), statement=summary))
    if partition.other:
        other_id = ClusterId(deck.deck_id, OTHER_LOCAL_ID)
        clusters.append(Cluster(other_id, OTHER_SUMMARY[deck.language], partition.other))
    result = tuple(clusters)
    return emit_if_consistent(llm, claims, lambda: emit(result))
