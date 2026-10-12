"""クラスタ・想起・グロッサリ・台本・含意判定の受入テスト(C-003 / C-004 / C-005 / C-006 / C-013)。claim ごとに1本。

すべて `cli.main` を conftest の実行ヘルパ越しに走らせ、成果物(`out/<deck>/<段>/`)とログを読む。
本文はすべて合成で、実教材は含まない(NFR-2)。問題は既知5グループの合成フィクスチャ
`tests/cluster/fixtures/known5.jsonl` を使う。C-003 は5グループ16問すべて、それ以外は ec2 / iam / rds の
3グループ9問に絞る(想起素材の下書きを問題ごとに書くため)。

キャッシュは前段の成果物で引かれるので、stub の振る舞いを変えた実行はデッキ ID を分けて走らせる。
"""

import io
import json
import logging
import re
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from support.claude_fake import FakeRunner, exited, fake_stdout
from support.cli_harness import DECK, CliLlms, flag, no_preflight, recall_output, respond, write_root
from support.cli_harness import ROWS as DECK_ROWS

from quiz_sift import cli
from quiz_sift.cluster.metrics import ClusterMetrics, degeneracy_violations
from quiz_sift.cluster.run import ClassificationFailed
from quiz_sift.cluster.summary import UngroundedSummary
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.grounding.presence import is_present
from quiz_sift.grounding.terms import extract_terms
from quiz_sift.ports.llm import CallFailure, ClassificationResult, Entailment, GroupProposal, LlmCallError, RecallDraft
from quiz_sift.ports.process import CommandResult, CommandSpec
from quiz_sift.recall.generate import MAX_REGENERATIONS, RecallCallsFailed, RegenerationExhausted
from quiz_sift.safety.entailment import DISTRACTOR_SECTION, FOCUS_SECTION, EntailmentRejected
from quiz_sift.script.generate import LabelEnumerationFound, MissingAnswerElement, ScriptTooLong

from .conftest import CliRunner, StubLlm, StubTts, write_deck

KNOWN5 = Path(__file__).resolve().parents[1] / "cluster" / "fixtures" / "known5.jsonl"
SUBSET = ("ec2", "iam", "rds")
"""C-004 以降で使うグループ。クラスタ ID は c001=ec2, c002=iam, c003=rds になる。"""


def _fixture() -> list[dict[str, Any]]:
    return [json.loads(line) for line in KNOWN5.read_text(encoding="utf-8").splitlines() if line.strip()]


LABELS = "ABCDEF"
"""選択肢のラベル。フィクスチャの選択肢は4件で、C-006 の試験は5件・6件の問題も作る。"""


def _correct(row: Mapping[str, Any]) -> list[int]:
    """正解の選択肢の位置。フィクスチャは単一正解(`correct`)、この試験で足す複数正解の問題は `corrects`。"""
    return list(row["corrects"]) if "corrects" in row else [row["correct"]]


def _deck_row(row: Mapping[str, Any], *, explained: bool = True) -> dict[str, Any]:
    return {
        "id": row["id"],
        "stem": row["stem"],
        "options": [{"id": label, "text": text} for label, text in zip(LABELS, row["options"], strict=False)],
        "answers": [LABELS[i] for i in _correct(row)],
        "explanation": row["explanation"] if explained else None,
    }


def _groups(rows: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    groups: dict[str, tuple[str, ...]] = {}
    for row in rows:
        groups[row["group"]] = (*groups.get(row["group"], ()), row["id"])
    return groups


ROWS = _fixture()
GROUPS = _groups(ROWS)
SUBSET_ROWS = [row for row in ROWS if row["group"] in SUBSET]

# 各グループの問題文に語句が実在する要約。
SUMMARIES = {
    "s3": "S3 のストレージクラスはアクセス頻度で選ぶ",
    "ec2": "EC2 の購入オプション",
    "iam": "IAM ポリシーでは明示的な拒否が優先される",
    "rds": "RDS のマルチ AZ 配置とリードレプリカ",
    "eventbridge": "EventBridge のルールで Lambda を起動する",
}

_DRAFT_DECK = DeckId("drafts")
"""下書きの選択肢 ID を置くデッキ。`AnalysisLlm` が呼ばれた問題のデッキへ写し替える(デッキ ID は試験ごとに違う)。"""


def _oid(qid: str, label: str) -> OptionId:
    return OptionId(QuestionId(_DRAFT_DECK, qid), label)


def _draft(
    qid: str, terms: tuple[str, ...], answers: Mapping[str, str], discrimination: str, distractors: str
) -> RecallDraft:
    """`answers` は選択肢ラベル → 答えの要素、`distractors` は識別ポイントが参照する誤答のラベルの列。"""
    return RecallDraft(
        RecallKey(terms),
        tuple(AnswerElement(_oid(qid, label), text) for label, text in answers.items()),
        DiscriminationPoint(discrimination, tuple(_oid(qid, label) for label in distractors)),
    )


def _rebind(draft: RecallDraft, question: Question) -> RecallDraft:
    def move(option_id: OptionId) -> OptionId:
        return OptionId(question.question_id, option_id.local_id)

    return RecallDraft(
        draft.key,
        tuple(AnswerElement(move(e.option_id), e.text) for e in draft.answers),
        DiscriminationPoint(draft.discrimination.text, tuple(move(o) for o in draft.discrimination.distractor_ids)),
    )


def _answered(draft: RecallDraft, text: str) -> RecallDraft:
    """単一正解の下書きの答えを `text` に差し替える。"""
    (element,) = draft.answers
    return replace(draft, answers=(AnswerElement(element.option_id, text),))


def _discriminated(draft: RecallDraft, text: str) -> RecallDraft:
    return replace(draft, discrimination=DiscriminationPoint(text, draft.discrimination.distractor_ids))


# すべての検査を通る想起素材。キーの語句は問題文に実在し、識別ポイントは誤答選択肢を ID で参照する。
DRAFTS = {
    "q05": _draft(
        "q05",
        ("中断", "バッチ処理"),
        {"B": "スポットインスタンス"},
        "中断を許せるなら最安はスポットで、リザーブドインスタンスは長期の確約で割り引く仕組み",
        "C",
    ),
    "q06": _draft(
        "q06",
        ("常時稼働", "本番サーバー"),
        {"C": "リザーブドインスタンス"},
        "1年以上動かし続けるなら期間を確約して割引を受け、スポットインスタンスは中断され得るので本番に向かない",
        "A",
    ),
    "q07": _draft(
        "q07",
        ("ソフトウェアライセンス", "物理サーバー"),
        {"A": "Dedicated Hosts"},
        "サーバー単位のライセンスは物理サーバーを専有して持ち込み、Savings Plans は料金を割り引くだけ",
        "D",
    ),
    "q08": _draft(
        "q08",
        ("アプリケーション", "推奨方法"),
        {"B": "IAM ロールをインスタンスに割り当てる"},
        "一時的な認証情報が自動で更新されるロールを使い、アクセスキーをコードに埋め込むような固定の認証情報は避ける",
        "A",
    ),
    "q09": _draft(
        "q09",
        ("明示的な拒否", "許可"),
        {"B": "拒否"},
        "明示的な拒否は常に優先され、先に書かれた方のように書いた順では決まらない",
        "CD",
    ),
    "q10": _draft(
        "q10",
        ("必要最小限", "権限"),
        {"A": "最小権限の原則"},
        "必要な操作だけを許すのが最小権限で、多層防御は防御を何重にも重ねる別の考え方",
        "B",
    ),
    "q11": _draft(
        "q11",
        ("フェイルオーバー", "別のアベイラビリティーゾーン"),
        {"B": "マルチ AZ 配置"},
        "スタンバイへ自動で切り替わるのはマルチ AZ 配置で、リードレプリカは読み取りの負荷分散のためのもの",
        "A",
    ),
    "q12": _draft(
        "q12",
        ("読み取り負荷", "分散"),
        {"B": "リードレプリカ"},
        "読み取り専用のコピーを非同期で作るのがリードレプリカで、マルチ AZ 配置は可用性のための待機系",
        "A",
    ),
    "q13": _draft(
        "q13",
        ("スタンバイ", "レプリケーション方式"),
        {"B": "同期"},
        "マルチ AZ のスタンバイは同期で複製され、非同期で複製するのはリードレプリカ",
        "A",
    ),
}


@dataclass
class AnalysisLlm(StubLlm):
    """`groups`(ラベル → 問題の local_id)どおりに分類し、要約は `summaries` を返す。

    `failing` のグループは分割呼び出しの失敗として返す。含意判定は `contradicted` / `uncertain` に
    入っている主張にだけその判定を返し、ほかは `consistent`。`contradicts` を渡すと、それが真を返す
    (reference, statement) にも `contradicted` を返す。判定にかけた主張は `checked`、reference と組で
    `judged` に残る。

    想起素材は、`sequences` にある問題なら呼ばれるたびにその列を順に返し(尽きたら最後を返し続ける)、
    無ければ `drafts` を返す。どちらも呼ばれた問題のデッキへ選択肢 ID を写し替える。`call_failures` の
    問題は `LlmCallError` を上げる。呼ばれた問題は `recalled` に残る。
    """

    groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    summaries: Mapping[str, str] = field(default_factory=dict)
    failing: frozenset[str] = frozenset()
    contradicted: frozenset[str] = frozenset()
    uncertain: frozenset[str] = frozenset()
    checked: list[str] = field(default_factory=list)
    contradicts: Callable[[str, str], bool] = lambda reference, statement: False
    judged: list[tuple[str, str]] = field(default_factory=list)
    sequences: Mapping[str, Sequence[RecallDraft]] = field(default_factory=dict)
    call_failures: frozenset[str] = frozenset()
    recalled: list[str] = field(default_factory=list)

    def generate_recall(self, question: Question, language: Language) -> RecallDraft:
        self.calls.append("recall")
        qid = question.question_id.local_id
        self.recalled.append(qid)
        if qid in self.call_failures:
            raise LlmCallError("合成の呼び出しの失敗")
        sequence = self.sequences.get(qid, (self.drafts[qid],))
        return _rebind(sequence[min(self.recalled.count(qid), len(sequence)) - 1], question)

    def _proposals(self, deck: Deck) -> list[GroupProposal]:
        present = {q.question_id.local_id for q in deck.questions}
        proposals = []
        for label, ids in self.groups.items():
            members = tuple(QuestionId(deck.deck_id, i) for i in ids if i in present)
            if members:
                proposals.append(GroupProposal(label, members))
        return proposals

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        self.calls.append("classify")
        proposals = self._proposals(deck)
        ok = tuple(p for p in proposals if p.label not in self.failing)
        failed = tuple(CallFailure(p.question_ids, "LlmCallError: 429") for p in proposals if p.label in self.failing)
        return ClassificationResult(ok, failed)

    def summarize_cluster(self, members: Deck) -> str:
        first = members.questions[0].question_id.local_id
        label = next(label for label, ids in self.groups.items() if first in ids)
        if label not in self.summaries:
            return super().summarize_cluster(members)
        self.calls.append("summarize")
        return self.summaries[label]

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        super().check_entailment(reference, statement)
        self.checked.append(statement)
        self.judged.append((reference, statement))
        if statement in self.contradicted or self.contradicts(reference, statement):
            return Entailment.CONTRADICTED
        if statement in self.uncertain:
            return Entailment.UNCERTAIN
        return Entailment.CONSISTENT


def _subset_llm(**changes: Any) -> AnalysisLlm:
    return AnalysisLlm(**{"drafts": DRAFTS, "groups": GROUPS, "summaries": SUMMARIES, **changes})


def _deck(workspace: Path, name: str, rows: list[dict[str, Any]] = SUBSET_ROWS, *, explained: bool = True) -> DeckId:
    deck_id = DeckId(name)
    write_deck(workspace, deck_id, [_deck_row(row, explained=explained) for row in rows])
    return deck_id


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _artifacts(workspace: Path, deck_id: DeckId, stage: str) -> list[Path]:
    """`out/<deck>/<段>/` に残ったファイル。書きかけの一時ファイルも数える。"""
    directory = workspace / "out" / deck_id / stage
    return sorted(directory.iterdir()) if directory.is_dir() else []


def _texts(row: Mapping[str, Any]) -> list[str]:
    return [row["stem"], *row["options"], row["explanation"]]


def _fails(run: CliRunner, *argv: str) -> SystemExit:
    with pytest.raises(SystemExit) as exc:
        run(*argv)
    assert exc.value.code not in (None, 0)
    return exc.value


_SUMMARY_LINE = re.compile(
    r"clustering summary: n=(\d+) m=(\d+) m_over_n=([\d.]+) other_ratio=([\d.]+) "
    r"max_cluster_ratio=([\d.]+) failure_count=(\d+)"
)


def _summary_line(caplog: pytest.LogCaptureFixture) -> re.Match[str]:
    found = [m for r in caplog.records if (m := _SUMMARY_LINE.fullmatch(r.getMessage()))]
    assert len(found) == 1
    return found[0]


# ---- C-003 ----


def test_c003_clusters_are_exclusive_and_summaries_grounded(
    workspace: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    assert len(GROUPS) == 5 and all(len(ids) >= 3 for ids in GROUPS.values())
    rows = {row["id"]: row for row in ROWS}
    deck_id = _deck(workspace, "known5", ROWS)
    llm = AnalysisLlm(groups=GROUPS, summaries=SUMMARIES)
    run = CliRunner(workspace, capsys, llm, StubTts())

    clustered = _load(run("cluster", deck_id))

    # 排他クラスタ: 全問題がちょうど1クラスタに属し、既知の5グループと一致する。
    members = [q for cluster in clustered["clusters"] for q in cluster["questions"]]
    assert sorted(members) == sorted(rows)
    assert len(members) == len(set(members))
    assert sorted(tuple(c["questions"]) for c in clustered["clusters"]) == sorted(GROUPS.values())

    # M/N・その他比率・最大クラスタ比率が成果物とサマリの両方に記録され、退化ガードの範囲に収まる。
    metrics = ClusterMetrics(**clustered["metrics"])
    assert (metrics.question_count, metrics.cluster_count, metrics.other_count, metrics.failure_count) == (16, 5, 0, 0)
    assert metrics.max_cluster_size == 4
    assert metrics.max_cluster_ratio <= 0.5
    assert metrics.other_ratio <= 0.2
    assert degeneracy_violations(metrics) == ()
    line = _summary_line(caplog)
    assert (int(line[1]), int(line[2])) == (16, 5)
    assert float(line[3]) == pytest.approx(metrics.m_over_n, abs=1e-4)
    assert float(line[4]) == pytest.approx(metrics.other_ratio, abs=1e-4)
    assert float(line[5]) == pytest.approx(metrics.max_cluster_ratio, abs=1e-4)
    assert int(line[6]) == 0

    # 各クラスタに要約が付き、要約の語句はそのクラスタの問題文に実在し、要約は含意判定にかかっている。
    for cluster in clustered["clusters"]:
        texts = [text for qid in cluster["questions"] for text in _texts(rows[qid])]
        terms = extract_terms(cluster["summary"], Language.JA)
        assert terms
        assert all(is_present(term, texts) for term in terms)
    assert sorted(llm.checked) == sorted(c["summary"] for c in clustered["clusters"])

    # 退化スタブ(全問を1グループに返す): 記録された比率で退化ガードが赤になる。
    degenerate = _deck(workspace, "known5-degenerate", ROWS)
    one_group = AnalysisLlm(groups={"all": tuple(rows)})
    caplog.clear()
    recorded = ClusterMetrics(
        **_load(CliRunner(workspace, capsys, one_group, StubTts())("cluster", degenerate))["metrics"]
    )
    assert recorded.max_cluster_ratio == pytest.approx(1.0)
    assert any("最大クラスタ比率" in v for v in degeneracy_violations(recorded))

    # 要約に別クラスタの語(Lambda は eventbridge にしか無い)が混ざれば、非0終了して何も書かない。
    ungrounded = _deck(workspace, "known5-ungrounded", ROWS)
    llm = AnalysisLlm(groups=GROUPS, summaries={**SUMMARIES, "s3": "S3 のストレージクラスは Lambda で選ぶ"})
    error = _fails(CliRunner(workspace, capsys, llm, StubTts()), "cluster", ungrounded)
    assert isinstance(error, UngroundedSummary)
    assert _artifacts(workspace, ungrounded, "cluster") == []

    # 分割呼び出しが2回失敗した実行: 下流(要約・含意判定・想起素材)へ進まず非0終了し、
    # サマリの3値が実際の値(失敗2件、失敗した6問が「その他」)と一致する。
    failing = _deck(workspace, "known5-failing", ROWS)
    llm = AnalysisLlm(groups=GROUPS, summaries=SUMMARIES, failing=frozenset({"rds", "eventbridge"}))
    caplog.clear()
    error = _fails(CliRunner(workspace, capsys, llm, StubTts()), "recall", failing)
    assert isinstance(error, ClassificationFailed)
    assert llm.calls == ["classify"]
    assert llm.checked == []
    assert _artifacts(workspace, failing, "cluster") == _artifacts(workspace, failing, "recall") == []
    failed_questions = len(GROUPS["rds"]) + len(GROUPS["eventbridge"])
    line = _summary_line(caplog)
    assert float(line[4]) == pytest.approx(failed_questions / 16, abs=1e-4)
    assert float(line[5]) == pytest.approx(failed_questions / 16, abs=1e-4)
    assert int(line[6]) == 2 == error.metrics.failure_count


# ---- C-004 ----


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _distractors(row: Mapping[str, Any]) -> list[str]:
    return [text for i, text in enumerate(row["options"]) if i not in _correct(row)]


def _exhausted(workspace: Path, capsys: pytest.CaptureFixture[str], name: str, **changes: Any) -> RegenerationExhausted:
    """`changes` の stub で recall を走らせ、作り直しの上限超過で止まって何も書かないことを確かめる。"""
    deck_id = _deck(workspace, name, changes.pop("rows", SUBSET_ROWS))
    error = _fails(CliRunner(workspace, capsys, _subset_llm(**changes), StubTts()), "recall", deck_id)
    assert isinstance(error, RegenerationExhausted), error
    assert _artifacts(workspace, deck_id, "recall") == []
    return error


def _failed_checks(error: RegenerationExhausted) -> dict[str, tuple[str, ...]]:
    return {e.question_id.local_id: e.failures for e in error.exhausted}


def test_c004_recall_keys_are_unique_and_grounded(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = {row["id"]: row for row in SUBSET_ROWS}
    deck_id = _deck(workspace, "recall")
    llm = _subset_llm()
    run = CliRunner(workspace, capsys, llm, StubTts())

    clusters = {c["id"]: c["questions"] for c in _load(run("cluster", deck_id))["clusters"]}
    recall = _load(run("recall", deck_id))
    materials = recall["materials"]

    # 全問に想起キー・答え・識別ポイントが付く。
    assert sorted(m["question"] for m in materials) == sorted(rows)
    for m in materials:
        assert m["terms"] and all(t.strip() for t in m["terms"])
        assert all(e["text"].strip() for e in m["answers"]) and m["discrimination"]["text"].strip()

    for m in materials:
        row = rows[m["question"]]
        # キーの語句は元の問題の問題文に実在する。
        assert all(is_present(term, [row["stem"]]) for term in m["terms"])
        # 識別ポイントは誤答選択肢の丸写しではない: 誤答選択肢の本文を除いても 30% 以上が残る。
        rest = _nfkc(m["discrimination"]["text"])
        for option in _distractors(row):
            rest = rest.replace(_nfkc(option), "")
        assert len(rest) >= 0.3 * len(_nfkc(m["discrimination"]["text"]))
        # 汎用語だけのキーではない: 自クラスタ以外のどのクラスタにも出現しない語句が1つ以上ある。
        others = [
            text for cid, qids in clusters.items() if cid != m["cluster"] for qid in qids for text in _texts(rows[qid])
        ]
        assert any(not is_present(term, others) for term in m["terms"])

    # 同じ想起キー(語の組)は複数クラスタで使われない。
    assert len({frozenset(m["terms"]) for m in materials}) == len(materials)
    report = recall["report"]
    assert (report["ungrounded_keys"], report["missing_options"], report["duplicate_keys"]) == (0, 0, 0)
    assert (report["generic_keys"], report["verbatim"], report["answer_mismatch"], report["empty"]) == (0, 0, 0, 0)
    assert report["regenerations"] == dict.fromkeys(rows, 0)
    # 答えの各要素と識別ポイントは含意判定にかかっている。
    statements = {e["text"] for m in materials for e in m["answers"]} | {m["discrimination"]["text"] for m in materials}
    assert statements <= set(llm.checked)

    # 検査に落ちる素材を返し続けると、上限まで作り直した後に非0終了して想起素材は書かれない。
    duplicate = RecallKey(("EC2",))
    variants: dict[str, tuple[dict[str, RecallDraft], dict[str, tuple[str, ...]]]] = {
        # ec2 の q05 と iam の q08 に、両方の問題文にある同じ語の組を与える。重複はクラスタ順で後の q08 にだけ
        # 数える。両方の問題文にある語はどちらのクラスタにも出現するので、両方が汎用語にもなる。
        "duplicate_key": (
            {"q05": replace(DRAFTS["q05"], key=duplicate), "q08": replace(DRAFTS["q08"], key=duplicate)},
            {"q05": ("generic",), "q08": ("generic", "duplicate_key")},
        ),
        # iam の q08 に、語句がすべて ec2 クラスタにも出現するキーを与える。
        "generic": ({"q08": replace(DRAFTS["q08"], key=RecallKey(("EC2",)))}, {"q08": ("generic",)}),
        # 誤答選択肢2件を貼り合わせた識別ポイント。
        "verbatim": (
            {"q06": _discriminated(DRAFTS["q06"], "スポットインスタンスとオンデマンドインスタンス")},
            {"q06": ("verbatim",)},
        ),
        # 問題に無い語句を含むキー。
        "ungrounded_key": (
            {"q10": replace(DRAFTS["q10"], key=RecallKey(("ゼロ知識証明", "権限")))},
            {"q10": ("ungrounded_key",)},
        ),
        # 空白だけの答え。
        "empty": ({"q12": _answered(DRAFTS["q12"], "  ")}, {"q12": ("empty",)}),
    }
    for check, (drafts, expected) in variants.items():
        error = _exhausted(workspace, capsys, f"recall-{check.replace('_', '-')}", drafts={**DRAFTS, **drafts})
        assert _failed_checks(error) == expected, check
        assert all(e.attempts == 1 + MAX_REGENERATIONS for e in error.exhausted)


def test_c004_recall_keys_are_grounded_in_the_stem_only(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A-1: 想起キーの照合先は問題文だけ。他クラスタ出現率とクラスタ要約は問題文・選択肢・解説(D-198)。"""
    rows = {row["id"]: row for row in SUBSET_ROWS}
    # q07 の選択肢にだけある語と、q10 の解説にだけある語。
    in_options, in_explanation = "Savings Plans", "必要な操作"
    assert in_options in rows["q07"]["options"] and in_options not in rows["q07"]["stem"]
    assert in_explanation in rows["q10"]["explanation"] and in_explanation not in rows["q10"]["stem"]
    keys = {
        "q07": replace(DRAFTS["q07"], key=RecallKey(("物理サーバー", in_options))),
        "q10": replace(DRAFTS["q10"], key=RecallKey(("必要最小限", in_explanation))),
    }
    error = _exhausted(workspace, capsys, "stem-only", drafts={**DRAFTS, **keys})
    assert _failed_checks(error) == {"q07": ("ungrounded_key",), "q10": ("ungrounded_key",)}

    # 問題文にある語だけのキーは通る(既定の DRAFTS はすべて問題文の語)。
    assert _load(CliRunner(workspace, capsys, _subset_llm(), StubTts())("recall", _deck(workspace, "stem-ok")))

    # 他クラスタ出現率は選択肢・解説も見る: 「インスタンス」は iam の q08 の問題文にあり、ec2 クラスタでは
    # 選択肢と解説にしか無い。問題文だけを照合先にすると汎用語にならない。
    assert not any("インスタンス" in rows[q]["stem"] for q in GROUPS["ec2"])
    assert any("インスタンス" in text for q in GROUPS["ec2"] for text in rows[q]["options"])
    generic = {"q08": replace(DRAFTS["q08"], key=RecallKey(("インスタンス",)))}
    error = _exhausted(workspace, capsys, "other-cluster-options", drafts={**DRAFTS, **generic})
    assert _failed_checks(error) == {"q08": ("generic",)}

    # クラスタ要約は選択肢・解説の語も実在とみなす: ec2 の要約に選択肢にだけある語を入れても通る。
    deck_id = _deck(workspace, "summary-options")
    summary = f"EC2 の購入オプションと {in_options}"
    llm = _subset_llm(summaries={**SUMMARIES, "ec2": summary})
    clusters = _load(CliRunner(workspace, capsys, llm, StubTts())("cluster", deck_id))["clusters"]
    assert summary in [c["summary"] for c in clusters]


# 複数正解(N=2)の問題。iam クラスタの q10 と差し替える。
MULTI_ROW = {
    "id": "q10",
    "group": "iam",
    "stem": "IAM ユーザーの権限管理で推奨される原則を2つ選べ。",
    "options": ["最小権限の原則", "ルートユーザーの常用", "職務分掌", "アクセスキーの共有"],
    "corrects": [0, 2],
    "explanation": "必要な権限だけを与える最小権限の原則と、職務を分けて相互に牽制する職務分掌が推奨される。",
}
MULTI_ROWS = [MULTI_ROW if row["id"] == "q10" else row for row in SUBSET_ROWS]
MULTI_DRAFT = _draft(
    "q10",
    ("権限管理", "推奨"),
    {"A": "必要な権限だけを与える最小権限の原則", "C": "職務を分けて牽制する職務分掌"},
    "ルートユーザーを日常の作業に使うのは避ける",
    "B",
)


def test_c004_answers_and_distractors_are_structured_by_option_id(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A-3: 答えは要素ごとに選択肢 ID を持ち、ID の集合が正解と一致する。識別ポイントは誤答を ID で参照する。"""
    deck_id = _deck(workspace, "structured", MULTI_ROWS)
    # 本文の括弧は検査しない: どの選択肢にも無い語句を括っても通る。
    quoted = _discriminated(DRAFTS["q07"], "物理サーバーを専有し、「ハードウェア専有プラン」とは違う")
    llm = _subset_llm(drafts={**DRAFTS, "q10": MULTI_DRAFT, "q07": quoted})
    recall = _load(CliRunner(workspace, capsys, llm, StubTts())("recall", deck_id))
    rows = {row["id"]: row for row in MULTI_ROWS}
    for m in recall["materials"]:
        row = rows[m["question"]]
        assert sorted(e["option"] for e in m["answers"]) == ["ABCD"[i] for i in _correct(row)]
        assert m["discrimination"]["distractors"]
        assert all("ABCD".index(o) not in _correct(row) for o in m["discrimination"]["distractors"])
    (multi,) = [m for m in recall["materials"] if m["question"] == "q10"]
    assert multi["answers"] == [{"option": e.option_id.local_id, "text": e.text} for e in MULTI_DRAFT.answers]
    assert multi["discrimination"] == {"text": MULTI_DRAFT.discrimination.text, "distractors": ["B"]}

    a, c = MULTI_DRAFT.answers
    wrong = AnswerElement(_oid("q10", "B"), "ルートユーザーを常用する")
    answer_sets = {
        "missing": (a,),
        "extra": (a, c, wrong),
        "duplicate": (a, a),
        "not_correct": (a, wrong),
        "none": (),
    }
    for name, answers in answer_sets.items():
        error = _exhausted(
            workspace,
            capsys,
            f"answers-{name.replace('_', '-')}",
            rows=MULTI_ROWS,
            drafts={**DRAFTS, "q10": replace(MULTI_DRAFT, answers=answers)},
        )
        assert _failed_checks(error) == {"q10": ("answer_mismatch",)}, name

    distractor_sets = {"empty": (), "correct": (_oid("q10", "A"),), "mixed": (_oid("q10", "B"), _oid("q10", "C"))}
    for name, distractors in distractor_sets.items():
        point = DiscriminationPoint(MULTI_DRAFT.discrimination.text, distractors)
        error = _exhausted(
            workspace,
            capsys,
            f"distractors-{name}",
            rows=MULTI_ROWS,
            drafts={**DRAFTS, "q10": replace(MULTI_DRAFT, discrimination=point)},
        )
        assert _failed_checks(error) == {"q10": ("missing_options",)}, name


def test_c004_failed_questions_are_regenerated_up_to_twice(
    workspace: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """A-5: 検査か含意判定に落ちた問題だけを上限2回まで作り直し、回数を report に記録する。"""
    ungrounded = replace(DRAFTS["q05"], key=RecallKey(("ゼロ知識証明",)))
    sequences = {
        "q05": (ungrounded, DRAFTS["q05"]),
        "q06": (_discriminated(DRAFTS["q06"], "スポットインスタンス"), ungrounded, DRAFTS["q06"]),
        # ec2 の q07 と同じキーを、iam の q09 が1回目に返す(q09 の問題文に無い語なので実在性にも落ちる)。
        "q09": (replace(DRAFTS["q09"], key=DRAFTS["q07"].key), DRAFTS["q09"]),
    }
    deck_id = _deck(workspace, "regenerate")
    llm = _subset_llm(sequences=sequences)
    recall = _load(CliRunner(workspace, capsys, llm, StubTts())("recall", deck_id))
    assert recall["report"]["regenerations"] == {**dict.fromkeys(DRAFTS, 0), "q05": 1, "q06": 2, "q09": 1}
    assert sorted(llm.recalled) == sorted([*DRAFTS, "q05", "q06", "q06", "q09"])

    # 上限を超えた問題があっても全問を回し切り、超えた問題をすべて列挙する。
    caplog.set_level(logging.INFO)
    always_bad = {"q05": (ungrounded,), "q12": (_answered(DRAFTS["q12"], ""),)}
    deck_id = _deck(workspace, "regenerate-exhausted")
    llm = _subset_llm(sequences=always_bad)
    error = _fails(CliRunner(workspace, capsys, llm, StubTts()), "recall", deck_id)
    assert isinstance(error, RegenerationExhausted)
    assert _failed_checks(error) == {"q05": ("ungrounded_key",), "q12": ("empty",)}
    assert [e.attempts for e in error.exhausted] == [3, 3]
    assert sorted(llm.recalled) == sorted([*DRAFTS, "q05", "q05", "q12", "q12"])
    # 機械検査に落ちた素材は含意判定にかけない。
    for qid in always_bad:
        (bad,) = always_bad[qid]
        assert not {bad.discrimination.text, *(e.text for e in bad.answers)} & set(llm.checked), qid
    assert _artifacts(workspace, deck_id, "recall") == []
    message = str(error.code)
    assert "q05(ungrounded_key; 3 回)" in message and "q12(empty; 3 回)" in message
    assert "ゼロ知識証明" not in message

    # 呼び出しの失敗は作り直さない: 失敗した問題は1回だけ呼ばれ、残りの問題は回し切る。
    deck_id = _deck(workspace, "regenerate-call-failure")
    llm = _subset_llm(call_failures=frozenset({"q06"}))
    error = _fails(CliRunner(workspace, capsys, llm, StubTts()), "recall", deck_id)
    assert isinstance(error, RecallCallsFailed)
    assert sorted(llm.recalled) == sorted(DRAFTS)

    # 含意判定に落ちた問題も同じ上限の中で作り直す(D-232)。判定器が q11 の答えを2回落としてから通すと、
    # q11 は3回生成されて成果物が書かれる。判定にかかるのは機械検査を通った素材だけ。
    answer = DRAFTS["q11"].answers[0].text
    for rejections, written in ((2, True), (3, False)):
        deck_id = _deck(workspace, f"regenerate-entailment-{rejections}")
        llm = _subset_llm()

        def rejects_first(_: str, statement: str, n: int = rejections, llm: AnalysisLlm = llm) -> bool:
            return statement == answer and llm.checked.count(answer) <= n

        llm.contradicts = rejects_first
        run = CliRunner(workspace, capsys, llm, StubTts())
        if written:
            recall = _load(run("recall", deck_id))
            assert recall["report"]["regenerations"] == {**dict.fromkeys(DRAFTS, 0), "q11": 2}
        else:
            error = _fails(run, "recall", deck_id)
            assert isinstance(error, RegenerationExhausted)
            assert _failed_checks(error) == {"q11": ("entailment",)}
            assert [e.attempts for e in error.exhausted] == [3]
            assert _artifacts(workspace, deck_id, "recall") == []
        # 上限を超えても残りの問題を回し切る。
        assert sorted(llm.recalled) == sorted([*DRAFTS, "q11", "q11"])
        assert llm.checked.count(answer) == 3


def test_c004_regenerations_are_counted_as_calls_and_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-5: 作り直しの呼び出しも llm_calls(`begin_call`)と provider_invocations(子の起動)に数えられる。

    既定 LLM(`ClaudeCliLlm`)を spawn だけ fake にして通し、呼び出しと起動を stub の記録で数える。
    """
    root = tmp_path / "root"
    root.mkdir()
    write_root(root)
    recalled: list[str] = []

    def run(spec: CommandSpec) -> CommandResult:
        if "terms" not in json.loads(flag(spec.argv, "--json-schema"))["properties"]:
            return respond(spec)
        prompt = json.loads(spec.stdin)
        recalled.append(prompt["question"]["stem"])
        output = recall_output(prompt)
        if len(recalled) == 1:
            # 最初の呼び出しだけ、問題文に無い語のキーを返す。
            output = {**output, "terms": ["ゼロ知識証明"]}
        return exited(fake_stdout(structured_output=output))

    llms = CliLlms(tmp_path, FakeRunner(run))
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    for stage in ("cluster", "recall"):
        assert cli.main(["--root", str(root), stage, DECK], llm_factory=llms, preflight=no_preflight) == 0
    issued = [s for s in llms.runner.specs if "terms" in json.loads(flag(s.argv, "--json-schema"))["properties"]]
    assert len(recalled) == len(issued) == len(DECK_ROWS) + 1
    assert recalled[0] == recalled[1]
    assert llms.methods.count("generate_recall") == len(DECK_ROWS) + 1


# ---- C-005 ----


def test_c005_glossary_reports_no_reference_and_separate_series(
    workspace: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    deck_id = _deck(workspace, "terms")
    llm = _subset_llm()
    tts = StubTts()
    run = CliRunner(workspace, capsys, llm, tts)

    # グロッサリが生成され、公式解説と照合して矛盾 0 件。
    glossary = _load(run("glossary", deck_id))
    terms = [term for term, _ in glossary["entries"]]
    assert {"EC2", "IAM", "RDS"} <= set(terms)
    assert len(terms) == len(set(terms))
    assert all(description.strip() for _, description in glossary["entries"])
    assert glossary["checked"] >= 1
    statements = {f"{term}: {description}" for term, description in glossary["entries"]}
    assert len(statements & set(llm.checked)) == glossary["checked"]
    assert any("glossary: consistent" in r.getMessage() and "矛盾 0 件" in r.getMessage() for r in caplog.records)

    # 照合対象(公式解説)が0件なら、合格ではなく「照合対象なし」と記録される。
    unexplained = _deck(workspace, "glossary-unexplained", explained=False)
    llm = _subset_llm()
    caplog.clear()
    glossary = _load(CliRunner(workspace, capsys, llm, StubTts())("glossary", unexplained))
    assert glossary["entries"]
    assert glossary["checked"] == 0
    assert llm.checked == []
    messages = [r.getMessage() for r in caplog.records if r.getMessage().startswith("glossary:")]
    assert len(messages) == 1
    assert "no_reference" in messages[0] and "照合対象なし" in messages[0]
    assert "consistent" not in messages[0]

    # 公式解説と矛盾する説明があれば、非0終了してグロッサリは書かれない。
    contradicting = _deck(workspace, "glossary-contradicted")
    llm = _subset_llm(contradicted=frozenset({"IAM: IAMの説明"}))
    error = _fails(CliRunner(workspace, capsys, llm, StubTts()), "glossary", contradicting)
    assert isinstance(error, EntailmentRejected)
    assert error.verdict is Entailment.CONTRADICTED
    assert _artifacts(workspace, contradicting, "glossary-terms") == []

    # 音声: グロッサリは想起素材とは別のディレクトリ・別の再生リストに出る。
    audio_dir = Path(run("audio", deck_id))
    glossary_dir = workspace / "out" / deck_id / "glossary"
    assert audio_dir == workspace / "out" / deck_id / "audio"
    recall_tracks = sorted(p.name for p in audio_dir.glob("*.mp3"))
    glossary_tracks = sorted(p.name for p in glossary_dir.glob("*.mp3"))
    assert recall_tracks == [f"00{i}_{deck_id}_c00{i}.mp3" for i in (1, 2, 3)]
    assert len(glossary_tracks) == len(terms)
    assert all(name.startswith("g") and "glossary" in name for name in glossary_tracks)
    assert not set(recall_tracks) & set(glossary_tracks)
    recall_playlist = (audio_dir / "playlist.m3u").read_text(encoding="utf-8")
    glossary_playlist = (glossary_dir / "glossary.m3u").read_text(encoding="utf-8")
    assert [line for line in recall_playlist.splitlines() if line.endswith(".mp3")] == recall_tracks
    assert [line for line in glossary_playlist.splitlines() if line.endswith(".mp3")] == glossary_tracks
    assert not list(audio_dir.glob("*glossary*"))


# ---- C-006 ----


def _seconds(text: str) -> float:
    """日本語の推定読み上げ秒数(空白を除いた文字数 ÷ 7 文字/秒)。"""
    return sum(not ch.isspace() for ch in text) / 7.0


def _answer(question: Mapping[str, Any]) -> str:
    """台本の成果物の1問分から、読み上げる答え(要素を空白で連結したもの)。"""
    return " ".join(question["answers"])


def _long(text: str, seconds: float) -> str:
    sentence = "スタンバイへ同期で複製され、障害時は自動で切り替わる。"
    return text + "。" + sentence * round(seconds / _seconds(sentence))


def test_c006_script_order_pause_and_no_label_enumeration(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    deck_id = _deck(workspace, "script")
    llm = _subset_llm()
    tts = StubTts()
    run = CliRunner(workspace, capsys, llm, tts)

    script = _load(run("script", deck_id))
    questions = [q for track in script["tracks"] for q in track["questions"]]
    assert sorted(q["id"] for q in questions) == sorted(DRAFTS)
    assert script["split"] == []
    for q in questions:
        draft = DRAFTS[q["id"]]
        # 問いかけは想起キーから作られ、答えと識別ポイントは想起素材のまま。
        assert all(term in q["prompt"] for term in draft.key.terms)
        assert (q["answers"], q["discrimination"]["text"]) == ([draft.answers[0].text], draft.discrimination.text)
        # 「間」はテキストではなく、秒数を持つ別の要素として表される。
        assert isinstance(q["pause"], float) and q["pause"] >= 3.0

    # 読み上げ順は 問いかけ → (間) → 答え → 識別ポイント。
    run("audio", deck_id)
    spoken = [text for text, _ in tts.inputs]
    expected = [t for q in questions for t in (q["prompt"], _answer(q), q["discrimination"]["text"])]
    assert spoken[: len(expected)] == expected

    # 選択肢ラベルの4件並列提示は、記号・番号・かなのどれでも、セグメントを跨いでも非0終了して書かれない。
    # q05 と q06 は同じ ec2 クラスタ(同じトラック)に入る。
    for series, (first, second) in {
        "kana": ("アとイは高い", "ウとエは中断されない"),
        "number": ("1番と2番は高い", "3番と4番は中断されない"),
        "letter": ("A と B は高い", "C と D は中断されない"),
    }.items():
        variant = _deck(workspace, f"script-{series}")
        drafts = {
            **DRAFTS,
            "q05": _discriminated(DRAFTS["q05"], f"{DRAFTS['q05'].discrimination.text}。{first}"),
            "q06": _discriminated(DRAFTS["q06"], f"{DRAFTS['q06'].discrimination.text}。{second}"),
        }
        error = _fails(CliRunner(workspace, capsys, _subset_llm(drafts=drafts), StubTts()), "script", variant)
        assert isinstance(error, LabelEnumerationFound)
        assert _artifacts(workspace, variant, "script") == []

    # 推定3分を超える rds クラスタ(1問約80秒 × 3問)は、問の境界で割られ、1問も打ち切られない。
    overlong = _deck(workspace, "script-overlong")
    drafts = {**DRAFTS, **{q: _answered(DRAFTS[q], _long(DRAFTS[q].answers[0].text, 70)) for q in GROUPS["rds"]}}
    script = _load(CliRunner(workspace, capsys, _subset_llm(drafts=drafts), StubTts())("script", overlong))
    assert script["split"] == ["c003"]
    parts = [track for track in script["tracks"] if track["cluster"] == "c003"]
    assert [track["part"] for track in parts] == [0, 1]
    assert [q["id"] for track in parts for q in track["questions"]] == list(GROUPS["rds"])
    for track in parts:
        seconds = sum(
            _seconds(q["prompt"] + _answer(q) + q["discrimination"]["text"]) + q["pause"] for q in track["questions"]
        )
        assert seconds <= 180
        assert all(q["answers"] == [drafts[q["id"]].answers[0].text] for q in track["questions"])

    # 1問だけで3分を超えるなら割れないので、そのクラスタを列挙して非0終了する。
    unsplittable = _deck(workspace, "script-unsplittable")
    drafts = {**DRAFTS, "q13": _answered(DRAFTS["q13"], _long("同期", 200))}
    error = _fails(CliRunner(workspace, capsys, _subset_llm(drafts=drafts), StubTts()), "script", unsplittable)
    assert isinstance(error, ScriptTooLong)
    assert [c.local_id for c in error.cluster_ids] == ["c003"]
    assert _artifacts(workspace, unsplittable, "script") == []


def _with_options(row: Mapping[str, Any], *extra: str) -> dict[str, Any]:
    """`row` の選択肢を `extra` の分だけ増やした問題(M = 4 + len(extra))。正解は変えない。"""
    return {**row, "options": [*row["options"], *extra]}


def test_c006_label_enumeration_uses_the_smallest_m_in_the_track(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A-1: 系列の先頭から M 件(M はトラック内の問題の M の最小値)が揃う形だけを列挙として止める。"""
    rows = {row["id"]: row for row in SUBSET_ROWS}
    # ec2 クラスタ(c001 = q05・q06・q07)は1トラックに入る。q05 と q06 の識別ポイントにラベルを足す。
    five = {q: _with_options(rows[q], "専有インスタンス") for q in GROUPS["ec2"]}
    mixed = {
        "q05": _with_options(rows["q05"], "専有インスタンス"),
        "q06": _with_options(rows["q06"], "専有インスタンス", "Capacity Reservations"),
    }

    def run(name: str, changed: Mapping[str, dict[str, Any]], first: str, second: str) -> Path | SystemExit:
        deck_id = _deck(workspace, name, [changed.get(row["id"], row) for row in SUBSET_ROWS])
        drafts = {
            **DRAFTS,
            "q05": _discriminated(DRAFTS["q05"], f"{DRAFTS['q05'].discrimination.text}。{first}"),
            "q06": _discriminated(DRAFTS["q06"], f"{DRAFTS['q06'].discrimination.text}。{second}"),
        }
        runner = CliRunner(workspace, capsys, _subset_llm(drafts=drafts), StubTts())
        try:
            return Path(runner("script", deck_id))
        except SystemExit as error:
            assert _artifacts(workspace, deck_id, "script") == []
            return error

    four_labels = {
        "letter": ("A と B は高い", "C と D は中断されない"),
        "number": ("1番と②は高い", "(３)と4番は中断されない"),
        "kana": ("アとイは高い", "ウとエは中断されない"),
    }
    five_labels = {
        "letter": ("A と B と C は高い", "D と E は中断されない"),
        "number": ("1番と②と3番は高い", "(４)と⑤は中断されない"),
        "kana": ("アとイとウは高い", "エとオは中断されない"),
    }
    for series in four_labels:
        # M=5 のトラックでは、先頭から4件の言及は止まらず、5件が揃えば止まる。
        assert isinstance(run(f"m5-four-{series}", five, *four_labels[series]), Path), series
        stopped = run(f"m5-five-{series}", five, *five_labels[series])
        assert isinstance(stopped, LabelEnumerationFound), series
        assert [(t.cluster_id.local_id, t.part) for t in stopped.tracks] == [("c001", 0)]
        # M の違う問題が混ざるトラック(4件・5件・6件)は最小の M=4 で判定する。
        assert isinstance(run(f"mixed-four-{series}", mixed, *four_labels[series]), LabelEnumerationFound), series

    # 6件の系列(全角の Ｆ・６ と丸数字 ⑥ を含む)。M=6 のトラックで、5件までは止まらず6件で止まる。
    six = {q: _with_options(rows[q], "専有インスタンス", "Capacity Reservations") for q in GROUPS["ec2"]}
    assert isinstance(run("m6-five", six, "A と B と C は高い", "D と E は中断されない"), Path)
    assert isinstance(
        run("m6-six-letter", six, "A と B と C は高い", "D と E とＦは中断されない"), LabelEnumerationFound
    )
    assert isinstance(
        run("m6-six-number", six, "1番と2番と3番は高い", "④と5番と⑥は中断されない"), LabelEnumerationFound
    )
    assert isinstance(run("m6-six-kana", six, "アとイとウは高い", "エとオとカは中断されない"), LabelEnumerationFound)


def test_c006_answer_mentions_every_correct_option(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A-2: N-of-M の問題の台本の答えは N 要素から組み立てられ、要素数が N と違えば止まる。"""
    deck_id = _deck(workspace, "script-multi", MULTI_ROWS)
    tts = StubTts()
    run = CliRunner(workspace, capsys, _subset_llm(drafts={**DRAFTS, "q10": MULTI_DRAFT}), tts)
    script = _load(run("script", deck_id))
    (multi,) = [q for track in script["tracks"] for q in track["questions"] if q["id"] == "q10"]
    # 正解 A・C の2要素が、選択肢の順でそれぞれ答えに入る。
    assert multi["answers"] == [e.text for e in MULTI_DRAFT.answers]
    # 読み上げは要素を番号なしで連結した1文。
    run("audio", deck_id)
    spoken = " ".join(e.text for e in MULTI_DRAFT.answers)
    assert spoken in [text for text, _ in tts.inputs]
    assert not re.search(r"(?:^|\s)(?:[1-6１-６]\s*[.．)）]|[(（][1-6１-６][)）]|[1-6１-６]番|[①-⑥])", spoken)

    # 想起素材の成果物が N と違う要素数を持つ(壊れた・古い成果物)なら、台本の段は非0で止まって何も書かない。
    broken = _deck(workspace, "script-multi-broken", MULTI_ROWS)
    run = CliRunner(workspace, capsys, _subset_llm(drafts={**DRAFTS, "q10": MULTI_DRAFT}), StubTts())
    recall_path = Path(run("recall", broken))
    recall = _load(str(recall_path))
    (row,) = [m for m in recall["materials"] if m["question"] == "q10"]
    row["answers"] = row["answers"][:1]
    recall_path.write_text(json.dumps(recall, ensure_ascii=False), encoding="utf-8")
    error = _fails(run, "script", broken)
    assert isinstance(error, MissingAnswerElement)
    assert error.question_id.local_id == "q10"
    assert MULTI_DRAFT.answers[0].text not in str(error.code)
    assert _artifacts(workspace, broken, "script") == []


# ---- C-013 ----


def test_c013_contradiction_blocks_output(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # クラスタ要約: 正解・解説(明示的な拒否が優先)と矛盾する要約は書かれない。
    # 語句はすべて iam クラスタに実在するので、止めるのは含意判定だけ。
    wrong_summary = "IAM ポリシーでは許可が拒否より優先される"
    deck_id = _deck(workspace, "c013-summary")
    llm = _subset_llm(summaries={**SUMMARIES, "iam": wrong_summary}, contradicted=frozenset({wrong_summary}))
    error = _fails(CliRunner(workspace, capsys, llm, StubTts()), "cluster", deck_id)
    assert isinstance(error, EntailmentRejected) and error.verdict is Entailment.CONTRADICTED
    assert wrong_summary in llm.checked
    assert _artifacts(workspace, deck_id, "cluster") == []

    # 想起素材: 誤った答え(q11 の正解はマルチ AZ 配置)は書かれない。判定できない(uncertain)ものも止める。
    # q11 だけを上限まで作り直し、それでも通らなければ成果物を書かずに止まる(D-232)。
    wrong_answer = "リードレプリカを作る"
    for verdict, changes in (
        (Entailment.CONTRADICTED, {"contradicted": frozenset({wrong_answer})}),
        (Entailment.UNCERTAIN, {"uncertain": frozenset({wrong_answer})}),
    ):
        deck_id = _deck(workspace, f"c013-recall-{verdict.value}")
        drafts = {**DRAFTS, "q11": _answered(DRAFTS["q11"], wrong_answer)}
        llm = _subset_llm(drafts=drafts, **changes)
        error = _fails(CliRunner(workspace, capsys, llm, StubTts()), "recall", deck_id)
        assert isinstance(error, RegenerationExhausted), verdict
        assert [(e.question_id.local_id, e.failures, e.attempts) for e in error.exhausted] == [
            ("q11", ("entailment",), 3)
        ]
        assert llm.checked.count(wrong_answer) == 3
        assert wrong_answer not in str(error.code)
        assert _artifacts(workspace, deck_id, "recall") == []

    # グロッサリ: 公式解説と矛盾する用語説明は書かれない。
    deck_id = _deck(workspace, "c013-glossary")
    llm = _subset_llm(contradicted=frozenset({"RDS: RDSの説明"}))
    error = _fails(CliRunner(workspace, capsys, llm, StubTts()), "glossary", deck_id)
    assert isinstance(error, EntailmentRejected)
    assert _artifacts(workspace, deck_id, "glossary-terms") == []

    # 台本: 想起素材の段を通った後でも、台本の段が自分で判定し、矛盾すれば台本も音声も書かない。
    deck_id = _deck(workspace, "c013-script")
    llm = _subset_llm()
    run = CliRunner(workspace, capsys, llm, StubTts())
    run("recall", deck_id)
    llm.contradicted = frozenset({DRAFTS["q12"].discrimination.text})
    llm.checked.clear()
    for stage in ("script", "audio"):
        error = _fails(run, stage, deck_id)
        assert isinstance(error, EntailmentRejected)
    assert DRAFTS["q12"].discrimination.text in llm.checked
    assert _artifacts(workspace, deck_id, "recall") != []
    assert _artifacts(workspace, deck_id, "script") == []
    assert not (workspace / "out" / deck_id / "audio").exists()

    # 言い換え: 正解の選択肢と字面の違う答え・要約でも、判定が consistent なら誤検出せずに全段が書かれる。
    paraphrased_summary = "IAM ポリシーの評価では拒否が許可より優先される"
    paraphrases = {
        "q11": _answered(DRAFTS["q11"], "マルチ AZ で待機系を持つ"),
        "q09": _answered(DRAFTS["q09"], "明示的な拒否が勝つ"),
    }
    deck_id = _deck(workspace, "c013-paraphrase")
    llm = _subset_llm(drafts={**DRAFTS, **paraphrases}, summaries={**SUMMARIES, "iam": paraphrased_summary})
    run = CliRunner(workspace, capsys, llm, StubTts())
    for stage, directory in (
        ("cluster", "cluster"),
        ("recall", "recall"),
        ("glossary", "glossary-terms"),
        ("script", "script"),
    ):
        run(stage, deck_id)
        assert _artifacts(workspace, deck_id, directory) != []
    assert {paraphrased_summary, "マルチ AZ で待機系を持つ", "明示的な拒否が勝つ"} <= set(llm.checked)

    # 想起素材の reference: 答えの要素は対象の正解を明示し、識別ポイントは参照先の誤答だけを誤答の節に置く。
    # 同じ主張は後の台本の段でも判定されるので、先に判定された想起素材の段の reference を見る。
    references: dict[str, str] = {}
    for reference, statement in llm.judged:
        references.setdefault(statement, reference)
    q11 = {row["id"]: row for row in SUBSET_ROWS}["q11"]
    assert f"{FOCUS_SECTION}: マルチ AZ 配置" in references["マルチ AZ で待機系を持つ"]
    discrimination = references[DRAFTS["q11"].discrimination.text]
    assert f"{DISTRACTOR_SECTION}: リードレプリカ" in discrimination
    assert not any(text in discrimination for text in _distractors(q11) if text != "リードレプリカ")
    # グロッサリの reference は公式解説のままで、正解・誤答の節を持たない。
    glossary = [reference for reference, statement in llm.judged if statement.endswith("の説明")]
    assert glossary
    assert not any(section in r for r in glossary for section in ("正解:", FOCUS_SECTION, DISTRACTOR_SECTION))


def test_c013_one_contradicting_element_blocks_output(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A-6: N 要素のうち1要素だけ、または参照先の誤答とだけ矛盾すると判定され続けると、成果物は書かれない。"""
    drafts = {**DRAFTS, "q10": MULTI_DRAFT}
    texts = dict(zip("ABCD", MULTI_ROW["options"], strict=True))

    # 矛盾しない実行: 各要素の reference は正解 N 件すべてと、その要素の正解を明示した節を持つ。
    llm = _subset_llm(drafts=drafts)
    CliRunner(workspace, capsys, llm, StubTts())("recall", _deck(workspace, "c013-multi", MULTI_ROWS))
    references = {statement: reference for reference, statement in llm.judged}
    for element, label in zip(MULTI_DRAFT.answers, "AC", strict=True):
        reference = references[element.text]
        assert f"正解: {texts['A']}、{texts['C']}" in reference
        assert f"{FOCUS_SECTION}: {texts[label]}" in reference
    assert f"{DISTRACTOR_SECTION}: {texts['B']}" in references[MULTI_DRAFT.discrimination.text]

    second = MULTI_DRAFT.answers[1].text
    for name, contradicts in {
        # 2つ目の要素(職務分掌)だけを矛盾と返す。台本は N 要素を連結した答えを判定するので、含むかで見る。
        "element": lambda reference, statement: second in statement,
        # 参照先の誤答の節を持つ reference とだけ矛盾と返す。
        "distractor": lambda reference, statement: f"{DISTRACTOR_SECTION}: {texts['B']}" in reference,
    }.items():
        deck_id = _deck(workspace, f"c013-multi-{name}", MULTI_ROWS)
        llm = _subset_llm(drafts=drafts, contradicts=contradicts)
        error = _fails(CliRunner(workspace, capsys, llm, StubTts()), "recall", deck_id)
        # q10 だけを上限まで作り直したうえで、成果物を書かずに止まる(D-232)。
        assert isinstance(error, RegenerationExhausted), name
        assert _failed_checks(error) == {"q10": ("entailment",)}, name
        assert llm.recalled.count("q10") == 3, name
        assert _artifacts(workspace, deck_id, "recall") == []

        # 台本: 想起素材を通した後に同じ判定器へ替えると、台本の段は最初の矛盾で止まって何も書かない。
        deck_id = _deck(workspace, f"c013-multi-script-{name}", MULTI_ROWS)
        llm = _subset_llm(drafts=drafts)
        run = CliRunner(workspace, capsys, llm, StubTts())
        run("recall", deck_id)
        llm.contradicts = contradicts
        error = _fails(run, "script", deck_id)
        assert isinstance(error, EntailmentRejected) and error.verdict is Entailment.CONTRADICTED, name
        assert _artifacts(workspace, deck_id, "script") == []


def test_c013_script_discrimination_is_judged_against_its_distractors(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A-3: 台本の識別ポイントは distractor_ids を引き継ぎ、台本段の reference に参照先の誤答の本文が入る。"""
    q11 = {row["id"]: row for row in SUBSET_ROWS}["q11"]
    statement = DRAFTS["q11"].discrimination.text
    deck_id = _deck(workspace, "c013-script-distractors")
    llm = _subset_llm()
    run = CliRunner(workspace, capsys, llm, StubTts())
    run("recall", deck_id)
    llm.judged.clear()
    script = _load(run("script", deck_id))

    # 成果物の識別ポイントは、想起素材の参照先(q11 は A = リードレプリカ)を引き継ぐ。
    (question,) = [q for track in script["tracks"] for q in track["questions"] if q["id"] == "q11"]
    assert question["discrimination"] == {"text": statement, "distractors": ["A"]}
    # 台本段の reference は、参照先の誤答の本文だけを「正しくない記述」の節に置く。
    (reference,) = [r for r, s in llm.judged if s == statement]
    assert f"{DISTRACTOR_SECTION}: リードレプリカ" in reference
    assert not any(text in reference for text in _distractors(q11) if text != "リードレプリカ")
    # 答えの reference には誤答の節が無い。
    (answer_reference,) = [r for r, s in llm.judged if s == DRAFTS["q11"].answers[0].text]
    assert DISTRACTOR_SECTION not in answer_reference

    # 参照先の誤答の節を持つ reference とだけ矛盾と返す判定器では、台本の段で止まって台本は書かれない。
    deck_id = _deck(workspace, "c013-script-distractors-contradicted")
    llm = _subset_llm()
    run = CliRunner(workspace, capsys, llm, StubTts())
    run("recall", deck_id)
    llm.contradicts = lambda reference, _: f"{DISTRACTOR_SECTION}: リードレプリカ" in reference
    llm.judged.clear()
    error = _fails(run, "script", deck_id)
    assert isinstance(error, EntailmentRejected) and error.verdict is Entailment.CONTRADICTED
    assert any(s == statement for _, s in llm.judged)
    assert _artifacts(workspace, deck_id, "script") == []


@dataclass
class _UngroundedOnce(AnalysisLlm):
    """`label` のグループの要約だけ、最初の1回は `ungrounded` を返す。"""

    label: str = ""
    ungrounded: str = ""
    returned: list[str] = field(default_factory=list)

    def summarize_cluster(self, members: Deck) -> str:
        summary = super().summarize_cluster(members)
        if summary == self.summaries[self.label] and self.ungrounded not in self.returned:
            self.returned.append(self.ungrounded)
            return self.ungrounded
        return summary


def test_regeneration_logs_name_ids_and_counts_only(
    workspace: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """A-5: cluster と glossary の作り直しのたびにログが1行出て、ID と検査名・回数だけを含む(NFR-2)。"""
    caplog.set_level(logging.INFO)
    deck_id = _deck(workspace, "regeneration-logs")
    summary = SUMMARIES["iam"]
    # ec2 クラスタの問題に Lambda は無いので、実在性に落ちる。
    ungrounded = "EC2 の購入オプションは Lambda で選ぶ"
    description = "IAM: IAMの説明"
    llm = _UngroundedOnce(drafts=DRAFTS, groups=GROUPS, summaries=SUMMARIES, label="ec2", ungrounded=ungrounded)

    def first_rejected(_: str, statement: str) -> bool:
        # iam の要約と IAM の説明を、それぞれ最初の1回だけ矛盾と返す。
        return statement in (summary, description) and llm.checked.count(statement) == 1

    llm.contradicts = first_rejected
    run = CliRunner(workspace, capsys, llm, StubTts())
    run("cluster", deck_id)
    run("glossary", deck_id)
    assert llm.returned == [ungrounded] and ungrounded not in llm.checked
    assert llm.checked.count(summary) == 2 and llm.checked.count(description) == 2

    lines = [r.getMessage() for r in caplog.records if "作り直す" in r.getMessage()]
    cluster_lines = [line for line in lines if line.startswith("cluster:")]
    glossary_lines = [line for line in lines if line.startswith("glossary:")]
    assert len(lines) == 3 and len(cluster_lines) == 2 and len(glossary_lines) == 1
    checks = []
    for line in cluster_lines:
        match = re.fullmatch(r"cluster: クラスタ c\d{3} の要約を作り直す\((grounding|entailment); 1 回目\)", line)
        assert match, line
        checks.append(match.group(1))
    assert sorted(checks) == ["entailment", "grounding"]
    assert re.fullmatch(r"glossary: 用語 #\d+ の説明を作り直す\(entailment; 1 回目\)", glossary_lines[0])
    # 要約・説明・用語の本文はどのログにも出ない。
    assert not [text for text in (summary, ungrounded, description, "IAM", "EC2") for line in lines if text in line]
