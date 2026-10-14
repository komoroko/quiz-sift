"""受入テストの共通部品。`cli.main` を stub の LLM / TTS で走らせる実行ヘルパ(AR-141)。

LLM と TTS の差し替えはこのファイル1箇所に集める。LLM の組み立て口(`cli.LlmFactory`)や起動前検査の
引数が変わったとき、直すのはここだけで、受入テスト本体(`test_*.py`)は書き換えない。
受入テストは、このヘルパと既存の合成フィクスチャ以外に、ほかのテストパッケージの conftest を
import しない(AR-151)。本文はすべて合成で、実教材は含まない(NFR-2)。

呼び出し回数は stub の記録で数える。`out/<deck>/meter.jsonl` の行数や行の形は読まない(AR-156)。
"""

import json
import math
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from quiz_sift import cli
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.ports.llm import ClassificationResult, Entailment, EntailmentStatement, GroupProposal, RecallDraft
from quiz_sift.ports.metering import RunMeter
from quiz_sift.ports.tts import PcmAudio, Tts

RATE = 16000


def write_deck(
    root: Path,
    deck_id: DeckId,
    rows: Sequence[Mapping[str, Any]],
    *,
    language: str = "ja",
    default_studied: str | None = None,
) -> Path:
    """`data/<deck_id>/deck.yaml` と `questions.jsonl` を書き、デッキのディレクトリを返す。"""
    deck_dir = root / "data" / deck_id
    deck_dir.mkdir(parents=True, exist_ok=True)
    manifest = f"deck_id: {deck_id}\ntitle: {deck_id} の模試\nlanguage: {language}\n"
    if default_studied is not None:
        manifest += f"default_studied: {default_studied}\n"
    (deck_dir / "deck.yaml").write_text(manifest, encoding="utf-8")
    (deck_dir / "questions.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return deck_dir


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """`data/` と `out/` を `.gitignore` の対象にした作業ルート(NFR-2 の書き出し前検査を通す)。"""
    (tmp_path / ".gitignore").write_text("data/\nout/\n", encoding="utf-8")
    return tmp_path


@dataclass
class StubLlm:
    """1問1クラスタに分け、要約には先頭の問題文を返し、想起素材は `drafts`(問題の local_id → 下書き)を返す。

    含意判定は常に `CONSISTENT`。呼び出しは `calls` にメソッド名で残る。
    """

    drafts: Mapping[str, RecallDraft] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        self.calls.append("classify")
        groups = tuple(GroupProposal(q.question_id.local_id, (q.question_id,)) for q in deck.questions)
        return ClassificationResult(assignments=groups, failures=())

    def summarize_cluster(self, members: Deck) -> str:
        self.calls.append("summarize")
        return members.questions[0].stem

    def generate_recall(self, question: Question, language: Language) -> RecallDraft:
        self.calls.append("recall")
        return self.drafts[question.question_id.local_id]

    def describe_term(self, term: str, language: Language) -> str:
        self.calls.append("describe")
        return f"{term}の説明"

    def check_entailments(self, reference: str, statements: Sequence[EntailmentStatement]) -> tuple[Entailment, ...]:
        self.calls.append("entailment")
        return tuple(Entailment.CONSISTENT for _ in statements)


@dataclass
class StubTts:
    """渡された `(本文, ボイス)` を記録し、0.5 秒の正弦波を返す。"""

    inputs: list[tuple[str, str]] = field(default_factory=list)

    def synthesize(self, text: str, voice_id: str) -> PcmAudio:
        self.inputs.append((text, voice_id))
        n = round(0.5 * RATE)
        tone = array("h", (round(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / RATE)) for i in range(n)))
        return PcmAudio(tone.tobytes(), RATE)


class CliRunner:
    """`cli.main` を同じ stub で呼び、標準出力の最終行(成果物のパス)を返す。"""

    def __init__(self, root: Path, capsys: pytest.CaptureFixture[str], llm: StubLlm, tts: StubTts) -> None:
        self.root = root
        self.capsys = capsys
        self.llm = llm
        self.tts = tts
        self.tts_model = "model-dir"

    def _llm(self, meter: RunMeter) -> StubLlm:
        return self.llm

    def _tts(self, model_dir: str) -> Tts:
        assert model_dir == self.tts_model
        return self.tts

    def _preflight(self) -> None:
        """起動前検査の fake。検査の中身は `Workspace` を直接走らせるテストが判定する(D-143)。"""

    def __call__(self, *argv: str) -> str:
        self.capsys.readouterr()
        extra = ["--tts-model", self.tts_model] if argv[0] == "audio" else []
        code = cli.main(
            ["--root", str(self.root), *argv, *extra],
            llm_factory=self._llm,
            preflight=self._preflight,
            tts_factory=self._tts,
        )
        assert code == 0
        return self.capsys.readouterr().out.strip().splitlines()[-1]
