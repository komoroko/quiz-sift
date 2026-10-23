"""専門用語の抽出(R-5 / ADR-010)。

名詞句の切り出しは `grounding.terms` と共有する。そのうえで専門用語らしいものだけを残す:

- 日本語: 英字を含む語句(S3, IAM ロール, EventBridge)と、カタカナが2字以上続く語句
  (ストレージクラス)。漢字だけの一般名詞(場合、方法)は残さない。
- 英語: 大文字略語(S3, EC2, IAM, DynamoDB)。英語の内容語は一般語と区別できないので採らない。
"""

import re

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.grounding.terms import acronyms
from quiz_sift.grounding.terms import extract_terms as noun_phrases

_ASCII_LETTER = re.compile(r"[A-Za-z]")
_KATAKANA_RUN = re.compile(r"[ァ-ヺー]{2,}")


def _is_technical_ja(term: str) -> bool:
    return bool(_ASCII_LETTER.search(term) or _KATAKANA_RUN.search(term))


def _texts(question: Question) -> list[str]:
    texts = [question.stem, *(option.text for option in question.options)]
    if question.explanation is not None:
        texts.append(question.explanation)
    return texts


def terms_in(text: str, language: Language) -> tuple[str, ...]:
    """`text` の専門用語を出現順に返す。重複は除く。"""
    if language is Language.EN:
        return acronyms(text)
    return tuple(term for term in noun_phrases(text, language) if _is_technical_ja(term))


def extract_terms(deck: Deck) -> tuple[str, ...]:
    """デッキの問題文・選択肢・解説に現れる専門用語を出現順に返す。重複は除く。"""
    found = (term for q in deck.questions for text in _texts(q) for term in terms_in(text, deck.language))
    return tuple(dict.fromkeys(found))
