"""台本の生成(R-6 / NFR-1)。想起素材から台本を組み立て、すべての検査を通ったときだけ成果物を書く。

1問分は `Prompt`(想起キーを問いかけにしたもの)→ `Pause` → `Answer` → `Discrimination` で、
テキストは想起素材の答え・識別ポイントをそのまま使う。台本のために LLM へ文章を書かせる呼び出しは無い。

1. **分割**(`split`): 3分を超えたクラスタは問の境界でトラックに割る。1問分だけで超える
   クラスタがあれば、それを列挙して止める(再生成が要る)。
2. **ラベル列挙判定**(`label_guard`): トラック単位で判定し、1本でも列挙があれば止める。
3. **含意判定**(NFR-1 / D-042): 各問の答えと識別ポイントが、その問題の正解・公式解説と矛盾しないこと。
   `safety.entailment.emit_if_consistent` 越しに書き出すので、`consistent` 以外で成果物は書かれない。

安い検査から順に行い、LLM の含意判定は機械検査をすべて通った台本にだけかける。
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt
from quiz_sift.recall.generate import RecallSet
from quiz_sift.recall.verify import RecallMaterial
from quiz_sift.safety.entailment import Claim, EntailmentChecker, emit_if_consistent
from quiz_sift.script.label_guard import find_enumerations
from quiz_sift.script.segments import QuestionScript, Track
from quiz_sift.script.split import TRACK_LIMIT_SECONDS, plan_tracks

logger = logging.getLogger(__name__)

PAUSE_SECONDS = 3.3
"""考える「間」の長さ。R-7 の無音構造基準(3秒以上)に余裕を持たせる。"""

_PROMPT = {
    Language.JA: ("{terms}。この組み合わせで選ぶものは?", "、"),
    Language.EN: ("{terms}. What fits this combination?", ", "),
}


class ScriptTooLong(SystemExit):
    """1問分だけで上限を超え、分割しても収まらないクラスタがある。捕まえずに上げれば非0で終了する。"""

    def __init__(self, cluster_ids: Sequence[ClusterId]) -> None:
        super().__init__(
            f"1問分で {TRACK_LIMIT_SECONDS:.0f} 秒を超え、再生成が要るクラスタ: {[c.local_id for c in cluster_ids]}"
        )
        self.cluster_ids = tuple(cluster_ids)


class LabelEnumerationFound(SystemExit):
    """選択肢ラベルの列挙を含むトラックがある。捕まえずに上げれば非0で終了する。

    メッセージにはトラックの識別子だけを載せ、教材本文は載せない(NFR-2)。
    """

    def __init__(self, tracks: Sequence[Track]) -> None:
        super().__init__(f"ラベル列挙を含むトラック: {[(t.cluster_id.local_id, t.part) for t in tracks]}")
        self.tracks = tuple(tracks)


@dataclass(frozen=True, slots=True, repr=False)
class ScriptSet:
    """台本ステージの成果物。"""

    tracks: tuple[Track, ...]
    split: tuple[ClusterId, ...]
    """3分を超えたため複数トラックに割ったクラスタ。"""


def question_script(material: RecallMaterial, language: Language) -> QuestionScript:
    """1問分の想起素材を台本にする。"""
    template, separator = _PROMPT[language]
    draft = material.draft
    return QuestionScript(
        material.question_id,
        Prompt(template.format(terms=separator.join(draft.key.terms))),
        Pause(PAUSE_SECONDS),
        Answer(draft.answer),
        Discrimination(draft.discrimination),
    )


def _reference(question: Question) -> str:
    correct = next(o.text for o in question.options if o.option_id == question.correct_option_id)
    reference = f"問題: {question.stem} 正解: {correct}"
    if question.explanation is not None:
        reference += f" 解説: {question.explanation}"
    return reference


def _claims(track: Track, questions: dict[str, Question]) -> list[Claim]:
    claims = []
    for q in track.questions:
        reference = _reference(questions[q.question_id.local_id])
        statements = [q.answer.text] + ([] if q.discrimination is None else [q.discrimination.text])
        claims.extend(Claim(reference=reference, statement=s) for s in statements)
    return claims


def generate_scripts[T](
    checker: EntailmentChecker,
    deck: Deck,
    clusters: Sequence[Cluster],
    recall: RecallSet,
    emit: Callable[[ScriptSet], T],
) -> T:
    """`clusters` の全クラスタの台本を作り、すべての検査を通ったときだけ `emit` に成果物を書かせる。

    割れないクラスタがあれば `ScriptTooLong`、ラベル列挙があれば `LabelEnumerationFound`、
    含意判定が `consistent` でなければ `EntailmentRejected`。どれも `emit` を呼ぶ前に上がる。
    """
    foreign = [c.cluster_id for c in clusters if c.cluster_id.deck_id != deck.deck_id]
    if foreign:
        raise ValueError("別デッキのクラスタが混ざっている")
    materials = {(m.cluster_id, m.question_id): m for m in recall.materials}
    missing = [(c, q) for c in clusters for q in c.question_ids if (c.cluster_id, q) not in materials]
    if missing:
        raise ValueError(f"想起素材の無い問題がある: {[q.local_id for _, q in missing]}")
    per_cluster = [
        (c.cluster_id, [question_script(materials[(c.cluster_id, q)], deck.language) for q in c.question_ids])
        for c in clusters
    ]
    plan = plan_tracks(per_cluster, deck.language)
    if plan.split:
        logger.info("script split clusters over limit: %s", [c.local_id for c in plan.split])
    if plan.unsplittable:
        raise ScriptTooLong(plan.unsplittable)
    enumerating = [t for t in plan.tracks if find_enumerations(t.segments, deck.language)]
    if enumerating:
        raise LabelEnumerationFound(enumerating)
    questions = {q.question_id.local_id: q for q in deck.questions}
    claims = [claim for track in plan.tracks for claim in _claims(track, questions)]
    result = ScriptSet(plan.tracks, plan.split)
    return emit_if_consistent(checker, claims, lambda: emit(result))
