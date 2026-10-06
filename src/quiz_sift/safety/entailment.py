"""含意判定器(NFR-1 / ADR-009)。生成物が正解・公式解説と矛盾するなら、成果物を出さずに止める。

判定そのものは LLM の含意判定に委ねる(D-024)。文字列一致は言い換えを矛盾と見なすので採らない。
このモジュールが持つのは、3値の判定を**書き出しの前に**集約して、`consistent` 以外で止める部分だけ。

- `uncertain` は `contradicted` と同じく止める(D-042)。判定できないものを通さない。
- 書き出しは `emit` として受け取り、判定が `consistent` のときにだけ呼ぶ。書いてから消す経路が
  そもそも無い。
- ミューテーション検出率は数値として記録するが、合格ラインは置かない(D-044)。
- 問題の正解・解説から reference を作るのは `reference_for` だけ(想起素材・台本・クラスタ要約が共有する)。
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from quiz_sift.domain.ids import OptionId
from quiz_sift.domain.question import Question
from quiz_sift.ports.llm import Entailment

logger = logging.getLogger(__name__)


class EntailmentChecker(Protocol):
    """`ports.llm.Llm` のうち含意判定だけを使う。`Llm` の実装はそのまま渡せる。"""

    def check_entailment(self, reference: str, statement: str) -> Entailment: ...


DISTRACTOR_SECTION = "誤答選択肢(この問題では正しくない記述)"
"""識別ポイントの reference で、参照先の誤答の本文を置く節の名前。`ENTAILMENT_SYSTEM` がこの節の意味を説明する。"""
FOCUS_SECTION = "この文が説明する正解"
"""答えの要素の reference で、その要素が説明する正解を置く節の名前。"""


def reference_for(question: Question, focus: OptionId | None = None, distractors: Sequence[OptionId] = ()) -> str:
    """`question` の含意判定の reference。正解 N 件すべてと解説を含む(NFR-1)。

    `focus` を渡すと、その正解を「この文が説明する正解」の節に明示する(D-210)。`distractors` を渡すと、
    その誤答の本文を「誤答選択肢(この問題では正しくない記述)」の節に置く(D-192)。誤答を正しい情報として
    渡すと、誤答を正しくないと述べる正しい識別ポイントが矛盾と判定される(AR-D4)。
    """
    texts = {option.option_id: option.text for option in question.options}
    if focus is not None and focus not in question.correct_option_ids:
        raise ValueError("focus はこの問題の正解の選択肢でなければならない")
    if any(d not in texts or d in question.correct_option_ids for d in distractors):
        raise ValueError("distractors はこの問題の誤答の選択肢でなければならない")
    sections = [f"問題: {question.stem}", f"正解: {'、'.join(texts[o] for o in question.correct_option_ids)}"]
    if focus is not None:
        sections.append(f"{FOCUS_SECTION}: {texts[focus]}")
    if distractors:
        sections.append(f"{DISTRACTOR_SECTION}: {'、'.join(texts[d] for d in distractors)}")
    if question.explanation is not None:
        sections.append(f"解説: {question.explanation}")
    return " ".join(sections)


@dataclass(frozen=True, slots=True, repr=False)
class Claim:
    """照合1件: `statement`(生成物の主張)が `reference`(正解・公式解説)と矛盾しないか。

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


def judge(checker: EntailmentChecker, claims: Sequence[Claim]) -> Entailment:
    """全件を集約した判定を返す。

    `contradicted` が1件でもあればそれ、無ければ `uncertain` が1件でもあればそれ、どちらも無ければ
    `consistent`。`contradicted` が出た時点で残りは問い合わせない。照合が0件なら確かめたものが
    無いので `uncertain` とする。
    """
    if not claims:
        return Entailment.UNCERTAIN
    result = Entailment.CONSISTENT
    for claim in claims:
        verdict = checker.check_entailment(claim.reference, claim.statement)
        if verdict is Entailment.CONTRADICTED:
            return verdict
        if verdict is Entailment.UNCERTAIN:
            result = verdict
    return result


def emit_if_consistent[T](checker: EntailmentChecker, claims: Sequence[Claim], emit: Callable[[], T]) -> T:
    """判定が `consistent` のときだけ `emit`(成果物の書き出し)を呼ぶ。それ以外は `EntailmentRejected`。

    `LlmCallError` はそのまま上がる。その場合も `emit` は呼ばれていない。
    """
    verdict = judge(checker, claims)
    if verdict is not Entailment.CONSISTENT:
        raise EntailmentRejected(verdict)
    return emit()


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
    verdicts = [checker.check_entailment(m.reference, m.statement) for m in mutants]
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
