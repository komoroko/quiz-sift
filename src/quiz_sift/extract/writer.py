"""抽出結果の書き出し(R-2 / NFR-2 / D-055)。

どの書き出しも、ファイルを開く前に書き出し先が `.gitignore` 対象であることを
`safety.gitignore_guard.ensure_ignored` で確かめる。実教材の本文を追跡対象のパスへ書かないため。
根拠と失敗のレポートも本文は持たないが同じ規則に従う(`docs/` も `reports/` も追跡対象)。

問題は ingest の入力スキーマ(ADR-011)の JSONL で書き、書いたファイルを ingest の reader と
バリデータにそのまま通す。1行でも読めないか除外される行があれば `BrokenOutputError` で非0終了する。
"""

import json
from collections.abc import Sequence
from pathlib import Path

from quiz_sift.domain.ids import DeckId
from quiz_sift.domain.report import IngestRejected
from quiz_sift.extract.udemy import ExtractedQuestion, ExtractResult
from quiz_sift.ingest.reader import IngestFormatError, read_jsonl
from quiz_sift.ingest.validate import validate
from quiz_sift.safety.gitignore_guard import ensure_ignored


class BrokenOutputError(SystemExit):
    """書き出した JSONL が ingest を通らない。捕まえずに上げれば非0で終了する。"""


def write_questions(path: Path, questions: Sequence[ExtractedQuestion], repo_root: Path) -> Path:
    """問題を JSONL で書き、ingest を通ることを確かめて、書いたパスを返す。"""
    target = ensure_ignored(path, repo_root)
    lines = [json.dumps(_question_row(question), ensure_ascii=False) for question in questions]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    verify_jsonl(target)
    return target


def verify_jsonl(path: Path) -> None:
    """JSONL を ingest の reader とバリデータに通す。通らない行があれば `BrokenOutputError`。"""
    try:
        records = read_jsonl(path)
    except IngestFormatError as exc:
        raise BrokenOutputError(f"書き出した JSONL を ingest が読めない: {exc}") from None
    rejected = [
        outcome for outcome in validate(records, DeckId(path.parent.name), None) if isinstance(outcome, IngestRejected)
    ]
    if rejected:
        detail = ", ".join(f"{outcome.raw_id}={outcome.reason.value}" for outcome in rejected)
        raise BrokenOutputError(f"書き出した JSONL の {len(rejected)} 件を ingest が除外する: {path}: {detail}")


def write_report(path: Path, result: ExtractResult, repo_root: Path) -> Path:
    """件数・根拠・失敗を JSON で書く。本文は含めない。"""
    target = ensure_ignored(path, repo_root)
    report = result.report
    document = {
        "question_blocks": report.question_blocks,
        "extracted": report.extracted,
        "failed": report.failed,
        "with_explanation": report.with_explanation,
        "without_explanation": report.without_explanation,
        "selector_kinds": sorted({evidence.selector_kind.value for evidence in result.evidences}),
        "evidences": [
            {
                "id": question.local_id,
                "selector_kind": question.evidence.selector_kind.value,
                "element_path": question.evidence.element_path,
            }
            for question in result.questions
        ],
        "extraction_failures": [
            {
                "question_index": failure.question_index,
                "missing_field": failure.missing_field.value,
                "element_path": failure.element_path,
            }
            for failure in report.extraction_failures
        ],
        "fetch_failures": [{"url": failure.url, "reason": failure.reason.value} for failure in report.fetch_failures],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _question_row(question: ExtractedQuestion) -> dict[str, object]:
    return {
        "id": question.local_id,
        "stem": question.stem,
        "options": [{"id": option.label, "text": option.text} for option in question.options],
        "answers": list(question.answer_labels),
        "explanation": question.explanation,
    }
