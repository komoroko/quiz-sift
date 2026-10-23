"""名詞句の切り出し(ADR-010 / D-056)。

日本語は fugashi + unidic-lite の形態素解析で名詞の連続を1語句として取る。ただし形態素解析は
`S3` を `S` と `3` に、`EC2` を `EC` と `2` に割る。AWS のサービス名はこの形が主なので、
大文字略語は形態素解析より先にヒューリスティックで拾い、その区間に掛かる形態素は捨てる。

英語は形態素解析を使わず、単語境界で割って機能語を除く。

入力は NFKC で正規化してから切り出すので、全角の `ＥＣ２` も `EC2` として返る。
"""

import re
import unicodedata
from functools import cache

import fugashi

from quiz_sift.domain.language import Language

# 英数字の連続。大文字で始まり、数字を含むか大文字を2つ以上含むものを略語とみなす(S3, EC2, IAM)。
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]+")
_EN_WORD = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
# 語句として意味を持たない英語の機能語。
_EN_FUNCTION_WORDS = frozenset(
    """
    a an the and or but nor not no of to in on at by for from with as into onto about over under
    is are was were be been being am do does did have has had can could will would shall should may
    might must this that these those it its it's which what who whom whose when where why how
    if then than so such each any all some both either neither you your we our they their he his
    she her i me my
    """.split()
)


def _is_acronym(word: str) -> bool:
    if len(word) < 2 or not word[0].isupper():
        return False
    return any(c.isdigit() for c in word) or sum(c.isupper() for c in word) >= 2


def acronyms(text: str) -> tuple[str, ...]:
    """大文字略語を出現順に返す。重複は除く。"""
    return tuple(dict.fromkeys(_acronym_spans(unicodedata.normalize("NFKC", text))))


def _acronym_spans(text: str) -> dict[str, list[tuple[int, int]]]:
    spans: dict[str, list[tuple[int, int]]] = {}
    for m in _ALNUM_RUN.finditer(text):
        if _is_acronym(m.group()):
            spans.setdefault(m.group(), []).append(m.span())
    return spans


@cache
def _tagger() -> fugashi.Tagger:
    return fugashi.Tagger()


def _japanese_terms(text: str) -> list[str]:
    covered = [span for spans in _acronym_spans(text).values() for span in spans]
    terms: list[tuple[int, str]] = [(start, text[start:end]) for start, end in covered]

    run_start = -1
    run: list[str] = []
    all_numeric = True

    def flush() -> None:
        # 数詞だけの連続(「3」)は語句にしない。
        if run and not all_numeric:
            terms.append((run_start, "".join(run)))

    offset = 0
    for word in _tagger()(text):
        start = text.find(word.surface, offset)
        end = start + len(word.surface)
        offset = end
        is_noun = word.feature.pos1 == "名詞" and not any(s < end and start < e for s, e in covered)
        # 空白を挟む名詞は別の語句にする(`Amazon EventBridge` → `Amazon`, `EventBridge`)。
        if run and (not is_noun or word.white_space):
            flush()
            run = []
        if is_noun:
            if not run:
                run_start, all_numeric = start, True
            run.append(word.surface)
            all_numeric = all_numeric and word.feature.pos2 == "数詞"
    flush()
    return [term for _, term in sorted(terms)]


def _english_terms(text: str) -> list[str]:
    return [w for w in _EN_WORD.findall(text) if w.casefold() not in _EN_FUNCTION_WORDS]


def extract_terms(text: str, language: Language) -> tuple[str, ...]:
    """`text` の名詞句(英語は内容語)を出現順に返す。重複は除く。"""
    normalized = unicodedata.normalize("NFKC", text)
    if language is Language.JA:
        found = _japanese_terms(normalized)
    else:
        found = _english_terms(normalized)
    return tuple(dict.fromkeys(found))
