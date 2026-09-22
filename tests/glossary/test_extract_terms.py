"""専門用語の抽出と説明(C-005 / R-5 / A-1)。"""

import pytest

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.language import Language
from quiz_sift.glossary.describe import GlossaryEntry, describe_terms
from quiz_sift.glossary.extract_terms import extract_terms, terms_in


class StubLlm:
    def __init__(self, blank: frozenset[str] = frozenset()) -> None:
        self.blank = blank
        self.described: list[tuple[str, Language]] = []

    def describe_term(self, term: str, language: Language) -> str:
        self.described.append((term, language))
        return "" if term in self.blank else f"{term} の説明"


def test_each_term_gets_one_description(deck: Deck) -> None:
    terms = extract_terms(deck)

    # 略語・英字を含む語句・カタカナ語が専門用語として取れ、漢字だけの一般名詞は取らない。
    assert {"S3", "EC2", "IAM", "ストレージクラス", "インスタンス"} <= set(terms)
    assert not {"方法", "データ向け", "頻度"} & set(terms)
    assert len(terms) == len(set(terms))

    llm = StubLlm()
    entries = describe_terms(llm, terms, deck.language)

    # 各用語に説明がちょうど1件、用語の順で付く。
    assert [e.term for e in entries] == list(terms)
    assert [term for term, _ in llm.described] == list(terms)
    assert all(language is Language.JA for _, language in llm.described)
    assert all(e.description.strip() for e in entries)

    # 空の説明は成果物にならない。
    with pytest.raises(ValueError, match="用語説明が空"):
        describe_terms(StubLlm(blank=frozenset({"S3"})), terms, deck.language)


def test_english_takes_acronyms() -> None:
    assert terms_in("Use an S3 bucket policy to restrict EC2 access via IAM and DynamoDB.", Language.EN) == (
        "S3",
        "EC2",
        "IAM",
        "DynamoDB",
    )


def test_entry_repr_hides_material() -> None:
    assert "S3" not in repr(GlossaryEntry("S3", "オブジェクトストレージ"))
