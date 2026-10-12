"""用語説明と公式解説の照合(R-5 / NFR-1)。矛盾が無いときだけグロッサリを書く。

照合結果は3値 `Consistent(n) | Contradicted(n) | NoReference` で、**`NoReference` を
`Consistent` に潰す経路を型で塞ぐ**:

- `Consistent` は照合件数 `checked >= 1` でなければ構築できない。照合0件から「矛盾0件」は作れない。
- 3値に共通の「合格か」を問う属性や `bool` 変換を置かない。呼び出し側は `match` で3値を分けるしかない。
- 成果物 `Glossary` の `verification` は `Consistent | NoReference` で、`Contradicted` は書き出しに届かない。

照合先は、用語が実在する問題の公式解説(`grounding.presence`)。判定は `safety.entailment` に委ねる
(文字列比較は言い換えを矛盾と見なすので採らない)。`uncertain` は `contradicted` と同じく止める(D-042)。

`consistent` でなかった用語は、その用語の説明だけを上限 `MAX_REGENERATIONS` 回まで作り直して判定し直す
(D-232 / ADR-026)。全用語を判定してから、上限を超えた用語の判定値を集約する。`LlmCallError` は作り直さない(D-101)。
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, assert_never

from quiz_sift.domain.deck import Deck
from quiz_sift.glossary.describe import DescribeLlm, GlossaryEntry, describe_terms
from quiz_sift.glossary.extract_terms import extract_terms
from quiz_sift.grounding.presence import is_present
from quiz_sift.ports.llm import Entailment
from quiz_sift.safety.entailment import Claim, EntailmentChecker, EntailmentRejected, judge

logger = logging.getLogger(__name__)

MAX_REGENERATIONS = 2
"""1用語あたりの説明の作り直しの上限。生成は最初の1回と合わせて最大 `1 + MAX_REGENERATIONS` 回(D-232)。"""


@dataclass(frozen=True, slots=True)
class Consistent:
    """照合した説明がすべて公式解説と矛盾しなかった。"""

    checked: int

    def __post_init__(self) -> None:
        if self.checked < 1:
            raise ValueError("照合0件は Consistent ではなく NoReference")


@dataclass(frozen=True, slots=True)
class Contradicted:
    """公式解説と矛盾する説明が `count` 件あった。"""

    count: int

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("矛盾0件は Contradicted ではない")


@dataclass(frozen=True, slots=True)
class NoReference:
    """照合先の公式解説が1件も無かった。合格ではない。"""


type Verification = Consistent | Contradicted | NoReference


class GlossaryLlm(DescribeLlm, EntailmentChecker, Protocol):
    """`ports.llm.Llm` のうち用語説明と含意判定だけを使う。`Llm` の実装はそのまま渡せる。"""


@dataclass(frozen=True, slots=True, repr=False)
class Glossary:
    """グロッサリの成果物。矛盾した照合結果は型の上でここに入らない。"""

    entries: tuple[GlossaryEntry, ...]
    verification: Consistent | NoReference


def _claim(entry: GlossaryEntry, deck: Deck) -> Claim | None:
    explanations = [
        q.explanation
        for q in deck.questions
        if q.explanation is not None and is_present(entry.term, [q.stem, *(o.text for o in q.options), q.explanation])
    ]
    if not explanations:
        return None
    return Claim(reference="\n".join(explanations), statement=f"{entry.term}: {entry.description}")


def _judged(llm: GlossaryLlm, number: int, entry: GlossaryEntry, deck: Deck) -> tuple[GlossaryEntry, Entailment | None]:
    """`entry` を判定し、`consistent` でなければ説明だけを上限まで作り直す。照合先が無ければ判定値は `None`。

    `number` は用語の通し番号で、ログには用語の本文の代わりにこれを載せる(NFR-2)。
    """
    claim = _claim(entry, deck)
    if claim is None:
        return entry, None
    for attempt in range(1, MAX_REGENERATIONS + 2):
        if attempt > 1:
            logger.info("glossary: 用語 #%d の説明を作り直す(entailment; %d 回目)", number, attempt - 1)
            entry = GlossaryEntry(entry.term, llm.describe_term(entry.term, deck.language))
            claim = Claim(reference=claim.reference, statement=f"{entry.term}: {entry.description}")
        verdict = judge(llm, [claim])
        if verdict is Entailment.CONSISTENT:
            break
    return entry, verdict


def verify(
    llm: GlossaryLlm, entries: tuple[GlossaryEntry, ...], deck: Deck
) -> tuple[tuple[GlossaryEntry, ...], Verification]:
    """各説明を、その用語が現れる問題の公式解説と1件ずつ照合し、作り直した説明と照合結果を返す。

    全用語を判定したうえで、上限まで作り直しても矛盾した用語があればその件数の `Contradicted`。
    矛盾が無く `uncertain` が1件でもあれば `EntailmentRejected`(D-042)。照合先が1件も無ければ `NoReference`。
    """
    judged = [_judged(llm, number, entry, deck) for number, entry in enumerate(entries, start=1)]
    final = tuple(entry for entry, _ in judged)
    verdicts = [verdict for _, verdict in judged if verdict is not None]
    if not verdicts:
        return final, NoReference()
    contradicted = verdicts.count(Entailment.CONTRADICTED)
    if contradicted:
        return final, Contradicted(contradicted)
    if Entailment.UNCERTAIN in verdicts:
        raise EntailmentRejected(Entailment.UNCERTAIN)
    return final, Consistent(len(verdicts))


def verification_summary(verification: Verification, total: int) -> str:
    """照合結果を記録用の1行にする。`NoReference` は合格と書かない。"""
    match verification:
        case Consistent(checked=checked):
            return f"glossary: consistent ({checked}/{total} 件を公式解説と照合、矛盾 0 件)"
        case Contradicted(count=count):
            return f"glossary: contradicted (公式解説と矛盾 {count} 件)"
        case NoReference():
            return f"glossary: no_reference (公式解説 0 件のため照合対象なし、未照合 {total} 件)"
        case _:
            assert_never(verification)


def emit_glossary[T](llm: GlossaryLlm, deck: Deck, emit: Callable[[Glossary], T]) -> T:
    """用語を抽出して説明を付け、公式解説と照合してから `emit` に成果物を書かせる。

    作り直しても矛盾が残れば照合結果をログに残して `EntailmentRejected` を上げ、`emit` を呼ばない。
    """
    entries, verification = verify(llm, describe_terms(llm, extract_terms(deck), deck.language), deck)
    logger.info("%s", verification_summary(verification, len(entries)))
    match verification:
        case Contradicted():
            raise EntailmentRejected(Entailment.CONTRADICTED)
        case Consistent() | NoReference():
            return emit(Glossary(entries, verification))
        case _:
            assert_never(verification)
