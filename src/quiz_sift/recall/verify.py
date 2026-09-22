"""想起素材の検査(R-4 / ADR-010)。すべて純粋関数で、LLM を呼ばない。

- **語句の実在性**: 想起キーの全 term が元の問題の本文(問題文・選択肢・解説)に実在すること。
- **参照する誤答選択肢の実在性**: 識別ポイントが鉤括弧・引用符で括って参照した語句が、元の問題の
  選択肢のどれかに実在すること。正解と誤答の対比で正解側を括ることはあるので、正解の選択肢も
  照合先に含める。どの選択肢にも無い語句を括っていれば、存在しない選択肢を参照している。
- **重複禁止**: キーは `frozenset(terms)` で比べる(AR-25)。tuple で比べると並べ替えただけのキーが
  別物になる。
- **汎用語だけのキーの禁止**: 各 term の他クラスタ出現率(自クラスタ以外で出現するクラスタ数 /
  全クラスタ数)を記録し、全 term が他クラスタにも出現するキーを数える。
- **丸写しの禁止**(AR-12): 識別ポイントのうち、**全**誤答選択肢との一致区間の和を除いた残りが
  全体の 30% 以上あること。1件ずつ比べると、2件を貼り合わせた識別ポイントがどちらとも半分しか
  一致せずに通ってしまう。
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from quiz_sift.domain.ids import ClusterId, QuestionId
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import RecallKey
from quiz_sift.grounding.presence import is_present
from quiz_sift.ports.llm import RecallDraft

MIN_ORIGINAL_RATIO = 0.30
"""識別ポイントのうち、誤答選択肢のどれとも一致しない部分が占めるべき最低割合。"""

# 識別ポイントが選択肢を参照するときの括り。日本語の鉤括弧と英語の引用符。
_QUOTED = re.compile(r"『([^』]+)』|「([^」]+)」|“([^”]+)”|\"([^\"]+)\"")


def question_texts(question: Question) -> list[str]:
    """問題の本文 — 問題文・選択肢・解説。"""
    texts = [question.stem, *(option.text for option in question.options)]
    if question.explanation is not None:
        texts.append(question.explanation)
    return texts


def distractors(question: Question) -> tuple[str, ...]:
    """誤答選択肢の本文。"""
    return tuple(o.text for o in question.options if o.option_id != question.correct_option_id)


def missing_key_terms(key: RecallKey, question: Question) -> tuple[str, ...]:
    """`key` の term のうち、元の問題の本文に実在しないもの。"""
    texts = question_texts(question)
    return tuple(term for term in key.terms if not is_present(term, texts))


def referenced_options(discrimination: str) -> tuple[str, ...]:
    """識別ポイントが括って参照している語句を出現順に返す。"""
    return tuple(next(g for g in m.groups() if g is not None) for m in _QUOTED.finditer(discrimination))


def missing_options(discrimination: str, question: Question) -> tuple[str, ...]:
    """識別ポイントが参照する語句のうち、元の問題の選択肢のどれにも実在しないもの。"""
    options = [option.text for option in question.options]
    return tuple(ref for ref in referenced_options(discrimination) if not is_present(ref, options))


def duplicate_count(keys: Sequence[RecallKey]) -> int:
    """重複するキーの数(`frozenset(terms)` が既出のもの)。0 なら重複は無い。"""
    return len(keys) - len({key.term_set for key in keys})


def other_cluster_rates(
    key: RecallKey, own: ClusterId, cluster_texts: Mapping[ClusterId, Sequence[str]]
) -> dict[str, float]:
    """`key` の各 term について、自クラスタ以外で出現するクラスタ数 / 全クラスタ数。"""
    others = [texts for cluster_id, texts in cluster_texts.items() if cluster_id != own]
    return {term: sum(is_present(term, texts) for texts in others) / len(cluster_texts) for term in key.terms}


def is_generic(rates: Mapping[str, float]) -> bool:
    """全 term が他クラスタにも出現するか。"""
    return all(rate > 0 for rate in rates.values())


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def original_ratio(discrimination: str, wrong: Sequence[str]) -> float:
    """識別ポイントのうち、`wrong`(全誤答選択肢)との一致区間の和に含まれない文字の割合。"""
    text = _normalize(discrimination)
    if not text:
        return 0.0
    covered = [False] * len(text)
    for option in wrong:
        matcher = SequenceMatcher(None, text, _normalize(option), autojunk=False)
        for block in matcher.get_matching_blocks():
            covered[block.a : block.a + block.size] = [True] * block.size
    return covered.count(False) / len(text)


def is_verbatim(discrimination: str, question: Question) -> bool:
    """識別ポイントが誤答選択肢の丸写しか。"""
    return original_ratio(discrimination, distractors(question)) < MIN_ORIGINAL_RATIO


@dataclass(frozen=True, slots=True, repr=False)
class RecallMaterial:
    """1問分の想起素材。どのクラスタのどの問題のものかを持つ。"""

    cluster_id: ClusterId
    question_id: QuestionId
    draft: RecallDraft


@dataclass(frozen=True, slots=True, repr=False)
class TermRate:
    """想起キーの1 term の他クラスタ出現率。"""

    question_id: QuestionId
    term: str
    rate: float


@dataclass(frozen=True, slots=True)
class RecallReport:
    """検査の結果。件数はすべて 0 でなければならない。`term_rates` は合否に関わらず記録する。"""

    ungrounded_keys: int
    """元の問題文に実在しない term を含むキーの数。"""
    missing_options: int
    """実在しない選択肢を参照する識別ポイントの数。"""
    duplicate_keys: int
    generic_keys: int
    """全 term が他クラスタにも出現するキーの数。"""
    verbatim: int
    """誤答選択肢の丸写しである識別ポイントの数。"""
    term_rates: tuple[TermRate, ...] = ()

    @property
    def passed(self) -> bool:
        return not (
            self.ungrounded_keys or self.missing_options or self.duplicate_keys or self.generic_keys or self.verbatim
        )


def verify(
    materials: Sequence[RecallMaterial],
    questions: Mapping[QuestionId, Question],
    cluster_texts: Mapping[ClusterId, Sequence[str]],
) -> RecallReport:
    """全素材を検査する。`cluster_texts` は各クラスタに属する問題の本文。"""
    rates: list[TermRate] = []
    generic = 0
    for material in materials:
        key_rates = other_cluster_rates(material.draft.key, material.cluster_id, cluster_texts)
        rates.extend(TermRate(material.question_id, term, rate) for term, rate in key_rates.items())
        generic += is_generic(key_rates)
    pairs = [(m.draft, questions[m.question_id]) for m in materials]
    return RecallReport(
        ungrounded_keys=sum(bool(missing_key_terms(d.key, q)) for d, q in pairs),
        missing_options=sum(bool(missing_options(d.discrimination, q)) for d, q in pairs),
        duplicate_keys=duplicate_count([m.draft.key for m in materials]),
        generic_keys=generic,
        verbatim=sum(is_verbatim(d.discrimination, q) for d, q in pairs),
        term_rates=tuple(rates),
    )
