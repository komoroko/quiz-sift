"""退化ガードと negative control(C-003 / R-3 / AR-11 / A-2 / A-3)。

ガードの入力はスタブが返した割当なので、既知5グループを返すスタブで緑になるだけでは
赤くなり得ない緑になる。退化スタブ2本(全問1グループ / 全問未分類)で同じガードが赤くなることを
併せて要求する。
"""

import pytest

from quiz_sift.cluster.assign import partition
from quiz_sift.cluster.metrics import MAX_CLUSTER_RATIO, MAX_OTHER_RATIO, degeneracy_violations, measure
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import QuestionId
from quiz_sift.ports.llm import GroupProposal

type Groups = dict[str, tuple[QuestionId, ...]]


def test_known_five_groups_pass_the_guard(known5: tuple[Deck, Groups]) -> None:
    deck, groups = known5
    # フィクスチャの定義: 既知の5グループから成り、各グループが3問以上。
    assert len(groups) == 5
    assert all(len(ids) >= 3 for ids in groups.values())

    metrics = measure(partition(deck, [GroupProposal(label, ids) for label, ids in groups.items()]), 0)

    assert metrics.cluster_count == 5
    assert metrics.max_cluster_ratio <= MAX_CLUSTER_RATIO
    assert metrics.other_ratio <= MAX_OTHER_RATIO
    assert degeneracy_violations(metrics) == ()


def test_degenerate_stubs_go_red(known5: tuple[Deck, Groups]) -> None:
    deck, _ = known5
    everything = tuple(question.question_id for question in deck.questions)

    # 全問を1グループに返すスタブ: 最大クラスタ比率で赤。
    one_group = measure(partition(deck, [GroupProposal("all", everything)]), 0)
    assert one_group.max_cluster_ratio == pytest.approx(1.0)
    assert one_group.other_ratio == 0
    violations = degeneracy_violations(one_group)
    assert any("最大クラスタ比率" in v for v in violations)
    assert not any("その他" in v for v in violations)

    # 全問を未分類で返すスタブ: 「その他」比率で赤。
    unclassified = measure(partition(deck, []), 0)
    assert unclassified.other_ratio == pytest.approx(1.0)
    assert any("「その他」比率" in v for v in degeneracy_violations(unclassified))

    # 閾値ちょうどは通る(境界が「以下」であること)。
    half = len(everything) // 2
    split = partition(deck, [GroupProposal("a", everything[:half]), GroupProposal("b", everything[half:])])
    assert measure(split, 0).max_cluster_ratio == MAX_CLUSTER_RATIO
    assert degeneracy_violations(measure(split, 0)) == ()
