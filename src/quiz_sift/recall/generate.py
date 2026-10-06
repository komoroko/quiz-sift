"""想起素材の生成(R-4 / NFR-1 / ADR-022)。問題ごとに素材を作り、すべての検査を通ったときだけ成果物を書く。

1. **1問ずつ生成 → 検査**(クラスタ順): `verify.check_question` に落ちた問題だけを、上限
   `MAX_REGENERATIONS` 回まで作り直す。重複キーはクラスタ順で前の問題と比べるので、後の問題が作り直される。
   他クラスタ出現率は合否に関わらずログに記録し、成果物にも載せる。
2. **呼び出しの失敗は作り直さない**(D-101): 失敗しても残りの問題を回し、段を終えてから `RecallCallsFailed`。
   overage か枠切れを見たら残りを発行しない(D-144)。
3. **上限超過**: 全問を回し切ってから、上限を超えた問題をすべて列挙して `RegenerationExhausted`(D-217)。
4. **含意判定**(NFR-1 / D-042): 答えの各要素は対象の正解を明示した reference と、識別ポイントは参照先の
   誤答の本文を加えた reference と照合する(D-192、D-210)。不合格は作り直さない。
   `safety.entailment.emit_if_consistent` 越しに書き出すので、`consistent` 以外で成果物は書かれない。

安い検査から順に行い、LLM の含意判定は機械検査をすべて通った素材にだけかける。
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.ports.llm import LlmCallError, RecallDraft
from quiz_sift.recall.verify import QuestionCheck, RecallMaterial, RecallReport, check_question, cluster_texts, report
from quiz_sift.safety.entailment import Claim, EntailmentChecker, emit_if_consistent, reference_for

logger = logging.getLogger(__name__)

MAX_REGENERATIONS = 2
"""1問あたりの作り直しの上限。生成は最初の1回と合わせて最大 `1 + MAX_REGENERATIONS` 回(D-171)。"""


class RecallLlm(EntailmentChecker, Protocol):
    """`ports.llm.Llm` のうち想起素材の生成と含意判定だけを使う。`Llm` の実装はそのまま渡せる。"""

    def generate_recall(self, question: Question, language: Language) -> RecallDraft: ...


class RecallCallsFailed(SystemExit):
    """LLM 呼び出しが1件以上失敗したか、残りを発行せずに止めた。下流へ進まず非0で終了する(R-3)。"""


@dataclass(frozen=True, slots=True)
class Exhausted:
    """作り直しの上限を超えた問題1件。最後の生成で落ちた検査の名前と、生成した回数を持つ。"""

    question_id: QuestionId
    failures: tuple[str, ...]
    attempts: int


class RegenerationExhausted(SystemExit):
    """作り直しの上限を超えた問題がある。捕まえずに上げれば非0で終了する。

    メッセージには問題 ID・検査名・回数だけを載せ、教材本文は載せない(NFR-2)。
    """

    def __init__(self, exhausted: Sequence[Exhausted]) -> None:
        listed = " ".join(f"{e.question_id.local_id}({','.join(e.failures)}; {e.attempts} 回)" for e in exhausted)
        super().__init__(f"想起素材の作り直しが上限 {MAX_REGENERATIONS} 回を超えたため成果物を書かない: {listed}")
        self.exhausted = tuple(exhausted)


@dataclass(frozen=True, slots=True, repr=False)
class RecallSet:
    """想起素材ステージの成果物。"""

    materials: tuple[RecallMaterial, ...]
    report: RecallReport


@dataclass(frozen=True, slots=True)
class _Generated:
    material: RecallMaterial
    check: QuestionCheck
    attempts: int


def _generate_one(
    llm: RecallLlm,
    question: Question,
    language: Language,
    cluster_id: ClusterId,
    texts: Mapping[ClusterId, Sequence[str]],
    earlier: Sequence[RecallMaterial],
    stop_seen: Callable[[], bool],
) -> _Generated | None:
    """1問を検査に通るか上限まで作る。生成の前に `stop_seen` が真なら `None`。`LlmCallError` はそのまま上がる。"""
    generated: _Generated | None = None
    for attempt in range(1, MAX_REGENERATIONS + 2):
        if stop_seen():
            return None
        material = RecallMaterial(cluster_id, question.question_id, llm.generate_recall(question, language))
        generated = _Generated(material, check_question(material, question, texts, earlier), attempt)
        if not generated.check.failures:
            break
        logger.info(
            "recall: 問題 %s が検査に落ちた(%d 回目): %s",
            question.question_id.local_id,
            attempt,
            ",".join(generated.check.failures),
        )
    return generated


def _claims(material: RecallMaterial, question: Question) -> list[Claim]:
    draft = material.draft
    claims = [Claim(reference_for(question, focus=e.option_id), e.text) for e in draft.answers]
    discrimination = draft.discrimination
    claims.append(Claim(reference_for(question, distractors=discrimination.distractor_ids), discrimination.text))
    return claims


def _log_rates(result: RecallReport) -> None:
    rates = [r.rate for r in result.term_rates]
    logger.info(
        "recall other-cluster rate: terms=%d max=%.3f mean=%.3f",
        len(rates),
        max(rates, default=0.0),
        sum(rates) / len(rates) if rates else 0.0,
    )


def generate_recall_set[T](
    llm: RecallLlm,
    deck: Deck,
    clusters: Sequence[Cluster],
    emit: Callable[[RecallSet], T],
    *,
    stop_seen: Callable[[], bool],
    failure_kind: Callable[[LlmCallError], str],
) -> T:
    """`clusters` の各問題の想起素材を作り、すべての検査を通ったときだけ `emit` に成果物を書かせる。

    `stop_seen` が真になったら残りを発行しない(D-144)。`failure_kind` は失敗した呼び出しをログに出す種別。
    呼び出しの失敗か未発行があれば `RecallCallsFailed`、上限を超えた問題があれば `RegenerationExhausted`、
    含意判定が `consistent` でなければ `EntailmentRejected`。どれも `emit` を呼ぶ前に上がる。
    """
    foreign = [c.cluster_id for c in clusters if c.cluster_id.deck_id != deck.deck_id]
    if foreign:
        raise ValueError("別デッキのクラスタが混ざっている")
    questions = {q.question_id: q for q in deck.questions}
    texts = cluster_texts({c.cluster_id: c.question_ids for c in clusters}, questions)
    order = [(c.cluster_id, qid) for c in clusters for qid in c.question_ids]
    done: list[_Generated] = []
    exhausted: list[Exhausted] = []
    failures = 0
    for cluster_id, qid in order:
        try:
            generated = _generate_one(
                llm, questions[qid], deck.language, cluster_id, texts, [g.material for g in done], stop_seen
            )
        except LlmCallError as error:
            failures += 1
            logger.warning("recall: 問題 %s の呼び出しが失敗: %s", qid.local_id, failure_kind(error))
            continue
        if generated is None:
            break
        done.append(generated)
        if generated.check.failures:
            exhausted.append(Exhausted(qid, generated.check.failures, generated.attempts))
    issued = len(done) + failures
    logger.info(
        "recall summary: questions=%d issued=%d failure_count=%d regenerated=%d exhausted=%d",
        len(order),
        issued,
        failures,
        sum(g.attempts > 1 for g in done),
        len(exhausted),
    )
    if failures or issued < len(order):
        raise RecallCallsFailed(
            "想起素材の外部 API 呼び出しの失敗か未発行があるため下流へ進まない"
            f"(失敗 {failures} 回、発行 {issued} / {len(order)})"
        )
    if exhausted:
        raise RegenerationExhausted(exhausted)
    result = report([g.check for g in done], {g.material.question_id: g.attempts - 1 for g in done})
    _log_rates(result)
    claims = [claim for g in done for claim in _claims(g.material, questions[g.material.question_id])]
    materials = RecallSet(tuple(g.material for g in done), result)
    return emit_if_consistent(llm, claims, lambda: emit(materials))
