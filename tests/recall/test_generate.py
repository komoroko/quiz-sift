"""想起素材の3要素と含意判定の結線(C-004 / C-013 / R-4 / NFR-1 / A-1 / A-7)。"""

from dataclasses import replace
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.recall import RecallKey
from quiz_sift.ports.llm import Entailment
from quiz_sift.recall.generate import EmptyRecallElement, generate_recall_set
from quiz_sift.safety.entailment import EntailmentRejected

from .conftest import GOOD, StubLlm, Writer

# 語句も参照もすべて実在するが、正解・解説(標準-IA はミリ秒単位で取り出せる)と矛盾する答え。
CONTRADICTING = "S3 標準-IA の取り出しには数時間かかる"


def test_three_elements_are_non_empty(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    writer = Writer(tmp_path / "recall.tsv")
    generate_recall_set(StubLlm(GOOD), deck, clusters, writer)
    assert writer.result is not None
    materials = writer.result.materials
    # 全クラスタの全問題に1件ずつ、所属クラスタつきで出る。
    assert [(m.cluster_id.local_id, m.question_id.local_id) for m in materials] == [
        ("c001", "q01"),
        ("c001", "q02"),
        ("c002", "q03"),
        ("c002", "q04"),
        ("c003", "q05"),
    ]
    for m in materials:
        assert m.draft.key.terms and all(t.strip() for t in m.draft.key.terms)
        assert m.draft.answer.strip()
        assert m.draft.discrimination.strip()
    assert len((tmp_path / "recall.tsv").read_text(encoding="utf-8").splitlines()) == len(GOOD)

    # どれか1要素でも空白だけなら、含意判定より前に非0で止まり、何も書かれない。
    blanks = {
        "key": replace(GOOD["q03"], key=RecallKey(("明示的な拒否", "  "))),
        "answer": replace(GOOD["q03"], answer=""),
        "discrimination": replace(GOOD["q03"], discrimination=" \n"),
    }
    for element, draft in blanks.items():
        out = tmp_path / f"blank-{element}.tsv"
        llm = StubLlm({**GOOD, "q03": draft})
        with pytest.raises(EmptyRecallElement) as exc:
            generate_recall_set(llm, deck, clusters, Writer(out))
        assert exc.value.code not in (None, 0), element
        assert exc.value.question_id.local_id == "q03"
        assert not out.exists(), element
        assert llm.checked == [], element


def test_recall_goes_through_entailment(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    out = tmp_path / "recall.tsv"
    llm = StubLlm({**GOOD, "q01": replace(GOOD["q01"], answer=CONTRADICTING)}, contradicted=frozenset({CONTRADICTING}))
    with pytest.raises(EntailmentRejected) as exc:
        generate_recall_set(llm, deck, clusters, Writer(out))
    assert exc.value.verdict is Entailment.CONTRADICTED
    assert exc.value.code not in (None, 0)
    assert not out.exists()
    # 答えはその問題の正解・公式解説を照合先として判定されている。
    reference = next(ref for ref, statement in llm.checked if statement == CONTRADICTING)
    assert "正解: S3 標準-IA" in reference and "ミリ秒単位で取り出せる" in reference
    assert "IAM" not in reference

    # 識別ポイントも照合される。判定できない(uncertain)ものも書かずに止める(D-042)。
    unsure = GOOD["q05"].discrimination
    with pytest.raises(EntailmentRejected) as exc:
        generate_recall_set(StubLlm(GOOD, uncertain=frozenset({unsure})), deck, clusters, Writer(out))
    assert exc.value.verdict is Entailment.UNCERTAIN
    assert not out.exists()

    # 矛盾が無ければ、全素材の答えと識別ポイントが1件ずつ判定されてから書き出される。
    llm = StubLlm(GOOD)
    generate_recall_set(llm, deck, clusters, Writer(out))
    assert out.exists()
    expected = [s for d in GOOD.values() for s in (d.answer, d.discrimination)]
    assert sorted(statement for _, statement in llm.checked) == sorted(expected)
