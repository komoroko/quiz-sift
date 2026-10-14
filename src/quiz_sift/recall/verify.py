"""想起素材の検査(R-4 / ADR-010 / ADR-022)。すべて純粋関数で、LLM を呼ばない。

検査は1問単位で、落ちた問題だけを `recall/generate` が作り直す。照合先は `grounding/targets` の定数からしか
作らない。

- **空要素**(`empty`): 想起キーの term・答えの要素・識別ポイントのどれかが空白だけ。
- **語句の実在性**(`ungrounded_key`): 想起キーの全 term が、その問題の問題文に実在すること(`RECALL_KEY_FIELDS`)。
- **答えの対応**(`answer_mismatch`): 答えの要素が持つ選択肢 ID が重複なく、その集合が正解の集合と一致すること。
- **参照する誤答選択肢**(`missing_options`): 識別ポイントの `distractor_ids` が1件以上で、すべてその問題の
  誤答選択肢であること。本文の括弧は参照とみなさない(D-177)。
- **汎用語だけのキーの禁止**(`generic`): 各 term の他クラスタ出現率(自クラスタ以外で出現するクラスタ数 /
  全クラスタ数、照合先は `OTHER_CLUSTER_FIELDS`)を記録し、全 term が他クラスタにも出現するキーを落とす。
- **丸写しの禁止**(`verbatim`、AR-12): 識別ポイントのうち、**全**誤答選択肢との一致区間の和を除いた残りが
  全体の 30% 以上あること。1件ずつ比べると、2件を貼り合わせた識別ポイントがどちらとも半分しか
  一致せずに通ってしまう。
- **重複禁止**(`duplicate_key`): 問題をまたぐ唯一の検査。キーは `frozenset(terms)` で比べ(AR-25)、
  **異なるクラスタ**の素材の間だけで数える(D-200)。クラスタ順で後に来た問題を落ちとする。
"""

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final

from quiz_sift.domain.ids import ClusterId, OptionId, QuestionId
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import RecallKey
from quiz_sift.grounding.presence import is_present
from quiz_sift.grounding.targets import OTHER_CLUSTER_FIELDS, RECALL_KEY_FIELDS, QuestionField, texts_for
from quiz_sift.ports.llm import Problem, RecallDraft

MIN_ORIGINAL_RATIO = 0.30
"""識別ポイントのうち、誤答選択肢のどれとも一致しない部分が占めるべき最低割合。"""

EMPTY: Final = "empty"
UNGROUNDED_KEY: Final = "ungrounded_key"
ANSWER_MISMATCH: Final = "answer_mismatch"
MISSING_OPTIONS: Final = "missing_options"
GENERIC: Final = "generic"
VERBATIM: Final = "verbatim"
DUPLICATE_KEY: Final = "duplicate_key"


def distractors(question: Question) -> tuple[str, ...]:
    """誤答選択肢の本文。"""
    return tuple(o.text for o in question.options if o.option_id not in question.correct_option_ids)


def missing_key_terms(
    key: RecallKey, question: Question, fields: tuple[QuestionField, ...] = RECALL_KEY_FIELDS
) -> tuple[str, ...]:
    """`key` の term のうち、`question` の `fields` の部分に実在しないもの。"""
    texts = texts_for(question, fields)
    return tuple(term for term in key.terms if not is_present(term, texts))


def empty_elements(draft: RecallDraft) -> tuple[str, ...]:
    """空白だけの要素の位置(`terms[i]`・`answers[i]`・`discrimination`)。"""
    texts = (
        *((f"terms[{i}]", term) for i, term in enumerate(draft.key.terms)),
        *((f"answers[{i}]", element.text) for i, element in enumerate(draft.answers)),
        ("discrimination", draft.discrimination.text),
    )
    return tuple(name for name, text in texts if not text.strip())


def mismatched_answers(draft: RecallDraft, question: Question) -> tuple[OptionId, ...]:
    """答えの要素の選択肢 ID と正解の集合の食い違い: 重複した ID、正解でない ID、答えに無い正解の ID。"""
    ids = [element.option_id for element in draft.answers]
    duplicated = [option_id for i, option_id in enumerate(ids) if option_id in ids[:i]]
    extra = [option_id for option_id in ids if option_id not in question.correct_option_ids]
    lacking = [option_id for option_id in question.correct_option_ids if option_id not in ids]
    return tuple(dict.fromkeys((*duplicated, *extra, *lacking)))


def misreferenced_options(draft: RecallDraft, question: Question) -> tuple[OptionId, ...]:
    """識別ポイントが参照する ID のうち、その問題の誤答選択肢でないもの。"""
    wrong = {o.option_id for o in question.options} - set(question.correct_option_ids)
    return tuple(option_id for option_id in draft.discrimination.distractor_ids if option_id not in wrong)


def missing_options(draft: RecallDraft, question: Question) -> bool:
    """識別ポイントが誤答選択肢を1件も参照しないか、誤答選択肢でない ID を参照しているか。"""
    return not draft.discrimination.distractor_ids or bool(misreferenced_options(draft, question))


def cluster_texts(
    clusters: Mapping[ClusterId, Sequence[QuestionId]], questions: Mapping[QuestionId, Question]
) -> dict[ClusterId, list[str]]:
    """各クラスタに属する問題の本文(`OTHER_CLUSTER_FIELDS`)。他クラスタ出現率の照合先。"""
    return {
        cluster_id: [text for qid in qids for text in texts_for(questions[qid], OTHER_CLUSTER_FIELDS)]
        for cluster_id, qids in clusters.items()
    }


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


def is_duplicate(material: RecallMaterial, earlier: Sequence[RecallMaterial]) -> bool:
    """`material` のキーが、`earlier`(クラスタ順で前の素材)のうち**別クラスタ**のものと同じ組か(D-200)。"""
    terms = material.draft.key.term_set
    return any(m.cluster_id != material.cluster_id and m.draft.key.term_set == terms for m in earlier)


@dataclass(frozen=True, slots=True, repr=False)
class TermRate:
    """想起キーの1 term の他クラスタ出現率。"""

    question_id: QuestionId
    term: str
    rate: float


@dataclass(frozen=True, slots=True, repr=False)
class QuestionCheck:
    """1問分の検査の結果。`failures` は落ちた検査の名前(空なら合格)。`rates` は合否に関わらず記録する。

    `problems` は落ちた検査ごとの対象で、作り直しの呼び出しに渡す(D-245)。成果物と途中結果の通過・失格の記録は
    名前だけを持つので、そこから読み戻した結果では空になる。
    """

    failures: tuple[str, ...]
    rates: tuple[TermRate, ...]
    problems: tuple[Problem, ...] = ()


def check_question(
    material: RecallMaterial,
    question: Question,
    cluster_texts: Mapping[ClusterId, Sequence[str]],
    earlier: Sequence[RecallMaterial],
) -> QuestionCheck:
    """1問分の素材を検査する。`earlier` はクラスタ順で前の問題の素材(重複の判定にだけ使う)。"""
    draft = material.draft
    key_rates = other_cluster_rates(draft.key, material.cluster_id, cluster_texts)
    terms = draft.key.terms
    empty = empty_elements(draft)
    ungrounded = missing_key_terms(draft.key, question)
    mismatched = mismatched_answers(draft, question)
    checks = (
        (EMPTY, bool(empty), Problem(EMPTY, elements=empty)),
        (UNGROUNDED_KEY, bool(ungrounded), Problem(UNGROUNDED_KEY, ungrounded)),
        (ANSWER_MISMATCH, bool(mismatched), Problem(ANSWER_MISMATCH, options=mismatched)),
        (
            MISSING_OPTIONS,
            missing_options(draft, question),
            Problem(MISSING_OPTIONS, options=misreferenced_options(draft, question)),
        ),
        (GENERIC, is_generic(key_rates), Problem(GENERIC, terms)),
        (VERBATIM, is_verbatim(draft.discrimination.text, question), Problem(VERBATIM)),
        (DUPLICATE_KEY, is_duplicate(material, earlier), Problem(DUPLICATE_KEY, terms)),
    )
    failed = [(name, problem) for name, failed, problem in checks if failed]
    return QuestionCheck(
        failures=tuple(name for name, _ in failed),
        rates=tuple(TermRate(material.question_id, term, rate) for term, rate in key_rates.items()),
        problems=tuple(problem for _, problem in failed),
    )


@dataclass(frozen=True, slots=True)
class RecallReport:
    """成果物に載せる検査の結果。件数は最終の素材についての数で、書き出された成果物ではすべて 0。

    `term_rates` は合否に関わらず記録する。`regenerations` は問題ごとの作り直しの回数(D-219)。
    """

    empty: int
    ungrounded_keys: int
    """元の問題文に実在しない term を含むキーの数。"""
    answer_mismatch: int
    missing_options: int
    """誤答選択肢を正しく参照しない識別ポイントの数。"""
    duplicate_keys: int
    generic_keys: int
    """全 term が他クラスタにも出現するキーの数。"""
    verbatim: int
    """誤答選択肢の丸写しである識別ポイントの数。"""
    term_rates: tuple[TermRate, ...] = ()
    regenerations: tuple[tuple[QuestionId, int], ...] = ()

    @property
    def passed(self) -> bool:
        return not (
            self.empty
            or self.ungrounded_keys
            or self.answer_mismatch
            or self.missing_options
            or self.duplicate_keys
            or self.generic_keys
            or self.verbatim
        )


def report(checks: Sequence[QuestionCheck], regenerations: Mapping[QuestionId, int]) -> RecallReport:
    """1問ごとの検査の結果と、問題ごとの作り直しの回数を集める。"""
    failures = [name for check in checks for name in check.failures]
    return RecallReport(
        empty=failures.count(EMPTY),
        ungrounded_keys=failures.count(UNGROUNDED_KEY),
        answer_mismatch=failures.count(ANSWER_MISMATCH),
        missing_options=failures.count(MISSING_OPTIONS),
        duplicate_keys=failures.count(DUPLICATE_KEY),
        generic_keys=failures.count(GENERIC),
        verbatim=failures.count(VERBATIM),
        term_rates=tuple(rate for check in checks for rate in check.rates),
        regenerations=tuple(regenerations.items()),
    )
