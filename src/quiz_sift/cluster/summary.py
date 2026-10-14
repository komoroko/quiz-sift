"""クラスタ要約(R-3 / NFR-1)。要約を作り、2つの検査を通ったときだけ成果物を書く。

1. **語句の実在性**(ADR-010 / D-161): 要約から切り出した専門用語の断片のすべてが、そのクラスタに
   属する問題の本文(問題文・選択肢・解説)のどれかに実在すること。断片が1つでも実在しなければ止める。
   断片は要約の本文から直接切り出す。日本語デッキは英字を含む英数字の語(数字始まりの 42U も語全体)とカタカナ2字以上の列
   (漢字と混ざった句はカタカナの列だけ)、英語デッキは大文字略語と文頭以外の大文字始まりの語。
   漢字だけの語や小文字の一般語は照合しない。専門用語が0件の要約は照合を通り、含意判定だけがかかる。
2. **含意判定**(NFR-1 / D-042): 要約が、そのクラスタの問題の正解・公式解説と矛盾しないこと。

クラスタごとに「要約 → 実在性 → 含意判定」を行い、どちらかに落ちたらそのクラスタの要約だけを上限
`MAX_REGENERATIONS` 回まで作り直す(D-232 / ADR-026)。上限を超えたクラスタが出た時点で、最後に落ちた検査に
応じて `UngroundedSummary` か `EntailmentRejected` で止める。全クラスタが通ったときだけ成果物を書く。
`LlmCallError` は作り直さない(D-101)。

「その他」クラスタは論点を持たないので要約を生成せず、固定のラベルを置く。知識の主張ではないので
どちらの検査にもかけない。
"""

import logging
import re
import unicodedata
from collections.abc import Callable
from typing import Protocol

from quiz_sift.cluster.assign import Partition
from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId
from quiz_sift.domain.language import Language
from quiz_sift.grounding.presence import is_present
from quiz_sift.grounding.targets import SUMMARY_FIELDS, texts_for
from quiz_sift.ports.llm import Entailment, EntailmentStatement, GroupProposal
from quiz_sift.safety.entailment import EntailmentChecker, EntailmentRejected, judge, reference_for

logger = logging.getLogger(__name__)

OTHER_LOCAL_ID = "other"
"""「その他」クラスタの `ClusterId.local_id`。"""
OTHER_SUMMARY = {Language.JA: "その他", Language.EN: "Other"}
"""「その他」クラスタの要約として置く固定のラベル。"""
MAX_REGENERATIONS = 2
"""1クラスタあたりの要約の作り直しの上限。生成は最初の1回と合わせて最大 `1 + MAX_REGENERATIONS` 回(D-232)。"""


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


_ALNUM_WORD = r"[0-9]*[A-Za-z][A-Za-z0-9]*"
"""英字を1字以上含む英数字の連なり全体。数字始まりの語(42U)も末尾の英字だけにせず語全体で取り、数字だけの語は取らない。"""
_JA_TERM = re.compile(rf"{_ALNUM_WORD}|[\u30a1-\u30fa\u30fc]{{2,}}")
_EN_WORD = re.compile(_ALNUM_WORD)
_EN_ACRONYM = re.compile(r"[A-Z][A-Z0-9]+")
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|\n)\W*$")


def _technical_terms(summary: str, language: Language) -> list[str]:
    """`summary` から専門用語の断片を出現順に切り出す(D-161)。"""
    text = unicodedata.normalize("NFKC", summary)
    if language is Language.JA:
        return _JA_TERM.findall(text)
    terms = []
    for match in _EN_WORD.finditer(text):
        word = match.group()
        at_start = _SENTENCE_START.search(text, 0, match.start()) is not None
        if _EN_ACRONYM.fullmatch(word) or (word[0].isupper() and not at_start):
            terms.append(word)
    return terms


def ungrounded_terms(summary: str, members: Deck) -> tuple[str, ...]:
    """`summary` の専門用語の断片のうち、`members` のどの問題の本文にも実在しないもの。"""
    texts = [text for question in members.questions for text in texts_for(question, SUMMARY_FIELDS)]
    return tuple(term for term in _technical_terms(summary, members.language) if not is_present(term, texts))


def _reference(members: Deck) -> str:
    """クラスタ内の各問題の reference を並べる。各問題の正解 N 件をすべて含む(NFR-1)。"""
    return "\n".join(reference_for(question) for question in members.questions)


def _members(deck: Deck, group: GroupProposal) -> Deck:
    wanted = set(group.question_ids)
    return Deck(
        deck_id=deck.deck_id,
        language=deck.language,
        default_studied=deck.default_studied,
        questions=tuple(q for q in deck.questions if q.question_id in wanted),
    )


def _summarize(llm: SummaryLlm, cluster_id: ClusterId, members: Deck) -> str:
    """1クラスタの要約を、実在性と含意判定を通るか上限まで作る。上限を超えたら最後に落ちた検査で止める。"""
    failure: UngroundedSummary | EntailmentRejected | None = None
    for attempt in range(1, MAX_REGENERATIONS + 2):
        if failure is not None:
            check = "grounding" if isinstance(failure, UngroundedSummary) else "entailment"
            logger.info("cluster: クラスタ %s の要約を作り直す(%s; %d 回目)", cluster_id.local_id, check, attempt - 1)
        summary = llm.summarize_cluster(members)
        missing = ungrounded_terms(summary, members)
        if missing:
            failure = UngroundedSummary(cluster_id, len(missing))
            continue
        verdict = judge(llm, _reference(members), [EntailmentStatement("summary", summary)])
        if verdict is Entailment.CONSISTENT:
            return summary
        failure = EntailmentRejected(verdict)
    assert failure is not None
    raise failure


def emit_summarized[T](
    llm: SummaryLlm, deck: Deck, partition: Partition, emit: Callable[[tuple[Cluster, ...]], T]
) -> T:
    """各クラスタを要約し、全クラスタが実在性と含意判定を通ったときだけ `emit` にクラスタ列を渡して書かせる。

    落ちたクラスタだけを上限まで作り直す。上限を超えたクラスタがあれば、最後に落ちた検査に応じて
    `UngroundedSummary` か `EntailmentRejected`。どちらも `emit` を呼ぶ前に上がる。
    """
    clusters: list[Cluster] = []
    for index, group in enumerate(partition.groups, start=1):
        cluster_id = ClusterId(deck.deck_id, f"c{index:03d}")
        summary = _summarize(llm, cluster_id, _members(deck, group))
        clusters.append(Cluster(cluster_id=cluster_id, summary=summary, question_ids=group.question_ids))
    if partition.other:
        other_id = ClusterId(deck.deck_id, OTHER_LOCAL_ID)
        clusters.append(Cluster(other_id, OTHER_SUMMARY[deck.language], partition.other))
    return emit(tuple(clusters))
