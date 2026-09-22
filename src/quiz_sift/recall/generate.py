"""想起素材の生成(R-4 / NFR-1)。素材を作り、すべての検査を通ったときだけ成果物を書く。

1. **3要素が空でないこと**: 想起キーの各 term・答え・識別ポイントのどれかが空白だけなら止める。
2. **`verify` の検査**: 語句の実在性・参照する選択肢の実在性・キーの重複・汎用語だけのキー・丸写し。
   他クラスタ出現率は合否に関わらずログに記録し、成果物にも載せる。
3. **含意判定**(NFR-1 / D-042): 答えと識別ポイントが、その問題の正解・公式解説と矛盾しないこと。
   `safety.entailment.emit_if_consistent` 越しに書き出すので、`consistent` 以外で成果物は書かれない。

安い検査から順に行い、LLM の含意判定は機械検査をすべて通った素材にだけかける。
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.ports.llm import RecallDraft
from quiz_sift.recall.verify import RecallMaterial, RecallReport, question_texts, verify
from quiz_sift.safety.entailment import Claim, EntailmentChecker, emit_if_consistent

logger = logging.getLogger(__name__)


class RecallLlm(EntailmentChecker, Protocol):
    """`ports.llm.Llm` のうち想起素材の生成と含意判定だけを使う。`Llm` の実装はそのまま渡せる。"""

    def generate_recall(self, question: Question, language: Language) -> RecallDraft: ...


class EmptyRecallElement(SystemExit):
    """想起キー・答え・識別ポイントのどれかが空。捕まえずに上げれば非0で終了する。"""

    def __init__(self, question_id: QuestionId) -> None:
        super().__init__(f"問題 {question_id.local_id} の想起素材に空の要素がある")
        self.question_id = question_id


class RecallRejected(SystemExit):
    """`verify` の検査で1件以上が落ちた。捕まえずに上げれば非0で終了する。

    メッセージには件数だけを載せ、教材本文は載せない(NFR-2)。
    """

    def __init__(self, report: RecallReport) -> None:
        super().__init__(
            "想起素材の検査に落ちたため成果物を書かない: "
            f"ungrounded_keys={report.ungrounded_keys} missing_options={report.missing_options} "
            f"duplicate_keys={report.duplicate_keys} generic_keys={report.generic_keys} verbatim={report.verbatim}"
        )
        self.report = report


@dataclass(frozen=True, slots=True, repr=False)
class RecallSet:
    """想起素材ステージの成果物。"""

    materials: tuple[RecallMaterial, ...]
    report: RecallReport


def _is_blank(text: str) -> bool:
    return not text.strip()


def _draft(llm: RecallLlm, question: Question, language: Language) -> RecallDraft:
    draft = llm.generate_recall(question, language)
    if any(_is_blank(t) for t in draft.key.terms) or _is_blank(draft.answer) or _is_blank(draft.discrimination):
        raise EmptyRecallElement(question.question_id)
    return draft


def _reference(question: Question) -> str:
    correct = next(o.text for o in question.options if o.option_id == question.correct_option_id)
    reference = f"問題: {question.stem} 正解: {correct}"
    if question.explanation is not None:
        reference += f" 解説: {question.explanation}"
    return reference


def _log_rates(report: RecallReport) -> None:
    rates = [r.rate for r in report.term_rates]
    logger.info(
        "recall other-cluster rate: terms=%d max=%.3f mean=%.3f generic_keys=%d",
        len(rates),
        max(rates, default=0.0),
        sum(rates) / len(rates) if rates else 0.0,
        report.generic_keys,
    )


def generate_recall_set[T](
    llm: RecallLlm, deck: Deck, clusters: Sequence[Cluster], emit: Callable[[RecallSet], T]
) -> T:
    """`clusters` の各問題の想起素材を作り、すべての検査を通ったときだけ `emit` に成果物を書かせる。

    空の要素があれば `EmptyRecallElement`、検査に落ちれば `RecallRejected`、含意判定が
    `consistent` でなければ `EntailmentRejected`。どれも `emit` を呼ぶ前に上がる。
    """
    foreign = [c.cluster_id for c in clusters if c.cluster_id.deck_id != deck.deck_id]
    if foreign:
        raise ValueError("別デッキのクラスタが混ざっている")
    questions = {q.question_id: q for q in deck.questions}
    materials = tuple(
        RecallMaterial(cluster.cluster_id, qid, _draft(llm, questions[qid], deck.language))
        for cluster in clusters
        for qid in cluster.question_ids
    )
    cluster_texts = {
        c.cluster_id: [text for qid in c.question_ids for text in question_texts(questions[qid])] for c in clusters
    }
    report = verify(materials, questions, cluster_texts)
    _log_rates(report)
    if not report.passed:
        raise RecallRejected(report)
    claims = [
        Claim(reference=_reference(questions[m.question_id]), statement=statement)
        for m in materials
        for statement in (m.draft.answer, m.draft.discrimination)
    ]
    result = RecallSet(materials, report)
    return emit_if_consistent(llm, claims, lambda: emit(result))
