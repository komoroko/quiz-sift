"""想起キーの語句と、識別ポイントが参照する選択肢の実在性(C-004 / R-4 / A-5 / A-6)。"""

from dataclasses import replace
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.recall import RecallKey
from quiz_sift.recall.generate import RecallRejected, generate_recall_set
from quiz_sift.recall.verify import missing_key_terms, missing_options, referenced_options

from .conftest import GOOD, StubLlm, Writer, question


def test_recall_key_terms_exist_in_source(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    for qid, draft in GOOD.items():
        assert missing_key_terms(draft.key, question(deck, qid)) == (), qid
    q01 = question(deck, "q01")
    # 元の問題に無い語。別クラスタの問題にある語(IAM)も、この問題に無ければ実在しない。
    assert missing_key_terms(RecallKey(("ストレージクラス", "Lambda")), q01) == ("Lambda",)
    assert missing_key_terms(RecallKey(("IAM", "アクセス頻度")), q01) == ("IAM",)
    # 英数字の語は語境界で照合する。「S3」は「S30」に実在しない。
    assert missing_key_terms(RecallKey(("S30",)), q01) == ("S30",)

    out = tmp_path / "recall.tsv"
    llm = StubLlm({**GOOD, "q01": replace(GOOD["q01"], key=RecallKey(("アクセス頻度", "Lambda")))})
    with pytest.raises(RecallRejected) as exc:
        generate_recall_set(llm, deck, clusters, Writer(out))
    assert exc.value.code not in (None, 0)
    assert exc.value.report.ungrounded_keys == 1
    assert "Lambda" not in str(exc.value)
    assert not out.exists()
    assert llm.checked == []

    # 全キーが実在すれば混入は 0 件で書き出される。
    writer = Writer(out)
    generate_recall_set(StubLlm(GOOD), deck, clusters, writer)
    assert writer.result is not None and writer.result.report.ungrounded_keys == 0


def test_referenced_distractors_exist(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    q01 = question(deck, "q01")
    assert referenced_options(GOOD["q01"].discrimination) == ("S3 Glacier Deep Archive",)
    for qid, draft in GOOD.items():
        assert missing_options(draft.discrimination, question(deck, qid)) == (), qid
    # 『』・引用符の括りも参照として読む。正解側を括った対比は実在する選択肢の参照。
    assert missing_options('『S3 標準-IA』は即座に取り出せるが "S3 Glacier Deep Archive" は遅い', q01) == ()
    # 元データに無い選択肢の参照は実在しない。
    phantom = "即座に取り出すなら標準-IA で、「S3 Intelligent-Tiering」は自動で階層を移す"
    assert missing_options(phantom, q01) == ("S3 Intelligent-Tiering",)

    out = tmp_path / "recall.tsv"
    llm = StubLlm({**GOOD, "q01": replace(GOOD["q01"], discrimination=phantom)})
    with pytest.raises(RecallRejected) as exc:
        generate_recall_set(llm, deck, clusters, Writer(out))
    assert exc.value.code not in (None, 0)
    assert exc.value.report.missing_options == 1
    assert "Intelligent" not in str(exc.value)
    assert not out.exists()
    assert llm.checked == []

    writer = Writer(out)
    generate_recall_set(StubLlm(GOOD), deck, clusters, writer)
    assert writer.result is not None and writer.result.report.missing_options == 0
