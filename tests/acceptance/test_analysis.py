"""クラスタ・想起・グロッサリ・台本・含意判定の受入テスト(C-003 / C-004 / C-005 / C-006 / C-013)。claim ごとに1本。

すべて `cli.main` を conftest の実行ヘルパ越しに走らせ、成果物(`out/<deck>/<段>/`)とログを読む。
本文はすべて合成で、実教材は含まない(NFR-2)。問題は既知5グループの合成フィクスチャ
`tests/cluster/fixtures/known5.jsonl` を使う。C-003 は5グループ16問すべて、それ以外は ec2 / iam / rds の
3グループ9問に絞る(想起素材の下書きを問題ごとに書くため)。

キャッシュは前段の成果物で引かれるので、stub の振る舞いを変えた実行はデッキ ID を分けて走らせる。
"""

import json
import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from quiz_sift.cluster.metrics import ClusterMetrics, degeneracy_violations
from quiz_sift.cluster.run import ClassificationFailed
from quiz_sift.cluster.summary import UngroundedSummary
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.recall import RecallKey
from quiz_sift.grounding.presence import is_present
from quiz_sift.grounding.terms import extract_terms
from quiz_sift.ports.llm import CallFailure, ClassificationResult, Entailment, GroupProposal, RecallDraft
from quiz_sift.recall.generate import RecallRejected
from quiz_sift.safety.entailment import EntailmentRejected
from quiz_sift.script.generate import LabelEnumerationFound, ScriptTooLong

from .conftest import CliRunner, StubLlm, StubTts, write_deck

KNOWN5 = Path(__file__).resolve().parents[1] / "cluster" / "fixtures" / "known5.jsonl"
SUBSET = ("ec2", "iam", "rds")
"""C-004 以降で使うグループ。クラスタ ID は c001=ec2, c002=iam, c003=rds になる。"""


def _fixture() -> list[dict[str, Any]]:
    return [json.loads(line) for line in KNOWN5.read_text(encoding="utf-8").splitlines() if line.strip()]


def _deck_row(row: Mapping[str, Any], *, explained: bool = True) -> dict[str, Any]:
    return {
        "id": row["id"],
        "stem": row["stem"],
        "options": [{"id": label, "text": text} for label, text in zip("ABCD", row["options"], strict=True)],
        "answer": "ABCD"[row["correct"]],
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

# すべての検査を通る想起素材。識別ポイントが鉤括弧で括るのは、その問題の誤答選択肢。
DRAFTS = {
    "q05": RecallDraft(
        RecallKey(("中断", "バッチ処理")),
        "スポットインスタンス",
        "中断を許せるなら最安はスポットで、「リザーブドインスタンス」は長期の確約で割り引く仕組み",
    ),
    "q06": RecallDraft(
        RecallKey(("常時稼働", "本番サーバー")),
        "リザーブドインスタンス",
        "1年以上動かし続けるなら期間を確約して割引を受け、「スポットインスタンス」は中断され得るので本番に向かない",
    ),
    "q07": RecallDraft(
        RecallKey(("ソフトウェアライセンス", "物理サーバー")),
        "Dedicated Hosts",
        "サーバー単位のライセンスは物理サーバーを専有して持ち込み、「Savings Plans」は料金を割り引くだけ",
    ),
    "q08": RecallDraft(
        RecallKey(("アプリケーション", "推奨方法")),
        "IAM ロールをインスタンスに割り当てる",
        "一時的な認証情報が自動で更新されるロールを使い、「アクセスキーをコードに埋め込む」ような固定の認証情報は避ける",
    ),
    "q09": RecallDraft(
        RecallKey(("明示的な拒否", "許可")),
        "拒否",
        "明示的な拒否は常に優先され、「先に書かれた方」のように書いた順では決まらない",
    ),
    "q10": RecallDraft(
        RecallKey(("必要最小限", "権限")),
        "最小権限の原則",
        "必要な操作だけを許すのが最小権限で、「多層防御」は防御を何重にも重ねる別の考え方",
    ),
    "q11": RecallDraft(
        RecallKey(("フェイルオーバー", "別のアベイラビリティーゾーン")),
        "マルチ AZ 配置",
        "スタンバイへ自動で切り替わるのはマルチ AZ 配置で、「リードレプリカ」は読み取りの負荷分散のためのもの",
    ),
    "q12": RecallDraft(
        RecallKey(("読み取り負荷", "分散")),
        "リードレプリカ",
        "読み取り専用のコピーを非同期で作るのがリードレプリカで、「マルチ AZ 配置」は可用性のための待機系",
    ),
    "q13": RecallDraft(
        RecallKey(("スタンバイ", "レプリケーション方式")),
        "同期",
        "マルチ AZ のスタンバイは同期で複製され、「非同期」で複製するのはリードレプリカ",
    ),
}


@dataclass
class AnalysisLlm(StubLlm):
    """`groups`(ラベル → 問題の local_id)どおりに分類し、要約は `summaries` を返す。

    `failing` のグループは分割呼び出しの失敗として返す。含意判定は `contradicted` / `uncertain` に
    入っている主張にだけその判定を返し、ほかは `consistent`。判定にかけた主張は `checked` に残る。
    """

    groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    summaries: Mapping[str, str] = field(default_factory=dict)
    failing: frozenset[str] = frozenset()
    contradicted: frozenset[str] = frozenset()
    uncertain: frozenset[str] = frozenset()
    checked: list[str] = field(default_factory=list)

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
        if statement in self.contradicted:
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

_QUOTED = re.compile(r"「([^」]+)」")


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _distractors(row: Mapping[str, Any]) -> list[str]:
    return [text for i, text in enumerate(row["options"]) if i != row["correct"]]


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
        assert m["answer"].strip() and m["discrimination"].strip()

    for m in materials:
        row = rows[m["question"]]
        # キーの語句は元の問題に実在する。
        assert all(is_present(term, _texts(row)) for term in m["terms"])
        # 識別ポイントが参照する誤答選択肢は元データに実在する。
        referenced = _QUOTED.findall(m["discrimination"])
        assert referenced
        assert all(ref in _distractors(row) for ref in referenced)
        # 識別ポイントは誤答選択肢の丸写しではない: 誤答選択肢の本文を除いても 30% 以上が残る。
        rest = _nfkc(m["discrimination"])
        for option in _distractors(row):
            rest = rest.replace(_nfkc(option), "")
        assert len(rest) >= 0.3 * len(_nfkc(m["discrimination"]))
        # 汎用語だけのキーではない: 自クラスタ以外のどのクラスタにも出現しない語句が1つ以上ある。
        others = [
            text for cid, qids in clusters.items() if cid != m["cluster"] for qid in qids for text in _texts(rows[qid])
        ]
        assert any(not is_present(term, others) for term in m["terms"])

    # 同じ想起キー(語の組)は複数クラスタで使われない。
    assert len({frozenset(m["terms"]) for m in materials}) == len(materials)
    report = recall["report"]
    assert (report["ungrounded_keys"], report["missing_options"], report["duplicate_keys"]) == (0, 0, 0)
    assert (report["generic_keys"], report["verbatim"]) == (0, 0)
    # 答えと識別ポイントは含意判定にかかっている。
    assert {m["answer"] for m in materials} | {m["discrimination"] for m in materials} <= set(llm.checked)

    # 検査に落ちる素材を1件ずつ仕込むと、非0終了して想起素材は書かれない。
    variants = {
        # ec2 の q05 と iam の q08 に、両方に実在する語の組を順序だけ変えて与える。
        "duplicate_keys": {
            "q05": replace(DRAFTS["q05"], key=RecallKey(("EC2", "インスタンス"))),
            "q08": replace(DRAFTS["q08"], key=RecallKey(("インスタンス", "EC2"))),
        },
        # iam の q08 に、語句がすべて ec2 クラスタにも出現するキーを与える。
        "generic_keys": {"q08": replace(DRAFTS["q08"], key=RecallKey(("EC2", "インスタンス")))},
        # 誤答選択肢2件を貼り合わせた識別ポイント。
        "verbatim": {"q06": replace(DRAFTS["q06"], discrimination="スポットインスタンスとオンデマンドインスタンス")},
        # 選択肢に無い語句を括って参照する識別ポイント。
        "missing_options": {
            "q07": replace(DRAFTS["q07"], discrimination="物理サーバーを専有し、「ハードウェア専有プラン」とは違う")
        },
        # 問題に無い語句を含むキー。
        "ungrounded_keys": {"q10": replace(DRAFTS["q10"], key=RecallKey(("ゼロ知識証明", "権限")))},
    }
    for count, drafts in variants.items():
        variant = _deck(workspace, f"recall-{count.replace('_', '-')}")
        error = _fails(
            CliRunner(workspace, capsys, _subset_llm(drafts={**DRAFTS, **drafts}), StubTts()), "recall", variant
        )
        assert isinstance(error, RecallRejected)
        assert getattr(error.report, count) >= 1
        assert _artifacts(workspace, variant, "recall") == []


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
        assert (q["answer"], q["discrimination"]) == (draft.answer, draft.discrimination)
        # 「間」はテキストではなく、秒数を持つ別の要素として表される。
        assert isinstance(q["pause"], float) and q["pause"] >= 3.0

    # 読み上げ順は 問いかけ → (間) → 答え → 識別ポイント。
    run("audio", deck_id)
    spoken = [text for text, _ in tts.inputs]
    expected = [t for q in questions for t in (q["prompt"], q["answer"], q["discrimination"])]
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
            "q05": replace(DRAFTS["q05"], discrimination=f"{DRAFTS['q05'].discrimination}。{first}"),
            "q06": replace(DRAFTS["q06"], discrimination=f"{DRAFTS['q06'].discrimination}。{second}"),
        }
        error = _fails(CliRunner(workspace, capsys, _subset_llm(drafts=drafts), StubTts()), "script", variant)
        assert isinstance(error, LabelEnumerationFound)
        assert _artifacts(workspace, variant, "script") == []

    # 推定3分を超える rds クラスタ(1問約80秒 × 3問)は、問の境界で割られ、1問も打ち切られない。
    overlong = _deck(workspace, "script-overlong")
    drafts = {**DRAFTS, **{q: replace(DRAFTS[q], answer=_long(DRAFTS[q].answer, 70)) for q in GROUPS["rds"]}}
    script = _load(CliRunner(workspace, capsys, _subset_llm(drafts=drafts), StubTts())("script", overlong))
    assert script["split"] == ["c003"]
    parts = [track for track in script["tracks"] if track["cluster"] == "c003"]
    assert [track["part"] for track in parts] == [0, 1]
    assert [q["id"] for track in parts for q in track["questions"]] == list(GROUPS["rds"])
    for track in parts:
        seconds = sum(
            _seconds(q["prompt"] + q["answer"] + q["discrimination"]) + q["pause"] for q in track["questions"]
        )
        assert seconds <= 180
        assert all(q["answer"] == drafts[q["id"]].answer for q in track["questions"])

    # 1問だけで3分を超えるなら割れないので、そのクラスタを列挙して非0終了する。
    unsplittable = _deck(workspace, "script-unsplittable")
    drafts = {**DRAFTS, "q13": replace(DRAFTS["q13"], answer=_long("同期", 200))}
    error = _fails(CliRunner(workspace, capsys, _subset_llm(drafts=drafts), StubTts()), "script", unsplittable)
    assert isinstance(error, ScriptTooLong)
    assert [c.local_id for c in error.cluster_ids] == ["c003"]
    assert _artifacts(workspace, unsplittable, "script") == []


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
    wrong_answer = "リードレプリカを作る"
    for verdict, changes in (
        (Entailment.CONTRADICTED, {"contradicted": frozenset({wrong_answer})}),
        (Entailment.UNCERTAIN, {"uncertain": frozenset({wrong_answer})}),
    ):
        deck_id = _deck(workspace, f"c013-recall-{verdict.value}")
        drafts = {**DRAFTS, "q11": replace(DRAFTS["q11"], answer=wrong_answer)}
        llm = _subset_llm(drafts=drafts, **changes)
        error = _fails(CliRunner(workspace, capsys, llm, StubTts()), "recall", deck_id)
        assert isinstance(error, EntailmentRejected) and error.verdict is verdict
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
    llm.contradicted = frozenset({DRAFTS["q12"].discrimination})
    llm.checked.clear()
    for stage in ("script", "audio"):
        error = _fails(run, stage, deck_id)
        assert isinstance(error, EntailmentRejected)
    assert DRAFTS["q12"].discrimination in llm.checked
    assert _artifacts(workspace, deck_id, "recall") != []
    assert _artifacts(workspace, deck_id, "script") == []
    assert not (workspace / "out" / deck_id / "audio").exists()

    # 言い換え: 正解の選択肢と字面の違う答え・要約でも、判定が consistent なら誤検出せずに全段が書かれる。
    paraphrased_summary = "IAM ポリシーの評価では拒否が許可より優先される"
    paraphrases = {
        "q11": replace(DRAFTS["q11"], answer="マルチ AZ で待機系を持つ"),
        "q09": replace(DRAFTS["q09"], answer="明示的な拒否が勝つ"),
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
