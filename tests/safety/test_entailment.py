"""含意判定器(C-013 / NFR-1 / ADR-009)。

LLM の応答は**手書きフィクスチャ**で固定する(D-043)。判定は `AnthropicLlm` に偽のクライアントを
差して通すので、応答 JSON のパースから集約・書き出しの抑止までが実物の経路になる。
測っているのは判定器のロジックであって、モデルの判定能力ではない。
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from quiz_sift.adapters.llm_anthropic import AnthropicLlm
from quiz_sift.ports.llm import Entailment, LlmCallError, TokenUsage
from quiz_sift.safety.entailment import (
    Claim,
    EntailmentRejected,
    emit_if_consistent,
    judge,
    measure_mutations,
)

REFERENCE = (
    "正解: S3 標準-IA。解説: アクセス頻度は低いが、必要なときには即座に取り出せる必要があるデータに向く。"
    "取り出しにはミリ秒単位でアクセスでき、Glacier のような復元待ちは発生しない。"
)

PARAPHRASES = (
    "めったに読まないが、読むときはすぐ欲しいデータなら S3 標準-IA を選ぶ。",
    "標準-IA は低頻度アクセス向けで、取り出しは即時。Glacier と違って復元を待たなくてよい。",
    "Standard-IA: たまにしか使わないけれど、使うときは待てないデータの置き場所。",
)
"""正解を言い換えただけの生成物。字面は REFERENCE とほとんど重ならない。"""

PLANTED_CONTRADICTION = "S3 標準-IA は取り出しに数時間の復元待ちが発生する。"

MUTANTS = (
    ("否定", "S3 標準-IA は即座に取り出すことができない。", "contradicted"),
    ("数値", "S3 標準-IA の取り出しには 12 時間かかる。", "contradicted"),
    ("取り違え", "アクセス頻度は低いが即時に取り出したいデータには S3 Glacier Deep Archive が向く。", "contradicted"),
    ("条件反転", "S3 標準-IA はアクセス頻度が高いデータに向く。", "contradicted"),
    ("量化", "S3 標準-IA は必ず復元リクエストを出してから読む。", "uncertain"),
    ("微修正", "S3 標準-IA の取り出しは秒単位で完了する。", "consistent"),
)
"""(種類, 矛盾を仕込んだ主張, 手書きの LLM 応答)。取りこぼしと uncertain を含めてある:
検出率に合格ラインは無く(D-044)、1.0 でない値も数値のまま記録されることを見るため。
"""

FIXTURE_VERDICTS = {
    **dict.fromkeys(PARAPHRASES, "consistent"),
    PLANTED_CONTRADICTION: "contradicted",
    **{statement: verdict for _, statement, verdict in MUTANTS},
}
"""手書きフィクスチャ: statement ごとに LLM が返した体の verdict。"""


class Recorder:
    def record(self, model: str, usage: TokenUsage) -> None:
        pass

    def begin_call(self, method: str) -> None:
        pass


class FixtureClient:
    """`messages.create` の要求から statement を読み、手書きの verdict を JSON 応答として返す。"""

    def __init__(self, verdicts: dict[str, str | Exception]) -> None:
        self.verdicts = verdicts
        self.asked: list[str] = []
        self.messages = self

    def create(self, **request: Any) -> object:
        statement = json.loads(request["messages"][0]["content"])["statement"]
        self.asked.append(statement)
        verdict = self.verdicts[statement]
        if isinstance(verdict, Exception):
            raise verdict
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps({"verdict": verdict}))],
            usage=SimpleNamespace(
                input_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=1
            ),
        )


def make_llm(verdicts: dict[str, str | Exception]) -> tuple[AnthropicLlm, FixtureClient]:
    client = FixtureClient(verdicts)
    llm = AnthropicLlm(cast(Any, Recorder()))
    llm.client = cast(Any, client)
    return llm, client


class Emitter:
    """成果物の書き出し役。呼ばれたかどうかだけを記録する。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls = 0

    def __call__(self) -> Path:
        self.calls += 1
        self.path.write_text("artifact", encoding="utf-8")
        return self.path


def claims(*statements: str) -> list[Claim]:
    return [Claim(reference=REFERENCE, statement=s) for s in statements]


def test_three_verdicts_and_fail_closed(tmp_path: Path) -> None:
    llm, _ = make_llm({"良": "consistent", "悪": "contradicted", "謎": "uncertain", "壊": LlmCallError("refusal")})

    # 3値がそのまま区別されて返る。uncertain は consistent に倒れない。
    assert judge(llm, claims("良", "良")) is Entailment.CONSISTENT
    assert judge(llm, claims("良", "謎")) is Entailment.UNCERTAIN
    assert judge(llm, claims("謎", "悪", "良")) is Entailment.CONTRADICTED
    # 照合が1件も無いものは確かめていないので consistent にしない。
    assert judge(llm, []) is Entailment.UNCERTAIN

    ok = Emitter(tmp_path / "ok.txt")
    assert emit_if_consistent(llm, claims("良"), ok) == ok.path
    assert ok.calls == 1

    # contradicted・uncertain・判定不能のどれでも、書き出しは一度も呼ばれない(書いてから消すのではない)。
    for name, statements in (("contradicted", ("良", "悪")), ("uncertain", ("謎",)), ("empty", ())):
        emitter = Emitter(tmp_path / f"{name}.txt")
        with pytest.raises(EntailmentRejected) as rejected:
            emit_if_consistent(llm, claims(*statements), emitter)
        assert emitter.calls == 0
        assert not emitter.path.exists()
        assert rejected.value.verdict is (Entailment.CONTRADICTED if name == "contradicted" else Entailment.UNCERTAIN)
        # 本文を終了メッセージに載せない(NFR-3)。
        assert all(s not in str(rejected.value.code) for s in statements)

    broken = Emitter(tmp_path / "broken.txt")
    with pytest.raises(LlmCallError):
        emit_if_consistent(llm, claims("良", "壊"), broken)
    assert broken.calls == 0

    # 捕まえずに上がれば、プロセスは非0で終了し成果物は無い。
    for verdict in ("contradicted", "uncertain"):
        artifact = tmp_path / f"proc-{verdict}.txt"
        script = f"""
from pathlib import Path
from quiz_sift.ports.llm import Entailment
from quiz_sift.safety.entailment import Claim, emit_if_consistent

class Checker:
    def check_entailment(self, reference, statement):
        return Entailment({verdict!r})

emit_if_consistent(Checker(), [Claim("r", "s")], lambda: Path({str(artifact)!r}).write_text("x"))
"""
        src = str(Path(__file__).resolve().parents[2] / "src")
        proc = subprocess.run(
            [sys.executable, "-c", script], env={"PYTHONPATH": src}, capture_output=True, text=True, check=False
        )
        assert proc.returncode != 0, proc.stderr
        assert verdict in proc.stderr
        assert not artifact.exists()


def test_no_false_positive_on_paraphrase(tmp_path: Path) -> None:
    llm, client = make_llm(dict(FIXTURE_VERDICTS))

    # 言い換えは字面が正解とほとんど重ならないが、1件も矛盾扱いにならない。
    false_positives = [p for p in PARAPHRASES if judge(llm, claims(p)) is not Entailment.CONSISTENT]
    assert false_positives == []
    emitter = Emitter(tmp_path / "paraphrase.txt")
    emit_if_consistent(llm, claims(*PARAPHRASES), emitter)
    assert emitter.calls == 1

    # 言い換えに混ぜて仕込んだ矛盾は検出され、成果物は書かれない。
    planted = Emitter(tmp_path / "planted.txt")
    with pytest.raises(EntailmentRejected) as rejected:
        emit_if_consistent(llm, claims(*PARAPHRASES[:2], PLANTED_CONTRADICTION, PARAPHRASES[2]), planted)
    assert rejected.value.verdict is Entailment.CONTRADICTED
    assert planted.calls == 0
    # 判定は全件 LLM に問い合わせている(字面で先に弾いていない)。
    assert PLANTED_CONTRADICTION in client.asked


def test_mutation_detection_rate_is_recorded(caplog: pytest.LogCaptureFixture, record_property: Any) -> None:
    llm, client = make_llm(dict(FIXTURE_VERDICTS))
    mutants = claims(*(statement for _, statement, _ in MUTANTS))

    with caplog.at_level(logging.INFO, logger="quiz_sift.safety.entailment"):
        report = measure_mutations(llm, mutants)

    # 全ミューテーションを1件ずつ判定している。
    assert client.asked == [m.statement for m in mutants]
    assert (report.total, report.contradicted, report.uncertain) == (6, 4, 1)
    assert report.detection_rate == pytest.approx(4 / 6)

    # 記録: 検出率が数値としてログに出ている。合格ラインは判定しない(D-044)。
    records = [r for r in caplog.records if r.name == "quiz_sift.safety.entailment"]
    assert len(records) == 1
    assert records[0].args is not None
    assert float(cast(tuple[Any, ...], records[0].args)[0]) == report.detection_rate
    assert "rate=0.667" in records[0].getMessage()
    record_property("mutation_detection_rate", report.detection_rate)

    with pytest.raises(ValueError):
        measure_mutations(llm, [])
