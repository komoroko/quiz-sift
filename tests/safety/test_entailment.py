"""含意判定器(C-013 / NFR-1 / ADR-009)。

LLM の応答は**手書きフィクスチャ**で固定する(D-043)。fake の判定器は手書きの判定語を共有の契約
(`llm_contract.parse_entailment`)で読むので、判定語のパースから集約・書き出しの抑止までが実物の経路になる。
測っているのは判定器のロジックであって、モデルの判定能力ではない。
"""

import logging
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from quiz_sift.adapters.llm_contract import parse_entailment
from quiz_sift.adapters.llm_prompts import ENTAILMENT_SYSTEM
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ports.llm import Entailment, EntailmentStatement, LlmCallError
from quiz_sift.safety.entailment import (
    Claim,
    EntailmentRejected,
    aggregate,
    ask,
    judge,
    measure_mutations,
    reference_for,
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


class FixtureChecker:
    """`EntailmentChecker` の fake。statement ごとの手書きの verdict を、応答の JSON として共有の契約で読む。"""

    def __init__(self, verdicts: dict[str, str | Exception]) -> None:
        self.verdicts = verdicts
        self.asked: list[str] = []
        self.calls = 0

    def check_entailments(self, reference: str, statements: Sequence[EntailmentStatement]) -> tuple[Entailment, ...]:
        self.calls += 1
        self.asked.extend(s.statement for s in statements)
        out = []
        for s in statements:
            verdict = self.verdicts[s.statement]
            if isinstance(verdict, Exception):
                raise verdict
            out.append(parse_entailment({"verdict": verdict}))
        return tuple(out)


def statements(*texts: str) -> list[EntailmentStatement]:
    return [EntailmentStatement(f"s{i}", text) for i, text in enumerate(texts)]


def claims(*texts: str) -> list[Claim]:
    return [Claim(reference=REFERENCE, statement=s) for s in texts]


def test_no_stage_independent_emit_path() -> None:
    """成果物を書くかどうかは段が `judge` の集約で決める。最後の利用者だった script 段がトラックごとの判定
    (C-028)に移ったので、段に依らない書き出しの受け口(`emit_if_consistent`)は持たない。"""
    import quiz_sift.safety.entailment as entailment

    assert not hasattr(entailment, "emit_if_consistent")


def test_three_verdicts_and_fail_closed(tmp_path: Path) -> None:
    llm = FixtureChecker({"良": "consistent", "悪": "contradicted", "謎": "uncertain", "壊": LlmCallError("refusal")})

    # 3値がそのまま区別されて返る。uncertain は consistent に倒れない。
    assert judge(llm, REFERENCE, statements("良", "良")) is Entailment.CONSISTENT
    assert judge(llm, REFERENCE, statements("良", "謎")) is Entailment.UNCERTAIN
    assert judge(llm, REFERENCE, statements("謎", "悪", "良")) is Entailment.CONTRADICTED
    # 照合が1件も無いものは確かめていないので consistent にしない。
    assert judge(llm, REFERENCE, []) is Entailment.UNCERTAIN

    # contradicted・uncertain・判定不能の停止は判定値だけを持ち、本文を終了メッセージに載せない(NFR-3)。
    for name, texts in (("contradicted", ("良", "悪")), ("uncertain", ("謎",)), ("empty", ())):
        rejected = EntailmentRejected(judge(llm, REFERENCE, statements(*texts)))
        assert rejected.verdict is (Entailment.CONTRADICTED if name == "contradicted" else Entailment.UNCERTAIN)
        assert all(s not in str(rejected.code) for s in texts)

    # 判定の呼び出しが失敗したら、判定値を返さずにそのまま上がる(consistent に倒れない)。
    with pytest.raises(LlmCallError):
        judge(llm, REFERENCE, statements("良", "壊"))

    # 捕まえずに上がれば、プロセスは非0で終了し成果物は無い。
    for verdict in ("contradicted", "uncertain"):
        artifact = tmp_path / f"proc-{verdict}.txt"
        script = f"""
from pathlib import Path
from quiz_sift.ports.llm import Entailment, EntailmentStatement
from quiz_sift.safety.entailment import EntailmentRejected, judge

class Checker:
    def check_entailments(self, reference, statements):
        return tuple(Entailment({verdict!r}) for _ in statements)

verdict = judge(Checker(), "r", [EntailmentStatement("0", "s")])
if verdict is not Entailment.CONSISTENT:
    raise EntailmentRejected(verdict)
Path({str(artifact)!r}).write_text("x")
"""
        src = str(Path(__file__).resolve().parents[2] / "src")
        proc = subprocess.run(
            [sys.executable, "-c", script], env={"PYTHONPATH": src}, capture_output=True, text=True, check=False
        )
        assert proc.returncode != 0, proc.stderr
        assert verdict in proc.stderr
        assert not artifact.exists()


def test_no_false_positive_on_paraphrase() -> None:
    llm = FixtureChecker(dict(FIXTURE_VERDICTS))

    # 言い換えは字面が正解とほとんど重ならないが、1件も矛盾扱いにならない。
    false_positives = [p for p in PARAPHRASES if judge(llm, REFERENCE, statements(p)) is not Entailment.CONSISTENT]
    assert false_positives == []
    assert judge(llm, REFERENCE, statements(*PARAPHRASES)) is Entailment.CONSISTENT

    # 言い換えに混ぜて仕込んだ矛盾は検出される。
    assert (
        judge(llm, REFERENCE, statements(*PARAPHRASES[:2], PLANTED_CONTRADICTION, PARAPHRASES[2]))
        is Entailment.CONTRADICTED
    )
    # 判定は全件 LLM に問い合わせている(字面で先に弾いていない)。
    assert PLANTED_CONTRADICTION in llm.asked


def test_mutation_detection_rate_is_recorded(caplog: pytest.LogCaptureFixture, record_property: Any) -> None:
    llm = FixtureChecker(dict(FIXTURE_VERDICTS))
    mutants = claims(*(statement for _, statement, _ in MUTANTS))

    with caplog.at_level(logging.INFO, logger="quiz_sift.safety.entailment"):
        report = measure_mutations(llm, mutants)

    # 全ミューテーションを1件ずつ判定している。
    assert llm.asked == [m.statement for m in mutants]
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


def _multi_question(explanation: str | None = "どちらも推奨される原則である。") -> Question:
    """正解 2 件(A・C)、誤答 2 件(B・D)の問題。"""
    qid = QuestionId(DeckId("deck"), "q1")
    texts = {"A": "最小権限の原則", "B": "ルートユーザーの常用", "C": "職務分掌", "D": "アクセスキーの共有"}
    options = tuple(Option(OptionId(qid, label), text) for label, text in texts.items())
    correct = (options[0].option_id, options[2].option_id)
    return Question(qid, "推奨される原則を2つ選べ。", options, correct, explanation, StudiedFlag.UNKNOWN)


def test_reference_text_holds_the_right_sections() -> None:
    """A-8・ADR-029: reference は正解 N 件すべてと解説だけを持ち、焦点と誤答は主張の側に置く。"""
    question = _multi_question()

    # どの主張にも同じ reference: 正解 N 件すべてと解説。誤答の本文は入らない。
    plain = reference_for(question)
    assert plain == (
        "問題: 推奨される原則を2つ選べ。 正解: 最小権限の原則、職務分掌 解説: どちらも推奨される原則である。"
    )
    assert not any(text in plain for text in ("ルートユーザーの常用", "アクセスキーの共有"))
    # 解説の無い問題では解説の節を作らない。
    assert "解説" not in reference_for(_multi_question(None))
    # 焦点・誤答の節を reference に作る経路は無い。
    with pytest.raises(TypeError):
        reference_for(question, focus=question.correct_option_ids[0])  # type: ignore[call-arg]

    # 判定器への指示は、主張の focus をその主張が説明する正解として、distractors を「正しくない記述の一覧」として
    # 読ませる(D-210、AR-D4)。
    assert "focus の正解を説明している" in ENTAILMENT_SYSTEM
    assert "distractors があれば、それはこの問題の答えとして正しくない記述" in ENTAILMENT_SYSTEM
    assert "distractors を正しくないと述べることは矛盾ではない" in ENTAILMENT_SYSTEM


def test_one_call_per_reference_and_order_is_kept() -> None:
    """ADR-029: 1つの reference の主張は1回で聞き、判定値は主張の順に集約する。数の合わない応答は通さない。"""
    llm = FixtureChecker({"良": "consistent", "悪": "contradicted", "謎": "uncertain"})
    assert ask(llm, REFERENCE, statements("謎", "良", "悪")) == (
        Entailment.UNCERTAIN,
        Entailment.CONSISTENT,
        Entailment.CONTRADICTED,
    )
    assert llm.calls == 1
    # 集約の規則は 1件ずつ聞いたときと同じ。
    assert aggregate([]) is Entailment.UNCERTAIN
    assert aggregate([Entailment.CONSISTENT, Entailment.UNCERTAIN]) is Entailment.UNCERTAIN
    assert aggregate([Entailment.UNCERTAIN, Entailment.CONTRADICTED]) is Entailment.CONTRADICTED
    # 主張が0件なら呼ばない。
    judge(llm, REFERENCE, [])
    assert llm.calls == 1

    # 主張の ID の重複と、判定値の数の食い違いは落とす。
    with pytest.raises(ValueError, match="重複"):
        ask(llm, REFERENCE, [EntailmentStatement("x", "良"), EntailmentStatement("x", "良")])

    class Short:
        def check_entailments(self, reference: str, items: Sequence[EntailmentStatement]) -> tuple[Entailment, ...]:
            return (Entailment.CONSISTENT,)

    with pytest.raises(ValueError, match="合わない"):
        ask(Short(), REFERENCE, statements("良", "良"))
