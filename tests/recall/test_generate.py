"""想起素材の生成ループと含意判定の結線(C-004 / C-013 / R-4 / NFR-1)。"""

from dataclasses import replace
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.ports.llm import Entailment
from quiz_sift.recall.generate import MAX_REGENERATIONS, RecallCallsFailed, RegenerationExhausted
from quiz_sift.safety.entailment import DISTRACTOR_SECTION, FOCUS_SECTION, EntailmentRejected

from .conftest import GOOD, StubLlm, Writer, generate

# 語句も参照もすべて実在するが、正解・解説(標準-IA はミリ秒単位で取り出せる)と矛盾する答え。
CONTRADICTING = "S3 標準-IA の取り出しには数時間かかる"
ORDER = [("c001", "q01"), ("c001", "q02"), ("c002", "q03"), ("c002", "q04"), ("c003", "q05")]


def test_three_elements_are_non_empty(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    writer = Writer(tmp_path / "recall.tsv")
    generate(StubLlm(GOOD), deck, clusters, writer)
    assert writer.result is not None
    materials = writer.result.materials
    # 全クラスタの全問題に1件ずつ、所属クラスタつきで出る。
    assert [(m.cluster_id.local_id, m.question_id.local_id) for m in materials] == ORDER
    for m in materials:
        assert m.draft.key.terms and all(t.strip() for t in m.draft.key.terms)
        assert all(e.text.strip() for e in m.draft.answers)
        assert m.draft.discrimination.text.strip()
    assert len((tmp_path / "recall.tsv").read_text(encoding="utf-8").splitlines()) == len(GOOD)
    assert writer.result.report.regenerations == tuple((m.question_id, 0) for m in materials)

    # どれか1要素でも空白だけなら作り直し、上限を超えたら含意判定より前に非0で止まり、何も書かれない。
    (element,) = GOOD["q03"].answers
    blanks = {
        "key": replace(GOOD["q03"], key=RecallKey(("明示的な拒否", "  "))),
        "answer": replace(GOOD["q03"], answers=(AnswerElement(element.option_id, ""),)),
        "discrimination": replace(
            GOOD["q03"], discrimination=DiscriminationPoint(" \n", GOOD["q03"].discrimination.distractor_ids)
        ),
    }
    for name, draft in blanks.items():
        out = tmp_path / f"blank-{name}.tsv"
        llm = StubLlm({**GOOD, "q03": draft})
        with pytest.raises(RegenerationExhausted) as exc:
            generate(llm, deck, clusters, Writer(out))
        assert exc.value.code not in (None, 0), name
        assert [(e.question_id.local_id, e.attempts) for e in exc.value.exhausted] == [("q03", 1 + MAX_REGENERATIONS)]
        assert "empty" in exc.value.exhausted[0].failures, name
        assert not out.exists(), name
        assert llm.checked == [], name


def test_failed_question_is_regenerated_until_it_passes(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path
) -> None:
    ungrounded = replace(GOOD["q01"], key=RecallKey(("Lambda",)))
    llm = StubLlm(GOOD, sequences={"q01": (ungrounded, ungrounded, GOOD["q01"]), "q04": (ungrounded, GOOD["q04"])})
    writer = Writer(tmp_path / "recall.tsv")
    generate(llm, deck, clusters, writer)
    assert writer.result is not None
    # 落ちた問題だけを作り直し、その回数を問題ごとに記録する。
    assert llm.recalled == ["q01", "q01", "q01", "q02", "q03", "q04", "q04", "q05"]
    assert {q.local_id: n for q, n in writer.result.report.regenerations} == {
        "q01": 2,
        "q02": 0,
        "q03": 0,
        "q04": 1,
        "q05": 0,
    }
    assert writer.result.report.passed
    # 書き出されるのは最後に検査を通った素材。
    assert [m.draft for m in writer.result.materials] == list(GOOD.values())


def test_exhausted_questions_are_all_listed_after_every_question(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path
) -> None:
    out = tmp_path / "recall.tsv"
    llm = StubLlm(
        {
            **GOOD,
            "q01": replace(GOOD["q01"], key=RecallKey(("Lambda",))),
            "q04": replace(GOOD["q04"], discrimination=DiscriminationPoint("ロールを使う", ())),
        }
    )
    with pytest.raises(RegenerationExhausted) as exc:
        generate(llm, deck, clusters, Writer(out))
    # 上限を超えた q01 の後も残りの問題を回し切り、超えた問題をすべて列挙する。
    assert llm.recalled == ["q01"] * 3 + ["q02", "q03"] + ["q04"] * 3 + ["q05"]
    assert [(e.question_id.local_id, e.failures, e.attempts) for e in exc.value.exhausted] == [
        ("q01", ("ungrounded_key",), 3),
        ("q04", ("missing_options",), 3),
    ]
    message = str(exc.value.code)
    assert "q01(ungrounded_key; 3 回)" in message and "q04(missing_options; 3 回)" in message
    assert "Lambda" not in message and "ロール" not in message
    assert not out.exists()
    assert llm.checked == []


def test_call_failures_are_not_regenerated(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    out = tmp_path / "recall.tsv"
    llm = StubLlm(GOOD, failing=frozenset({"q02", "q04"}))
    with pytest.raises(RecallCallsFailed) as exc:
        generate(llm, deck, clusters, Writer(out))
    # 失敗した問題は1回だけ呼ばれ、残りの問題は回し切ってから止まる。
    assert llm.recalled == [qid for _, qid in ORDER]
    assert "失敗 2 回、発行 5 / 5" in str(exc.value.code)
    assert not out.exists()
    assert llm.checked == []


def test_stop_seen_issues_no_more_calls(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    out = tmp_path / "recall.tsv"
    llm = StubLlm(GOOD, sequences={"q02": (replace(GOOD["q02"], key=RecallKey(("Lambda",))), GOOD["q02"])})
    # q02 の1回目の後に、overage か枠切れを見たことにする。作り直しも含めて残りを発行しない。
    with pytest.raises(RecallCallsFailed) as exc:
        generate(llm, deck, clusters, Writer(out), stop_seen=lambda: len(llm.recalled) >= 2)
    assert llm.recalled == ["q01", "q02"]
    assert "失敗 0 回、発行 1 / 5" in str(exc.value.code)
    assert not out.exists()


def test_recall_goes_through_entailment(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    out = tmp_path / "recall.tsv"
    (element,) = GOOD["q01"].answers
    llm = StubLlm(
        {**GOOD, "q01": replace(GOOD["q01"], answers=(AnswerElement(element.option_id, CONTRADICTING),))},
        contradicted=frozenset({CONTRADICTING}),
    )
    with pytest.raises(EntailmentRejected) as exc:
        generate(llm, deck, clusters, Writer(out))
    assert exc.value.verdict is Entailment.CONTRADICTED
    assert exc.value.code not in (None, 0)
    assert not out.exists()
    # 答えの要素は、その問題の正解・公式解説と、要素が説明する正解を照合先として判定されている。
    reference = next(ref for ref, statement in llm.checked if statement == CONTRADICTING)
    assert "正解: S3 標準-IA" in reference and "ミリ秒単位で取り出せる" in reference
    assert f"{FOCUS_SECTION}: S3 標準-IA" in reference
    assert "IAM" not in reference
    # 含意判定の不合格は作り直さない。
    assert llm.recalled == [qid for _, qid in ORDER]

    # 識別ポイントも照合される。判定できない(uncertain)ものも書かずに止める(D-042)。
    unsure = GOOD["q05"].discrimination.text
    with pytest.raises(EntailmentRejected) as exc:
        generate(StubLlm(GOOD, uncertain=frozenset({unsure})), deck, clusters, Writer(out))
    assert exc.value.verdict is Entailment.UNCERTAIN
    assert not out.exists()

    # 矛盾が無ければ、全素材の答えの要素と識別ポイントが1件ずつ判定されてから書き出される。
    llm = StubLlm(GOOD)
    generate(llm, deck, clusters, Writer(out))
    assert out.exists()
    expected = [s for d in GOOD.values() for s in (*(e.text for e in d.answers), d.discrimination.text)]
    assert sorted(statement for _, statement in llm.checked) == sorted(expected)
    # 識別ポイントの reference は参照先の誤答を誤答の節に置く(q05 は B = リードレプリカ)。
    reference = next(ref for ref, statement in llm.checked if statement == unsure)
    assert f"{DISTRACTOR_SECTION}: リードレプリカ" in reference
    assert "スナップショット" not in reference
