"""含意判定器(NFR-1 / ADR-009)。生成物が正解・公式解説と矛盾するなら、成果物を出さずに止める。

判定そのものは LLM の含意判定に委ねる(D-024)。文字列一致は言い換えを矛盾と見なすので採らない。
このモジュールが持つのは、3値の判定を**書き出しの前に**集約して、`consistent` 以外で止める部分だけ。

- `uncertain` は `contradicted` と同じく止める(D-042)。判定できないものを通さない。
- 段は `judge` の集約が `consistent` のときだけ成果物を書き、それ以外は `EntailmentRejected` で止める。
  書いてから消す経路がそもそも無い。
- ミューテーション検出率は数値として記録するが、合格ラインは置かない(D-044)。
- 問題の正解・解説から reference を作るのは `reference_for` だけ(想起素材とクラスタ要約が共有する)。
  台本段は判定しない(D-248)。
- 判定は1つの reference に属する主張をまとめて1回で聞き、判定値を主張の ID で受ける(ADR-029)。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from quiz_sift.domain.question import Question
from quiz_sift.ports.llm import Entailment, EntailmentStatement

logger = logging.getLogger(__name__)


class EntailmentChecker(Protocol):
    """`ports.llm.Llm` のうち含意判定だけを使う。`Llm` の実装はそのまま渡せる。"""

    def check_entailments(
        self, reference: str, statements: Sequence[EntailmentStatement]
    ) -> tuple[Entailment, ...]: ...


def reference_for(question: Question) -> str:
    """`question` の含意判定の reference。問題文・正解 N 件すべてと解説を含む(NFR-1)。

    その主張が説明する正解(D-210)と、識別ポイントが参照する誤答(D-192)は、reference ではなく主張の側の
    `focus`・`distractors` に置く(ADR-029)。1問の主張をまとめて聞いても reference は1回で済む。
    """
    texts = {option.option_id: option.text for option in question.options}
    sections = [f"問題: {question.stem}", f"正解: {'、'.join(texts[o] for o in question.correct_option_ids)}"]
    if question.explanation is not None:
        sections.append(f"解説: {question.explanation}")
    return " ".join(sections)


@dataclass(frozen=True, slots=True, repr=False)
class Claim:
    """照合1件: `statement`(生成物の主張)が `reference`(正解・公式解説)と矛盾しないか。ミューテーションに使う。

    どちらも教材本文を含むので `repr` に出さない(NFR-3)。
    """

    reference: str
    statement: str


class EntailmentRejected(SystemExit):
    """判定が `consistent` でなかった。捕まえずに上げれば非0で終了する。

    メッセージには判定値だけを載せ、教材本文は載せない(NFR-3)。
    """

    def __init__(self, verdict: Entailment) -> None:
        super().__init__(f"含意判定が {verdict.value} を返したため成果物を書かない")
        self.verdict = verdict


def ask(
    checker: EntailmentChecker, reference: str, statements: Sequence[EntailmentStatement]
) -> tuple[Entailment, ...]:
    """`statements` を1回の呼び出しで判定し、主張の順の判定値を返す。数が合わなければ落とす。"""
    if len({s.id for s in statements}) != len(statements):
        raise ValueError("主張の ID が重複している")
    verdicts = checker.check_entailments(reference, statements)
    if len(verdicts) != len(statements):
        raise ValueError(f"判定値 {len(verdicts)} 件が主張 {len(statements)} 件と合わない")
    return verdicts


def aggregate(verdicts: Sequence[Entailment]) -> Entailment:
    """判定値の列を集約する。

    `contradicted` が1件でもあればそれ、無ければ `uncertain` が1件でもあればそれ、どちらも無ければ
    `consistent`。判定値が0件なら確かめたものが無いので `uncertain` とする。
    """
    if not verdicts:
        return Entailment.UNCERTAIN
    if Entailment.CONTRADICTED in verdicts:
        return Entailment.CONTRADICTED
    if Entailment.UNCERTAIN in verdicts:
        return Entailment.UNCERTAIN
    return Entailment.CONSISTENT


def judge(checker: EntailmentChecker, reference: str, statements: Sequence[EntailmentStatement]) -> Entailment:
    """1つの reference に対する主張を1回で判定し、`aggregate` で集約する。主張が0件なら呼ばずに `uncertain`。"""
    if not statements:
        return Entailment.UNCERTAIN
    return aggregate(ask(checker, reference, statements))


@dataclass(frozen=True, slots=True)
class MutationReport:
    """ミューテーション(正解に矛盾を仕込んだ主張)への判定の内訳。"""

    total: int
    contradicted: int
    uncertain: int

    @property
    def detection_rate(self) -> float:
        """`contradicted` と判定できた割合。`uncertain` は止まりはするが検出には数えない。"""
        return self.contradicted / self.total


def measure_mutations(checker: EntailmentChecker, mutants: Sequence[Claim]) -> MutationReport:
    """各ミューテーションを1件ずつ判定し、検出率をログに記録して返す。合格ラインは無い(D-044)。"""
    if not mutants:
        raise ValueError("ミューテーションが0件では検出率を定義できない")
    verdicts = [ask(checker, m.reference, [EntailmentStatement("0", m.statement)])[0] for m in mutants]
    report = MutationReport(
        total=len(verdicts),
        contradicted=verdicts.count(Entailment.CONTRADICTED),
        uncertain=verdicts.count(Entailment.UNCERTAIN),
    )
    logger.info(
        "mutation detection rate=%.3f (contradicted=%d uncertain=%d total=%d)",
        report.detection_rate,
        report.contradicted,
        report.uncertain,
        report.total,
    )
    return report
