"""想起素材の生成ループと含意判定の結線(C-004 / C-013 / R-4 / NFR-1)。"""

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from quiz_sift.adapters import llm_prompts
from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.pipeline.stage import Progress
from quiz_sift.ports.llm import Entailment, EntailmentStatement, LlmCallError, RecallDraft, Retry
from quiz_sift.recall.generate import (
    ENTAILMENT,
    MAX_REGENERATIONS,
    RecallCallsFailed,
    RegenerationExhausted,
    _statements,
)
from quiz_sift.recall.verify import RecallMaterial

from .conftest import DECK, GOOD, ROWS, StubLlm, Writer, generate, option, question

# 語句も参照もすべて実在するが、正解・解説(標準-IA はミリ秒単位で取り出せる)と矛盾する答え。
CONTRADICTING = "S3 標準-IA の取り出しには数時間かかる"
ORDER = [("c001", "q01"), ("c001", "q02"), ("c002", "q03"), ("c002", "q04"), ("c003", "q05")]


def judged(llm: StubLlm) -> set[str]:
    """含意判定にかかった問題(reference の問題文から引く)。"""
    return {qid for qid, stem, *_ in ROWS for reference, _ in llm.checked if reference.startswith(f"問題: {stem}")}


def test_three_elements_are_non_empty(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    writer = Writer(tmp_path / "recall.tsv")
    generate(StubLlm(GOOD), deck, clusters, writer)
    assert writer.result is not None
    materials = writer.result.materials
    # 全クラスタの全問題に1件ずつ、所属クラスタつきで出る。
    assert [(m.cluster_id.local_id, m.question_id.local_id) for m in materials] == ORDER
    for m in materials:
        assert m.draft.key.terms and all(t.strip() for t in m.draft.key.terms)
        assert all(e.text.strip() for e in m.draft.answers)
        assert m.draft.discrimination.text.strip()
    assert len((tmp_path / "recall.tsv").read_text(encoding="utf-8").splitlines()) == len(GOOD)
    assert writer.result.report.regenerations == tuple((m.question_id, 0) for m in materials)

    # どれか1要素でも空白だけなら作り直し、上限を超えたら含意判定より前に非0で止まり、何も書かれない。
    (element,) = GOOD["q03"].answers
    blanks = {
        "key": replace(GOOD["q03"], key=RecallKey(("明示的な拒否", "  "))),
        "answer": replace(GOOD["q03"], answers=(AnswerElement(element.option_id, ""),)),
        "discrimination": replace(
            GOOD["q03"], discrimination=DiscriminationPoint(" \n", GOOD["q03"].discrimination.distractor_ids)
        ),
    }
    for name, draft in blanks.items():
        out = tmp_path / f"blank-{name}.tsv"
        llm = StubLlm({**GOOD, "q03": draft})
        with pytest.raises(RegenerationExhausted) as exc:
            generate(llm, deck, clusters, Writer(out))
        assert exc.value.code not in (None, 0), name
        assert [(e.question_id.local_id, e.attempts) for e in exc.value.exhausted] == [("q03", 1 + MAX_REGENERATIONS)]
        assert "empty" in exc.value.exhausted[0].failures, name
        assert not out.exists(), name
        # 機械検査に落ちた素材は含意判定にかけない。
        assert "q03" not in judged(llm), name


def test_failed_question_is_regenerated_until_it_passes(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path
) -> None:
    ungrounded = replace(GOOD["q01"], key=RecallKey(("Lambda",)))
    llm = StubLlm(GOOD, sequences={"q01": (ungrounded, ungrounded, GOOD["q01"]), "q04": (ungrounded, GOOD["q04"])})
    writer = Writer(tmp_path / "recall.tsv")
    generate(llm, deck, clusters, writer)
    assert writer.result is not None
    # 落ちた問題だけを作り直し、その回数を問題ごとに記録する。
    assert llm.recalled == ["q01", "q01", "q01", "q02", "q03", "q04", "q04", "q05"]
    assert {q.local_id: n for q, n in writer.result.report.regenerations} == {
        "q01": 2,
        "q02": 0,
        "q03": 0,
        "q04": 1,
        "q05": 0,
    }
    assert writer.result.report.passed
    # 書き出されるのは最後に検査を通った素材。
    assert [m.draft for m in writer.result.materials] == list(GOOD.values())


def test_exhausted_questions_are_all_listed_after_every_question(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path
) -> None:
    out = tmp_path / "recall.tsv"
    llm = StubLlm(
        {
            **GOOD,
            "q01": replace(GOOD["q01"], key=RecallKey(("Lambda",))),
            "q04": replace(GOOD["q04"], discrimination=DiscriminationPoint("ロールを使う", ())),
        }
    )
    with pytest.raises(RegenerationExhausted) as exc:
        generate(llm, deck, clusters, Writer(out))
    # 上限を超えた q01 の後も残りの問題を回し切り、超えた問題をすべて列挙する。
    assert llm.recalled == ["q01"] * 3 + ["q02", "q03"] + ["q04"] * 3 + ["q05"]
    assert [(e.question_id.local_id, e.failures, e.attempts) for e in exc.value.exhausted] == [
        ("q01", ("ungrounded_key",), 3),
        ("q04", ("missing_options",), 3),
    ]
    message = str(exc.value.code)
    assert "q01(ungrounded_key; 3 回)" in message and "q04(missing_options; 3 回)" in message
    assert "Lambda" not in message and "ロール" not in message
    assert not out.exists()
    assert judged(llm) == {"q02", "q03", "q05"}


def test_call_failures_are_not_regenerated(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    out = tmp_path / "recall.tsv"
    llm = StubLlm(GOOD, failing=frozenset({"q02", "q04"}))
    with pytest.raises(RecallCallsFailed) as exc:
        generate(llm, deck, clusters, Writer(out))
    # 失敗した問題は1回だけ呼ばれ、残りの問題は回し切ってから止まる。
    assert llm.recalled == [qid for _, qid in ORDER]
    assert "失敗 2 回、発行 5 / 5" in str(exc.value.code)
    assert not out.exists()
    assert judged(llm) == {"q01", "q03", "q05"}


def test_stop_seen_issues_no_more_calls(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    out = tmp_path / "recall.tsv"
    llm = StubLlm(GOOD, sequences={"q02": (replace(GOOD["q02"], key=RecallKey(("Lambda",))), GOOD["q02"])})
    # q02 の1回目の後に、overage か枠切れを見たことにする。作り直しも含めて残りを発行しない。
    with pytest.raises(RecallCallsFailed) as exc:
        generate(llm, deck, clusters, Writer(out), stop_seen=lambda: len(llm.recalled) >= 2)
    assert llm.recalled == ["q01", "q02"]
    assert "失敗 0 回、発行 1 / 5" in str(exc.value.code)
    assert not out.exists()


def test_entailment_failure_is_regenerated_within_the_same_limit(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path
) -> None:
    """A-5: 含意判定に落ちた問題も、検査名 `entailment` として同じ上限の中で作り直す(D-232)。"""
    answer = GOOD["q01"].answers[0].text
    # 判定器が q01 の答えを2回落としてから通すと、q01 は3回生成されて成果物が書かれる。
    out = tmp_path / "twice.tsv"
    writer = Writer(out)
    llm = StubLlm(GOOD, rejections={answer: 2})
    generate(llm, deck, clusters, writer)
    assert out.exists() and writer.result is not None
    assert llm.recalled == ["q01"] * 3 + ["q02", "q03", "q04", "q05"]
    assert {q.local_id: n for q, n in writer.result.report.regenerations}["q01"] == 2
    assert [statement for _, statement in llm.checked].count(answer) == 3

    # 3回落とすと、残りの問題を回し切ってから RegenerationExhausted で止まり、何も書かない。
    out = tmp_path / "thrice.tsv"
    llm = StubLlm(GOOD, rejections={answer: 3})
    with pytest.raises(RegenerationExhausted) as exc:
        generate(llm, deck, clusters, Writer(out))
    assert exc.value.code not in (None, 0)
    assert [(e.question_id.local_id, e.failures, e.attempts) for e in exc.value.exhausted] == [
        ("q01", (ENTAILMENT,), 1 + MAX_REGENERATIONS)
    ]
    assert llm.recalled == ["q01"] * 3 + ["q02", "q03", "q04", "q05"]
    assert judged(llm) == {"q01", "q02", "q03", "q04", "q05"}
    assert answer not in str(exc.value.code)
    assert not out.exists()

    # 判定できない(uncertain)ものも落ちとして作り直し、通らなければ止まる(D-042)。
    unsure = GOOD["q05"].discrimination.text
    llm = StubLlm(GOOD, uncertain=frozenset({unsure}))
    with pytest.raises(RegenerationExhausted) as exc:
        generate(llm, deck, clusters, Writer(out))
    assert [(e.question_id.local_id, e.failures) for e in exc.value.exhausted] == [("q05", (ENTAILMENT,))]
    assert not out.exists()

    # 機械検査と含意判定の落ちは同じ上限を共有する: 1回目は実在性、2回目は含意判定、3回目で通る。
    ungrounded = replace(GOOD["q01"], key=RecallKey(("Lambda",)))
    contradicting = replace(GOOD["q01"], answers=(AnswerElement(GOOD["q01"].answers[0].option_id, CONTRADICTING),))
    llm = StubLlm(
        GOOD,
        contradicted=frozenset({CONTRADICTING}),
        sequences={"q01": (ungrounded, contradicting, GOOD["q01"])},
    )
    writer = Writer(tmp_path / "shared.tsv")
    generate(llm, deck, clusters, writer)
    assert writer.result is not None
    assert {q.local_id: n for q, n in writer.result.report.regenerations}["q01"] == 2
    # 実在性に落ちた1回目の素材は判定にかけていない。
    assert [s for _, s in llm.checked].count(answer) == 1

    # 含意判定の呼び出しの失敗は作り直さず、その問題の失敗に数え、残りを回し切ってから止まる。
    out = tmp_path / "check-failed.tsv"
    llm = StubLlm(GOOD, failing_checks=frozenset({GOOD["q02"].discrimination.text}))
    with pytest.raises(RecallCallsFailed) as failed:
        generate(llm, deck, clusters, Writer(out))
    assert llm.recalled == [qid for _, qid in ORDER]
    assert "失敗 1 回、発行 5 / 5" in str(failed.value.code)
    assert not out.exists()


def test_regeneration_log_names_question_id_and_counts_only(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A-9: 作り直しのたびにログが1行出て、問題 ID・検査名・回数だけを含む(NFR-2)。"""
    caplog.set_level(logging.INFO, logger="quiz_sift.recall.generate")
    ungrounded = replace(GOOD["q03"], key=RecallKey(("Lambda",)))
    answer = GOOD["q01"].answers[0].text
    llm = StubLlm(GOOD, rejections={answer: 1}, sequences={"q03": (ungrounded, ungrounded, GOOD["q03"])})
    generate(llm, deck, clusters, Writer(tmp_path / "recall.tsv"))
    lines = [r.getMessage() for r in caplog.records if "作り直す" in r.getMessage()]
    assert lines == [
        "recall: 問題 q01 を作り直す(entailment; 1 回目)",
        "recall: 問題 q03 を作り直す(ungrounded_key; 1 回目)",
        "recall: 問題 q03 を作り直す(ungrounded_key; 2 回目)",
    ]
    # 素材の本文(キーの語句・答え・識別ポイント)はどのログにも出ない。
    texts = {"Lambda", answer} | {t for d in GOOD.values() for t in (*d.key.terms, d.discrimination.text)}
    assert not any(text in r.getMessage() for r in caplog.records for text in texts)


def test_recall_references_hold_the_right_sections(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    out = tmp_path / "recall.tsv"
    llm = StubLlm(GOOD)
    generate(llm, deck, clusters, Writer(out))
    assert out.exists()
    # 全素材の答えの要素と識別ポイントが判定されてから書き出される。
    expected = [s for d in GOOD.values() for s in (*(e.text for e in d.answers), d.discrimination.text)]
    assert sorted(statement for _, statement in llm.checked) == sorted(expected)
    reference, statements = next(call for call in llm.calls if call[1][0].statement == GOOD["q01"].answers[0].text)
    # reference はその問題の正解・公式解説で、誤答の本文と他の問題は入らない。
    assert "正解: S3 標準-IA" in reference and "ミリ秒単位で取り出せる" in reference
    assert "S3 Glacier Deep Archive" not in reference and "IAM" not in reference
    # 答えの要素は、その要素が説明する正解を focus に持ち、誤答の本文を持たない(D-210)。
    answer, discrimination = statements
    assert (answer.focus, answer.distractors) == ("S3 標準-IA", ())
    # 識別ポイントは参照先の誤答の本文だけを distractors に持つ(D-192)。q01 は C = S3 Glacier Deep Archive。
    assert (discrimination.focus, discrimination.distractors) == (None, ("S3 Glacier Deep Archive",))
    _, (_, q05) = next(call for call in llm.calls if call[1][1].statement == GOOD["q05"].discrimination.text)
    assert q05.distractors == ("リードレプリカ",)


def _two_answer_deck() -> tuple[Deck, tuple[Cluster, ...]]:
    """正解 2 件(A・C)、誤答 2 件(B・D)の問題 m01 だけのデッキ。"""
    qid = QuestionId(DECK, "m01")
    texts = {"A": "最小権限の原則", "B": "ルートユーザーの常用", "C": "職務分掌", "D": "アクセスキーの共有"}
    options = tuple(Option(OptionId(qid, label), text) for label, text in texts.items())
    question = Question(
        qid,
        "IAM の設計で推奨される原則を2つ選べ。",
        options,
        (options[0].option_id, options[2].option_id),
        "最小権限の原則と職務分掌はどちらも推奨される。",
        StudiedFlag.UNKNOWN,
    )
    deck = Deck(DECK, Language.JA, None, (question,))
    return deck, (Cluster(ClusterId(DECK, "c001"), "c001", (qid,)),)


def _two_answer_draft(second: str) -> RecallDraft:
    qid = QuestionId(DECK, "m01")
    return RecallDraft(
        RecallKey(("IAM の設計", "推奨される原則")),
        (AnswerElement(OptionId(qid, "A"), "必要な権限だけを与える"), AnswerElement(OptionId(qid, "C"), second)),
        DiscriminationPoint("ルートユーザーは日常の作業に使わない", (OptionId(qid, "B"),)),
    )


def test_entailment_is_one_call_per_question(tmp_path: Path) -> None:
    """T-084 A-2: 1回の生成につき判定を1回だけ呼ぶ。reference は正解 N 件と解説で、focus と誤答は主張の側。"""
    deck, clusters = _two_answer_deck()
    contradicting = "職務分掌は1人に全権限を集めることである"
    good, bad = _two_answer_draft("権限を複数人に分ける"), _two_answer_draft(contradicting)

    # 要素2だけが contradicted の1回目を作り直し、2回目で通る。生成1回につき判定は1回。
    llm = StubLlm({"m01": good}, contradicted=frozenset({contradicting}), sequences={"m01": (bad, good)})
    writer = Writer(tmp_path / "twice.tsv")
    generate(llm, deck, clusters, writer)
    assert llm.recalled == ["m01", "m01"]
    assert len(llm.calls) == 2
    assert writer.result is not None
    assert {q.local_id: n for q, n in writer.result.report.regenerations} == {"m01": 1}
    reference, statements = llm.calls[0]
    assert reference == (
        "問題: IAM の設計で推奨される原則を2つ選べ。 正解: 最小権限の原則、職務分掌 "
        "解説: 最小権限の原則と職務分掌はどちらも推奨される。"
    )
    assert statements == (
        EntailmentStatement("answer-0", "必要な権限だけを与える", focus="最小権限の原則"),
        EntailmentStatement("answer-1", contradicting, focus="職務分掌"),
        EntailmentStatement(
            "discrimination", "ルートユーザーは日常の作業に使わない", distractors=("ルートユーザーの常用",)
        ),
    )
    # 答えの要素の主張には誤答の本文が無い。
    for answer in statements[:2]:
        assert answer.distractors == ()
        assert not any(text in answer.statement for text in ("ルートユーザーの常用", "アクセスキーの共有"))

    # 要素2が毎回 contradicted なら、上限まで作り直して RegenerationExhausted で止まる。判定は生成ごとに1回。
    llm = StubLlm({"m01": bad}, contradicted=frozenset({contradicting}))
    out = tmp_path / "exhausted.tsv"
    with pytest.raises(RegenerationExhausted) as exc:
        generate(llm, deck, clusters, Writer(out))
    assert [(e.question_id.local_id, e.failures, e.attempts) for e in exc.value.exhausted] == [
        ("m01", (ENTAILMENT,), 1 + MAX_REGENERATIONS)
    ]
    assert len(llm.calls) == len(llm.recalled) == 1 + MAX_REGENERATIONS
    assert not out.exists()


def _progress(tmp_path: Path) -> Progress:
    return Progress(tmp_path / "recall.progress")


def test_resume_skips_questions_that_passed(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A-2(ADR-028): 止まった後の再実行は残りの問題だけを呼び、重複の検査は戻した問題を前の問題として数える。"""
    caplog.set_level(logging.INFO, logger="quiz_sift.recall.generate")
    progress, out = _progress(tmp_path), tmp_path / "recall.tsv"
    # q04 の1回目は q01 と同じキー(別クラスタ)。q01 を前の問題として数えれば duplicate_key でも落ちる。
    duplicated = replace(GOOD["q04"], key=GOOD["q01"].key)
    sequences: dict[str, tuple[RecallDraft, ...]] = {"q04": (duplicated, GOOD["q04"])}

    first = StubLlm(GOOD, sequences=sequences)
    # 2問目の後に枠切れを見たことにする。
    with pytest.raises(RecallCallsFailed):
        generate(first, deck, clusters, Writer(out), stop_seen=lambda: len(first.recalled) >= 2, progress=progress)
    assert first.recalled == ["q01", "q02"]
    assert not out.exists()

    caplog.clear()
    second = StubLlm(GOOD, sequences=sequences)
    writer = Writer(out)
    generate(second, deck, clusters, writer, progress=progress)
    # 通過していた問題は呼ばず、判定にもかけない。
    assert second.recalled == ["q03", "q04", "q04", "q05"]
    assert judged(second) == {"q03", "q04", "q05"}
    regenerations = {"q01": 0, "q02": 0, "q03": 0, "q04": 1, "q05": 0}
    assert len(first.recalled) + len(second.recalled) == len(ORDER) + sum(regenerations.values())
    # 成果物は全問の想起素材で書かれる。
    assert writer.result is not None
    assert [(m.cluster_id.local_id, m.question_id.local_id) for m in writer.result.materials] == ORDER
    assert [m.draft for m in writer.result.materials] == list(GOOD.values())
    assert {q.local_id: n for q, n in writer.result.report.regenerations} == regenerations
    (regenerated,) = [r.getMessage() for r in caplog.records if "を作り直す" in r.getMessage()]
    assert "q04" in regenerated and "duplicate_key" in regenerated
    assert "recall resume: reused=2 remaining=3" in caplog.messages


class StopsOnCall(StubLlm):
    """`stop_after` 回目の `generate_recall` の呼び出しを、枠切れの失敗(出力なし)にする。"""

    def __init__(self, drafts: dict[str, RecallDraft], stop_after: int, **kwargs: Any) -> None:
        super().__init__(drafts, **kwargs)
        self.stop_after = stop_after
        self.stopped = False

    def generate_recall(
        self, question: Question, language: Language, retry: Retry[RecallDraft] | None = None
    ) -> RecallDraft:
        if len(self.recalled) + 1 == self.stop_after:
            self.recalled.append(question.question_id.local_id)
            self.stopped = True
            raise LlmCallError("合成の枠切れ")
        return super().generate_recall(question, language, retry)


def test_regeneration_cap_holds_across_resumes(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    """A-3(ADR-028、AR-14、AR-25): 作り直しの上限は再開をまたいで数え、判定値のある主張は聞き直さない。"""
    ungrounded = replace(GOOD["q01"], key=RecallKey(("Lambda",)))

    # (a) 上限を超えた q01 の後に枠切れで止まると、再開しても q01 を作り直さずに RegenerationExhausted。
    progress, out = Progress(tmp_path / "a"), tmp_path / "a.tsv"
    first = StubLlm({**GOOD, "q01": ungrounded})
    with pytest.raises(RecallCallsFailed):
        generate(first, deck, clusters, Writer(out), stop_seen=lambda: len(first.recalled) >= 4, progress=progress)
    assert first.recalled == ["q01"] * 3 + ["q02"]
    second = StubLlm(GOOD)  # 作り直せば通る素材を返す。
    with pytest.raises(RegenerationExhausted) as exc:
        generate(second, deck, clusters, Writer(out), progress=progress)
    assert "q01" not in second.recalled and "q02" not in second.recalled
    assert [(e.question_id.local_id, e.failures, e.attempts) for e in exc.value.exhausted] == [
        ("q01", ("ungrounded_key",), 1 + MAX_REGENERATIONS)
    ]
    assert not out.exists()

    # (b) 2回不合格の後、3回目の生成の呼び出しが枠切れで失敗して止まると、再開ではそれを3回目として生成する。
    progress, out = Progress(tmp_path / "b"), tmp_path / "b.tsv"
    stopping = StopsOnCall({**GOOD, "q01": ungrounded}, stop_after=3)
    with pytest.raises(RecallCallsFailed):
        generate(stopping, deck, clusters, Writer(out), stop_seen=lambda: stopping.stopped, progress=progress)
    assert stopping.recalled == ["q01"] * 3  # 3回目は出力なし
    second = StubLlm({**GOOD, "q01": ungrounded})
    with pytest.raises(RegenerationExhausted) as exc:
        generate(second, deck, clusters, Writer(out), progress=progress)
    assert second.recalled.count("q01") == 1
    outputs = (len(stopping.recalled) - 1) + second.recalled.count("q01")
    assert outputs == 1 + MAX_REGENERATIONS
    assert [(e.question_id.local_id, e.attempts) for e in exc.value.exhausted] == [("q01", 1 + MAX_REGENERATIONS)]
    assert not out.exists()

    # (c) 判定の呼び出しが枠切れで失敗して止まると、再開では同じ生成物を作り直さずに1回で判定し直す。
    progress, out = Progress(tmp_path / "c"), tmp_path / "c.tsv"
    answer = GOOD["q01"].answers[0].text
    discrimination = GOOD["q01"].discrimination.text
    first = StubLlm(GOOD, failing_checks=frozenset({discrimination}))
    with pytest.raises(RecallCallsFailed):
        generate(first, deck, clusters, Writer(out), stop_seen=lambda: len(first.calls) >= 1, progress=progress)
    assert first.recalled == ["q01"] and len(first.calls) == 1
    second = StubLlm(GOOD)
    generate(second, deck, clusters, Writer(out), progress=progress)
    assert "q01" not in second.recalled
    assert [s.statement for s in second.calls[0][1]] == [answer, discrimination]

    # (d) uncertain の判定値が返った後に止まると、再開ではその判定値を聞き直さず、2回目として作り直す。
    progress, out = Progress(tmp_path / "d"), tmp_path / "d.tsv"
    first = StubLlm(GOOD, uncertain=frozenset({answer}))
    with pytest.raises(RecallCallsFailed):
        generate(first, deck, clusters, Writer(out), stop_seen=lambda: len(first.calls) >= 1, progress=progress)
    assert first.recalled == ["q01"] and len(first.calls) == 1
    second = StubLlm(GOOD)  # どの主張にも consistent を返す。
    writer = Writer(out)
    generate(second, deck, clusters, writer, progress=progress)
    assert second.recalled.count("q01") == 1
    assert [qid for qid, stem, *_ in ROWS if second.calls[0][0].startswith(f"問題: {stem}")] == ["q01"]
    assert len([c for c in second.calls if c[1][0].statement == answer]) == 1
    assert writer.result is not None
    assert dict((q.local_id, n) for q, n in writer.result.report.regenerations)["q01"] == 1


def test_resumed_pass_cannot_share_a_key_with_a_question_filled_in_before_it(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """前方の問題の呼び出しが失敗し、後方の問題だけが通過して戻される経路でも、成果物に重複キーは入らない。

    戻した通過の問題は重複の検査を受け直さない。それで足りるのは、別クラスタの2問が同じ組のキーを持つと、
    全 term が両方の問題文にあって互いの他クラスタに出現するので、どちらも `generic` で落ちるからである。
    この前提が崩れたら、このテストが赤になる。
    """
    caplog.set_level(logging.INFO, logger="quiz_sift.recall.generate")
    progress, out = _progress(tmp_path), tmp_path / "recall.tsv"
    shared = RecallKey(("S3",))  # q01(c001)と q04(c002)の両方の問題文にある。
    drafts = {**GOOD, "q01": replace(GOOD["q01"], key=shared)}
    sequences: dict[str, tuple[RecallDraft, ...]] = {"q04": (replace(GOOD["q04"], key=shared), GOOD["q04"])}

    # 1回目: q01 の呼び出しが失敗する(枠切れではないので残りは回る)。q04 は q01 が無いまま検査される。
    first = StubLlm(drafts, sequences=sequences, failing=frozenset({"q01"}))
    with pytest.raises(RecallCallsFailed):
        generate(first, deck, clusters, Writer(out), progress=progress)
    (regenerated,) = [r.getMessage() for r in caplog.records if "を作り直す" in r.getMessage()]
    assert "q04" in regenerated and "generic" in regenerated
    assert first.recalled == ["q01", "q02", "q03", "q04", "q04", "q05"]

    # 再開: q01 を q04 と同じ組のキーで埋めようとしても、q01 自身が generic で落ちて成果物は書かれない。
    with pytest.raises(RegenerationExhausted) as exc:
        generate(StubLlm(drafts), deck, clusters, Writer(out), progress=progress)
    assert [(e.question_id.local_id, e.failures) for e in exc.value.exhausted] == [("q01", ("generic",))]
    assert not out.exists()


def test_statements_refuse_a_misplaced_focus_or_distractor(deck: Deck) -> None:
    """focus は正解の選択肢、distractors は同じ問題の誤答の選択肢に限る(取り違えは通さない)。"""
    q01 = next(q for q in deck.questions if q.question_id.local_id == "q01")  # 正解は B

    def material(recall: RecallDraft) -> RecallMaterial:
        return RecallMaterial(ClusterId(DECK, "c001"), q01.question_id, recall)

    good = GOOD["q01"]
    statements = _statements(material(good), q01)
    assert [s.focus for s in statements] == ["S3 標準-IA", None]
    assert statements[-1].distractors == ("S3 Glacier Deep Archive",)

    wrong_focus = replace(good, answers=(AnswerElement(option("q01", "A"), "S3 標準"),))
    with pytest.raises(ValueError, match="^答えの要素の focus はこの問題の正解の選択肢でなければならない$"):
        _statements(material(wrong_focus), q01)
    text = good.discrimination.text
    for distractors in ((option("q01", "B"),), (option("q02", "B"),), (option("q01", "C"), option("q01", "B"))):
        bad = replace(good, discrimination=DiscriminationPoint(text, distractors))
        with pytest.raises(ValueError, match="^distractors はこの問題の誤答の選択肢でなければならない$"):
            _statements(material(bad), q01)


def test_regeneration_receives_the_previous_material_and_why_it_failed(
    deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T-086 A-2: 作り直しは前回の素材と、それまでの全回の落ちた検査とその対象を受け取る。再開しても同じ(D-245)。"""
    caplog.set_level(logging.INFO)
    good = GOOD["q01"]  # 正解は B、識別ポイントは誤答 C を参照する
    # 1回目: キーが問題文に無く(ungrounded_key)、答えが誤答 A を指し(answer_mismatch)、識別ポイントが正解 B を指す。
    first = replace(
        good,
        key=RecallKey(("Lambda",)),
        answers=(AnswerElement(option("q01", "A"), "S3 標準"),),
        discrimination=DiscriminationPoint(good.discrimination.text, (option("q01", "B"),)),
    )
    # 2回目: 機械検査は通るが、答えが正解・解説と矛盾する(entailment)。
    second = replace(good, answers=(AnswerElement(option("q01", "B"), CONTRADICTING),))
    sequences: dict[str, tuple[RecallDraft, ...]] = {"q01": (first, second, good)}
    contradicted = frozenset({CONTRADICTING})

    llm = StubLlm(GOOD, contradicted=contradicted, sequences=sequences)
    generate(llm, deck, clusters, Writer(tmp_path / "once.tsv"))
    retries = [retry for qid, retry in llm.retries if qid == "q01"]
    assert retries[0] is None
    assert all(retry is None for qid, retry in llm.retries if qid != "q01")
    after_first, after_second = retries[1], retries[2]
    assert after_first is not None and after_second is not None
    assert (after_first.previous, after_second.previous) == (first, second)

    def shown(retry: Retry[RecallDraft]) -> list[tuple[object, ...]]:
        return [(p.check, p.terms, p.options, p.elements, p.statements) for p in retry.problems]

    by_first = [
        ("ungrounded_key", ("Lambda",), (), (), ()),
        ("answer_mismatch", (), (option("q01", "A"), option("q01", "B")), (), ()),
        ("missing_options", (), (option("q01", "B"),), (), ()),
    ]
    assert shown(after_first) == by_first
    # 3回目は1回目の分も累積して受け取る。
    by_second = [("entailment", (), (), (), ((CONTRADICTING, Entailment.CONTRADICTED),))]
    assert shown(after_second) == by_first + by_second

    # アダプタへは選択肢を不透明キー(o1…)で渡し、元のラベルを渡さない(D-206)。
    _, keyed = llm_prompts.recall_payload(question(deck, "q01"))
    keys = {option_id: key for key, option_id in keyed.items()}
    rendered = [llm_prompts.problem_json(p, keys) for p in after_second.problems]
    assert rendered[1] == {"check": "answer_mismatch", "options": ["o1", "o2"]}
    assert llm_prompts.recall_draft_json(after_first.previous, keys)["answers"] == [{"option": "o1", "text": "S3 標準"}]

    # 2回目の判定で落ちた後に止まり、--resume すると、3回目は止まらなかった場合と同じ理由を受け取る。
    progress = Progress(tmp_path / "recall.progress")
    stopping = StubLlm(GOOD, contradicted=contradicted, sequences=sequences)
    with pytest.raises(RecallCallsFailed):
        generate(stopping, deck, clusters, Writer(tmp_path / "a.tsv"), lambda: len(stopping.recalled) >= 2, progress)
    assert stopping.recalled == ["q01", "q01"]
    resumed = StubLlm(GOOD)
    generate(resumed, deck, clusters, Writer(tmp_path / "b.tsv"), progress=progress)
    (_, retry), *_ = resumed.retries
    assert retry is not None and retry.previous == second
    assert shown(retry) == by_first + by_second

    # ログは検査の名前と回数だけで、語句・主張・選択肢の本文は出ない。理由は repr にも出ない。
    messages = [r.getMessage() for r in caplog.records]
    assert any("q01 を作り直す(ungrounded_key,answer_mismatch,missing_options; 1 回目)" in m for m in messages)
    assert any("q01 を作り直す(entailment; 2 回目)" in m for m in messages)
    for text in ("Lambda", CONTRADICTING, "S3 標準", good.discrimination.text):
        assert not any(text in m for m in messages), text
        assert text not in repr(retry) and text not in repr(retry.problems)
