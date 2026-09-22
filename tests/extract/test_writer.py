"""書き出し(C-002 / NFR-2 / D-055)。gitignore 対象の検証が先、書いた JSONL は ingest を通る。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from quiz_sift.domain.ids import DeckId
from quiz_sift.domain.report import IngestOk
from quiz_sift.extract.udemy import FetchedPage, extract
from quiz_sift.extract.writer import BrokenOutputError, verify_jsonl, write_questions, write_report
from quiz_sift.ingest.reader import read_jsonl
from quiz_sift.ingest.validate import validate
from quiz_sift.safety.gitignore_guard import NotIgnoredError

from .conftest import page_html, question_html

SRC = Path(__file__).resolve().parents[2] / "src"


def repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("data/\nout/\n", encoding="utf-8")
    return tmp_path


def sample() -> FetchedPage:
    html = page_html(
        question_html(1, correct=1, selected=1, explanation="解説 1"),
        question_html(2, correct=3, selected=0),
    )
    return FetchedPage("https://example.invalid/quiz/1", html)


def test_writer_validates_gitignore_before_opening(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path)
    result = extract([sample()])
    opened: list[Path] = []
    original = Path.write_text

    def recording_write_text(self: Path, *args: object, **kwargs: object) -> int:
        opened.append(self)
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", recording_write_text)

    with pytest.raises(NotIgnoredError):
        write_questions(root / "docs" / "udemy.jsonl", result.questions, root)
    with pytest.raises(NotIgnoredError):
        write_report(root / "reports" / "extract.json", result, root)
    assert opened == []
    assert not (root / "docs").exists()
    assert not (root / "reports").exists()

    written = write_questions(root / "data" / "aws-saa" / "udemy.jsonl", result.questions, root)
    report = write_report(root / "out" / "aws-saa" / "extract" / "report.json", result, root)
    assert opened == [written, report]


def test_output_passes_ingest_validator(tmp_path: Path) -> None:
    root = repo(tmp_path)
    result = extract([sample()])

    path = write_questions(root / "data" / "aws-saa" / "udemy.jsonl", result.questions, root)

    outcomes = validate(read_jsonl(path), DeckId("aws-saa"), None)
    assert all(isinstance(o, IngestOk) for o in outcomes)
    questions = [o.question for o in outcomes if isinstance(o, IngestOk)]
    assert [q.correct_option_id.local_id for q in questions] == ["B", "D"]
    assert [q.explanation for q in questions] == ["解説 1", None]

    # 壊れた1行を足すと、検証は非0終了する。
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"id": "p001-q003", "stem": "s", "options": []\n')
    with pytest.raises(BrokenOutputError):
        verify_jsonl(path)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; from quiz_sift.extract.writer import verify_jsonl; "
            f"verify_jsonl(Path({str(path)!r}))",
        ],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SRC)},
    )
    assert completed.returncode != 0
    assert "ingest が読めない" in completed.stderr

    # 形は読めても ingest が除外する行(ID 重複)も非0で止まる。
    with pytest.raises(BrokenOutputError):
        write_questions(root / "data" / "aws-saa" / "dup.jsonl", result.questions * 2, root)


def test_report_carries_counts_and_evidence_without_body_text(tmp_path: Path) -> None:
    root = repo(tmp_path)
    result = extract([sample()])

    path = write_report(root / "out" / "aws-saa" / "extract" / "report.json", result, root)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["extracted"] == 2
    assert document["with_explanation"] == 1
    assert document["selector_kinds"] == ["correct-mark"]
    assert [e["id"] for e in document["evidences"]] == ["p001-q001", "p001-q002"]
    assert "問題" not in path.read_text(encoding="utf-8")
