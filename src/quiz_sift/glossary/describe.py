"""用語説明の生成(R-5)。各用語に説明を1件だけ付ける。"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from quiz_sift.domain.language import Language


class DescribeLlm(Protocol):
    """`ports.llm.Llm` のうち用語説明だけを使う。`Llm` の実装はそのまま渡せる。"""

    def describe_term(self, term: str, language: Language) -> str: ...


@dataclass(frozen=True, slots=True, repr=False)
class GlossaryEntry:
    """用語1件とその説明。教材由来の語句を含むので `repr` に出さない(NFR-2)。"""

    term: str
    description: str

    def __post_init__(self) -> None:
        if not self.term.strip():
            raise ValueError("用語が空")
        if not self.description.strip():
            # 語句そのものはメッセージに載せない(NFR-2)。
            raise ValueError("用語説明が空")


def describe_terms(llm: DescribeLlm, terms: Sequence[str], language: Language) -> tuple[GlossaryEntry, ...]:
    """各用語の説明を `language` で1件ずつ作る。空の説明が返れば `ValueError`。"""
    if len(set(terms)) != len(terms):
        raise ValueError("用語が重複している")
    return tuple(GlossaryEntry(term, llm.describe_term(term, language)) for term in terms)
