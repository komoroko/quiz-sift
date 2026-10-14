"""想起素材の生成(R-4 / NFR-1 / ADR-022)。問題ごとに素材を作り、すべての検査を通ったときだけ成果物を書く。

1. **1問ずつ生成 → 検査 → 含意判定**(クラスタ順): `verify.check_question` か含意判定に落ちた問題だけを、
   上限 `MAX_REGENERATIONS` 回まで作り直す。重複キーはクラスタ順で前の問題と比べるので、後の問題が作り直される。
   他クラスタ出現率は合否に関わらずログに記録し、成果物にも載せる。
2. **含意判定**(NFR-1 / D-042 / D-232): 機械検査をすべて通った素材だけを判定する。1回の生成につき1回、
   reference(問題の正解 N 件と解説)と、答えの各要素・識別ポイントの主張をまとめて聞く(ADR-029)。答えの要素には
   その要素が説明する正解を `focus` に、識別ポイントには参照先の誤答の本文を `distractors` に付ける(D-192、D-210)。
   `consistent` でなければ検査名 `entailment` の不合格として、同じ上限の中でその問題を作り直す。
3. **呼び出しの失敗は作り直さない**(D-101): 含意判定の呼び出しの失敗もその問題の失敗に数える。失敗しても
   残りの問題を回し、段を終えてから `RecallCallsFailed`。overage か枠切れを見たら残りを発行しない(D-144)。
4. **上限超過**: 全問を回し切ってから、上限を超えた問題をすべて列挙して `RegenerationExhausted`(D-217)。
   上限超過の問題が無いとき、成果物に書かれるのはすべて含意判定を通った素材だけになる。
5. **途中結果からの再開**(ADR-028): 実行の列では、問題ごとに「通過」「失格」「途中」を途中結果に書く。
   出力のあった生成1回ごとと、判定値が返った呼び出しごとに書き、出力の無い生成は回数に数えない。再開では、
   通過・失格の問題を呼び出さずに戻し(重複の検査でも前の問題として数える)、途中の問題は使った回数の続きから
   数え、判定値のある生成物を判定し直さない。失格の問題は作り直さないので、1回で走った場合と同じく止まる。
"""

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.ports.llm import Entailment, EntailmentStatement, LlmCallError, RecallDraft
from quiz_sift.recall.verify import (
    QuestionCheck,
    RecallMaterial,
    RecallReport,
    TermRate,
    check_question,
    cluster_texts,
    report,
)
from quiz_sift.safety.entailment import EntailmentChecker, aggregate, ask, reference_for

logger = logging.getLogger(__name__)

MAX_REGENERATIONS = 2
"""1問あたりの作り直しの上限。生成は最初の1回と合わせて最大 `1 + MAX_REGENERATIONS` 回(D-171)。"""
ENTAILMENT = "entailment"
"""含意判定が `consistent` でなかったときの検査名(D-232)。"""


class RecallLlm(EntailmentChecker, Protocol):
    """`ports.llm.Llm` のうち想起素材の生成と含意判定だけを使う。`Llm` の実装はそのまま渡せる。"""

    def generate_recall(self, question: Question, language: Language) -> RecallDraft: ...


class ItemProgress(Protocol):
    """段のキャッシュ鍵1つ分の途中結果(`pipeline.stage.Progress`)。項目は通し番号で読み書きする。"""

    def load(self) -> Mapping[int, bytes]: ...

    def save(self, index: int, data: bytes) -> None: ...


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
    """検査を通った(通過)か、上限を超えた(失格。`check.failures` が空でない)問題1件。"""

    material: RecallMaterial
    check: QuestionCheck
    attempts: int


@dataclass(frozen=True, slots=True)
class _Pending:
    """途中の問題1件。`attempts` は出力のあった生成の回数、`material` は判定中の生成物(無ければ次を生成する)、
    `verdicts` はその生成物の主張の判定値を主張の順に並べたもの(まだ判定していなければ `None`)。"""

    attempts: int
    material: RecallMaterial | None
    verdicts: tuple[Entailment, ...] | None


_START = _Pending(0, None, None)


def material_json(material: RecallMaterial) -> dict[str, Any]:
    """想起素材1件の JSON。成果物と途中結果が同じ形を使う。"""
    draft = material.draft
    return {
        "cluster": material.cluster_id.local_id,
        "question": material.question_id.local_id,
        "terms": list(draft.key.terms),
        "answers": [{"option": e.option_id.local_id, "text": e.text} for e in draft.answers],
        "discrimination": {
            "text": draft.discrimination.text,
            "distractors": [o.local_id for o in draft.discrimination.distractor_ids],
        },
    }


def material_from(row: Mapping[str, Any], deck_id: DeckId) -> RecallMaterial:
    """`material_json` の逆。"""
    question_id = QuestionId(deck_id, row["question"])
    discrimination = row["discrimination"]
    draft = RecallDraft(
        RecallKey(tuple(row["terms"])),
        tuple(AnswerElement(OptionId(question_id, e["option"]), e["text"]) for e in row["answers"]),
        DiscriminationPoint(
            discrimination["text"], tuple(OptionId(question_id, o) for o in discrimination["distractors"])
        ),
    )
    return RecallMaterial(ClusterId(deck_id, row["cluster"]), question_id, draft)


def _state_json(question_id: QuestionId, state: _Generated | _Pending) -> bytes:
    data: dict[str, Any] = {"question": question_id.local_id, "attempts": state.attempts}
    match state:
        case _Generated(material=material, check=check):
            data["state"] = "exhausted" if check.failures else "passed"
            data["material"] = material_json(material)
            data["failures"] = list(check.failures)
            data["rates"] = [[r.question_id.local_id, r.term, r.rate] for r in check.rates]
        case _Pending(material=material, verdicts=verdicts):
            data["state"] = "pending"
            data["material"] = None if material is None else material_json(material)
            data["verdicts"] = None if verdicts is None else [v.value for v in verdicts]
    return json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _state_from(raw: bytes, question_id: QuestionId) -> _Generated | _Pending:
    data = json.loads(raw)
    if data["question"] != question_id.local_id:
        raise ValueError(f"途中結果の問題が通し番号と合わない: {data['question']} != {question_id.local_id}")
    deck_id = question_id.deck_id
    match data["state"]:
        case "passed" | "exhausted":
            rates = tuple(TermRate(QuestionId(deck_id, q), term, rate) for q, term, rate in data["rates"])
            check = QuestionCheck(tuple(data["failures"]), rates)
            return _Generated(material_from(data["material"], deck_id), check, data["attempts"])
        case "pending":
            material = None if data["material"] is None else material_from(data["material"], deck_id)
            verdicts = None if data["verdicts"] is None else tuple(Entailment(v) for v in data["verdicts"])
            return _Pending(data["attempts"], material, verdicts)
        case other:
            raise ValueError(f"途中結果の状態が不明: {other}")


def _generate_one(
    llm: RecallLlm,
    question: Question,
    language: Language,
    cluster_id: ClusterId,
    texts: Mapping[ClusterId, Sequence[str]],
    earlier: Sequence[RecallMaterial],
    stop_seen: Callable[[], bool],
    pending: _Pending,
    save: Callable[[_Pending], None],
) -> _Generated | None:
    """1問を検査と含意判定に通るか上限まで作る。生成の前に `stop_seen` が真なら `None`。

    `pending` の回数の続きから数え、判定中の生成物があればその判定値を使う(無ければ判定する)。出力のあった生成と
    返った判定値ごとに `save` を呼ぶ。`LlmCallError`(生成と含意判定のどちらの呼び出しでも)はそのまま上がる。
    """
    attempts, material, verdicts = pending.attempts, pending.material, pending.verdicts
    while True:
        if material is None:
            if stop_seen():
                return None
            material = RecallMaterial(cluster_id, question.question_id, llm.generate_recall(question, language))
            attempts, verdicts = attempts + 1, None
            save(_Pending(attempts, material, None))
        check = check_question(material, question, texts, earlier)
        if not check.failures:
            statements = _statements(material, question)
            if verdicts is None:
                verdicts = ask(llm, reference_for(question), statements)
                save(_Pending(attempts, material, verdicts))
            elif len(verdicts) != len(statements):
                raise ValueError(f"途中結果の判定値 {len(verdicts)} 件が主張 {len(statements)} 件と合わない")
            if aggregate(verdicts) is not Entailment.CONSISTENT:
                check = replace(check, failures=(ENTAILMENT,))
        if not check.failures or attempts > MAX_REGENERATIONS:
            return _Generated(material, check, attempts)
        logger.info(
            "recall: 問題 %s を作り直す(%s; %d 回目)",
            question.question_id.local_id,
            ",".join(check.failures),
            attempts,
        )
        material, verdicts = None, None
        save(_Pending(attempts, None, None))


def _statements(material: RecallMaterial, question: Question) -> list[EntailmentStatement]:
    """1回の生成の主張。答えの要素はその正解を `focus` に、識別ポイントは参照先の誤答の本文を `distractors` に持つ。

    答えの要素には誤答の本文を付けない。誤答を正しい情報として渡すと、誤答を正しくないと述べる正しい識別ポイントが
    矛盾と判定される(AR-D4)。
    """
    texts = {option.option_id: option.text for option in question.options}
    draft = material.draft
    if any(e.option_id not in question.correct_option_ids for e in draft.answers):
        raise ValueError("答えの要素の focus はこの問題の正解の選択肢でなければならない")
    distractor_ids = draft.discrimination.distractor_ids
    if any(d not in texts or d in question.correct_option_ids for d in distractor_ids):
        raise ValueError("distractors はこの問題の誤答の選択肢でなければならない")
    statements = [
        EntailmentStatement(f"answer-{i}", e.text, focus=texts[e.option_id]) for i, e in enumerate(draft.answers)
    ]
    statements.append(
        EntailmentStatement(
            "discrimination", draft.discrimination.text, distractors=tuple(texts[d] for d in distractor_ids)
        )
    )
    return statements


def _log_rates(result: RecallReport) -> None:
    rates = [r.rate for r in result.term_rates]
    logger.info(
        "recall other-cluster rate: terms=%d max=%.3f mean=%.3f",
        len(rates),
        max(rates, default=0.0),
        sum(rates) / len(rates) if rates else 0.0,
    )


def _restore(progress: ItemProgress | None, clusters: Sequence[Cluster]) -> dict[int, _Generated | _Pending]:
    """途中結果を読み、通し番号(クラスタ順の問題の位置)ごとの状態にする。件数だけをログに出す(NFR-2)。"""
    if progress is None:
        return {}
    order = [qid for c in clusters for qid in c.question_ids]
    raw = progress.load()
    unknown = sorted(set(raw) - set(range(len(order))))
    if unknown:
        raise ValueError(f"途中結果に問題の無い通し番号がある: {unknown}")
    restored = {index: _state_from(data, order[index]) for index, data in raw.items()}
    reused = sum(isinstance(state, _Generated) for state in restored.values())
    if restored:
        logger.info("recall resume: reused=%d remaining=%d", reused, len(order) - reused)
    return restored


def generate_recall_set[T](
    llm: RecallLlm,
    deck: Deck,
    clusters: Sequence[Cluster],
    emit: Callable[[RecallSet], T],
    *,
    stop_seen: Callable[[], bool],
    failure_kind: Callable[[LlmCallError], str],
    progress: ItemProgress | None,
) -> T:
    """`clusters` の各問題の想起素材を作り、すべての検査と含意判定を通ったときだけ `emit` に成果物を書かせる。

    `stop_seen` が真になったら残りを発行しない(D-144)。`failure_kind` は失敗した呼び出しをログに出す種別。
    `progress` は問題ごとの途中結果(通し番号はクラスタ順の位置)。`None` なら読みも書きもしない。
    呼び出しの失敗か未発行があれば `RecallCallsFailed`、上限を超えた問題があれば `RegenerationExhausted`。
    どちらも `emit` を呼ぶ前に上がる。
    """
    foreign = [c.cluster_id for c in clusters if c.cluster_id.deck_id != deck.deck_id]
    if foreign:
        raise ValueError("別デッキのクラスタが混ざっている")
    questions = {q.question_id: q for q in deck.questions}
    texts = cluster_texts({c.cluster_id: c.question_ids for c in clusters}, questions)
    order = [(c.cluster_id, qid) for c in clusters for qid in c.question_ids]
    restored = _restore(progress, clusters)
    done: list[_Generated] = []
    exhausted: list[Exhausted] = []
    failures = 0
    for index, (cluster_id, qid) in enumerate(order):
        state = restored.get(index, _START)
        if isinstance(state, _Generated):
            generated: _Generated | None = state
        else:

            def save(state: _Generated | _Pending, index: int = index, qid: QuestionId = qid) -> None:
                if progress is not None:
                    progress.save(index, _state_json(qid, state))

            try:
                generated = _generate_one(
                    llm,
                    questions[qid],
                    deck.language,
                    cluster_id,
                    texts,
                    [g.material for g in done],
                    stop_seen,
                    state,
                    save,
                )
            except LlmCallError as error:
                failures += 1
                logger.warning("recall: 問題 %s の呼び出しが失敗: %s", qid.local_id, failure_kind(error))
                continue
            if generated is not None:
                save(generated)
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
    return emit(RecallSet(tuple(g.material for g in done), result))
