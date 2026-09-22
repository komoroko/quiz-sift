"""用語説明と公式解説の照合(C-005 / C-013 / R-5 / NFR-1 / A-2 / A-3)。"""

from pathlib import Path

import pytest

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.language import Language
from quiz_sift.glossary.describe import GlossaryEntry
from quiz_sift.glossary.verify import (
    Consistent,
    Contradicted,
    Glossary,
    NoReference,
    emit_glossary,
    verification_summary,
    verify,
)
from quiz_sift.ports.llm import Entailment
from quiz_sift.safety.entailment import EntailmentRejected

# 公式解説(標準-IA はミリ秒単位で取り出せる)と矛盾する説明。語句は正しく、文字列の一致では見分けられない。
CONTRADICTING = "S3 のストレージクラスの1つで、取り出しには数時間かかる"


class StubLlm:
    def __init__(
        self, descriptions: dict[str, str] | None = None, verdicts: dict[str, Entailment] | None = None
    ) -> None:
        self.descriptions = descriptions or {}
        self.verdicts = verdicts or {}
        self.checked: list[tuple[str, str]] = []

    def describe_term(self, term: str, language: Language) -> str:
        return self.descriptions.get(term, f"{term} の説明")

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        self.checked.append((reference, statement))
        for description, verdict in self.verdicts.items():
            if description in statement:
                return verdict
        return Entailment.CONSISTENT


class Writer:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.glossary: Glossary | None = None

    def __call__(self, glossary: Glossary) -> Path:
        self.glossary = glossary
        self.path.write_text("\n".join(f"{e.term}\t{e.description}" for e in glossary.entries), encoding="utf-8")
        return self.path


def test_no_reference_is_not_reported_as_pass(
    deck_without_explanations: Deck, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    llm = StubLlm()
    entries = (GlossaryEntry("S3", "オブジェクトストレージ"),)

    # 照合先が0件なら NoReference で、Consistent にはならない。含意判定も呼ばない。
    verification = verify(llm, entries, deck_without_explanations)
    assert verification == NoReference()
    assert not isinstance(verification, Consistent)
    assert llm.checked == []

    # 照合0件から Consistent / Contradicted は構築できない。
    with pytest.raises(ValueError):
        Consistent(0)
    with pytest.raises(ValueError):
        Contradicted(0)

    # 記録は「照合対象なし」で、合格(consistent)とは書かない。
    summary = verification_summary(verification, len(entries))
    assert "no_reference" in summary and "照合対象なし" in summary
    assert "consistent" not in summary and "合格" not in summary

    # 照合対象なしのまま書き出されるが、成果物にもそう記録され、ログにも残る。
    writer = Writer(tmp_path / "glossary.tsv")
    with caplog.at_level("INFO", logger="quiz_sift.glossary.verify"):
        emit_glossary(llm, deck_without_explanations, writer)
    assert writer.glossary is not None
    assert writer.glossary.verification == NoReference()
    assert "no_reference" in caplog.text
    assert "consistent" not in caplog.text


def test_consistent_counts_checked_terms(deck: Deck) -> None:
    llm = StubLlm()
    entries = (GlossaryEntry("S3", "オブジェクトストレージ"), GlossaryEntry("存在しない語", "説明"))

    # 照合先のある用語だけを数える。
    assert verify(llm, entries, deck) == Consistent(1)
    assert "1/2" in verification_summary(Consistent(1), 2)


def test_glossary_goes_through_entailment(deck: Deck, tmp_path: Path) -> None:
    out = tmp_path / "glossary.tsv"
    llm = StubLlm(
        descriptions={"ストレージクラス": CONTRADICTING},
        verdicts={CONTRADICTING: Entailment.CONTRADICTED},
    )

    with pytest.raises(EntailmentRejected) as exc:
        emit_glossary(llm, deck, Writer(out))
    assert exc.value.verdict is Entailment.CONTRADICTED
    assert exc.value.code not in (None, 0)
    assert not out.exists()

    # 矛盾の検出は含意判定を通っていて、照合先はその用語が現れる問題の公式解説。
    (reference, _) = next(pair for pair in llm.checked if CONTRADICTING in pair[1])
    assert "ミリ秒単位で取り出せる" in reference
    assert "一時的な認証情報" not in reference

    # 含意判定が矛盾を返さなければ、同じ説明でも書き出される — 止めたのは判定器であって文字列比較ではない。
    llm = StubLlm(descriptions={"ストレージクラス": CONTRADICTING})
    emit_glossary(llm, deck, Writer(out))
    assert out.exists()
    assert any(CONTRADICTING in statement for _, statement in llm.checked)


def test_uncertain_stops_without_writing(deck: Deck, tmp_path: Path) -> None:
    out = tmp_path / "glossary.tsv"
    llm = StubLlm(descriptions={"IAM": "権限の仕組み"}, verdicts={"権限の仕組み": Entailment.UNCERTAIN})

    with pytest.raises(EntailmentRejected) as exc:
        emit_glossary(llm, deck, Writer(out))
    assert exc.value.verdict is Entailment.UNCERTAIN
    assert not out.exists()
