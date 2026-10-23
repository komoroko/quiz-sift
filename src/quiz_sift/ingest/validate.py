"""入力1件ごとの妥当性判定と、`IngestReport` の組み立て(R-1)。

入力1件ごとに `IngestOk | IngestRejected` を返す。除外理由は3種類の enum で、1件に1つ:

1. `DUPLICATE_ID` — 同じ `id` が2件以上ある。**その `id` を持つ全件**を除外する。どれが正しいかは
   入力から決まらず、先勝ちにすると学習記録が入力の並び順で別の問題を指すようになる。
2. `OPTION_COUNT_UNSUPPORTED` — 選択肢の数 M が `OPTION_COUNTS` に無い。
3. `ANSWER_UNRESOLVED` — `answers` が空・重複・選択肢に無い ID を含む、または正解の数 N が M 以上。

正解は入力の並びに依らず選択肢の並び順に揃える。

既習フラグは `resolve_studied` で 個別 → デッキ既定 → `unknown` の順に解決する。
"""

from collections import Counter
from collections.abc import Sequence

from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.question import OPTION_COUNTS, Option, Question
from quiz_sift.domain.report import IngestOk, IngestOutcome, IngestRejected, IngestReport, RejectionReason
from quiz_sift.domain.studied import StudiedFlag, resolve_studied
from quiz_sift.ingest.reader import RawRecord


def validate(records: Sequence[RawRecord], deck_id: DeckId, deck_default: StudiedFlag | None) -> list[IngestOutcome]:
    """入力の並び順のまま、1件ごとの結果を返す。"""
    id_counts = Counter(record.raw_id for record in records)
    return [_validate_one(record, deck_id, deck_default, id_counts[record.raw_id] > 1) for record in records]


def build_report(outcomes: Sequence[IngestOutcome]) -> IngestReport:
    """1件ごとの結果から `IngestReport` を作る。件数は結果の列から数えるだけで、足し込まない。"""
    accepted = [outcome.question for outcome in outcomes if isinstance(outcome, IngestOk)]
    with_explanation = sum(1 for question in accepted if question.explanation is not None)
    return IngestReport(
        input_total=len(outcomes),
        accepted=len(accepted),
        rejected=tuple(outcome for outcome in outcomes if isinstance(outcome, IngestRejected)),
        with_explanation=with_explanation,
        without_explanation=len(accepted) - with_explanation,
    )


def _validate_one(
    record: RawRecord, deck_id: DeckId, deck_default: StudiedFlag | None, duplicated: bool
) -> IngestOutcome:
    if duplicated:
        return IngestRejected(record.raw_id, RejectionReason.DUPLICATE_ID)
    if len(record.options) not in OPTION_COUNTS:
        return IngestRejected(record.raw_id, RejectionReason.OPTION_COUNT_UNSUPPORTED)
    option_ids = [option.option_id for option in record.options]
    answers = set(record.answers)
    if (
        not answers
        or len(answers) != len(record.answers)
        or not answers <= set(option_ids)
        or len(answers) >= len(option_ids)
    ):
        return IngestRejected(record.raw_id, RejectionReason.ANSWER_UNRESOLVED)

    question_id = QuestionId(deck_id, record.raw_id)
    return IngestOk(
        Question(
            question_id=question_id,
            stem=record.stem,
            options=tuple(Option(OptionId(question_id, option.option_id), option.text) for option in record.options),
            correct_option_ids=tuple(
                OptionId(question_id, option_id) for option_id in option_ids if option_id in answers
            ),
            explanation=record.explanation,
            studied=resolve_studied(record.studied, deck_default),
        )
    )
