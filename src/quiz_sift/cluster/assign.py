"""排他分割(R-3 / ADR-003)。LLM の応答を信用せず、ここで排他性と網羅性を強制する。

- 同じラベルのグループは1つに合わせる。分類は分割呼び出しで行われ、別々の呼び出しが
  同じラベルを返す(`existing_labels` を渡している)ので、合わせないと1つの論点が割れる。
- 1問が複数のグループに現れたら、最初に現れたグループにだけ属させる。
- デッキに無い問題 ID は捨てる。どのグループにも属さなかった問題は「その他」に入れる。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import QuestionId
from quiz_sift.ports.llm import GroupProposal


@dataclass(frozen=True, slots=True, repr=False)
class Partition:
    """1デッキの排他分割。`groups` と `other` を合わせると、デッキの全問題をちょうど1回ずつ含む。"""

    groups: tuple[GroupProposal, ...]
    """空のグループは含まない。"""
    other: tuple[QuestionId, ...]
    """どのグループにも属さなかった問題(「その他」クラスタ)。"""

    def __post_init__(self) -> None:
        if any(not group.question_ids for group in self.groups):
            raise ValueError("空のグループがある")
        members = [qid for group in self.groups for qid in group.question_ids] + list(self.other)
        if len(set(members)) != len(members):
            raise ValueError("2つ以上のクラスタに属する問題がある")


def partition(deck: Deck, proposals: Sequence[GroupProposal]) -> Partition:
    """`proposals`(LLM の生の応答)を `deck` の排他分割にする。グループの順序は最初に現れた順。"""
    in_deck = {q.question_id for q in deck.questions}
    assigned: set[QuestionId] = set()
    by_label: dict[str, list[QuestionId]] = {}
    for proposal in proposals:
        members = by_label.setdefault(proposal.label, [])
        for qid in proposal.question_ids:
            if qid in in_deck and qid not in assigned:
                assigned.add(qid)
                members.append(qid)
    groups = tuple(GroupProposal(label, tuple(ids)) for label, ids in by_label.items() if ids)
    other = tuple(q.question_id for q in deck.questions if q.question_id not in assigned)
    return Partition(groups=groups, other=other)
