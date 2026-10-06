"""2クラスタ・3問の合成フィクスチャと、含意判定だけを返すスタブ(R-6 / NFR-1)。合成なので NFR-2 の対象外。"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.recall import RecallKey
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ports.llm import Entailment, RecallDraft
from quiz_sift.recall.generate import RecallSet
from quiz_sift.recall.verify import RecallMaterial, RecallReport
from quiz_sift.script.generate import ScriptSet
from quiz_sift.script.segments import QuestionScript

DECK = DeckId("aws-saa")

# (id, 問題文, 選択肢, 正解の添字, 解説)
ROWS = [
    (
        "q01",
        "アクセス頻度は低いが即座に取り出す必要があるデータに適した S3 のストレージクラスはどれか。",
        ["S3 標準", "S3 標準-IA", "S3 Glacier Deep Archive", "S3 One Zone-IA"],
        1,
        "S3 標準-IA はアクセス頻度が低いデータ向けのストレージクラスで、ミリ秒単位で取り出せる。",
    ),
    (
        "q02",
        "S3 のオブジェクトを 90 日後に自動で Glacier へ移すには何を設定するか。",
        ["ライフサイクルルール", "バケットポリシー", "レプリケーション", "バージョニング"],
        0,
        "ライフサイクルルールはオブジェクトを指定日数後に別のストレージクラスへ移行する。",
    ),
    (
        "q03",
        "IAM ポリシーで許可と明示的な拒否が両方ある場合、アクセスはどうなるか。",
        ["許可される", "拒否される", "先に書かれた方が優先される", "管理者だけ許可される"],
        1,
        "IAM では明示的な拒否が常に許可より優先される。",
    ),
]
CLUSTERS = {"c001": ("q01", "q02"), "c002": ("q03",)}

GOOD = {
    "q01": RecallDraft(
        RecallKey(("アクセス頻度", "即座", "ストレージクラス")),
        "S3 標準-IA",
        "標準-IA は低頻度向けでもミリ秒で取り出せるが、「S3 Glacier Deep Archive」は取り出しに数時間かかる",
    ),
    "q02": RecallDraft(
        RecallKey(("90 日後", "Glacier", "ライフサイクルルール")),
        "ライフサイクルルール",
        "移行を自動で行うのはライフサイクルルールで、「バケットポリシー」はアクセス制御しかしない",
    ),
    "q03": RecallDraft(
        RecallKey(("明示的な拒否", "許可")),
        "拒否される",
        "明示的な拒否は常に許可より優先され、書いた順序は関係しない",
    ),
}


def _question(qid: str, stem: str, texts: list[str], correct: int, explanation: str) -> Question:
    question_id = QuestionId(DECK, qid)
    options = tuple(Option(OptionId(question_id, label), text) for label, text in zip("ABCD", texts, strict=True))
    return Question(question_id, stem, options, (options[correct].option_id,), explanation, StudiedFlag.UNKNOWN)


@pytest.fixture
def deck() -> Deck:
    return Deck(DECK, Language.JA, None, tuple(_question(*row) for row in ROWS))


@pytest.fixture
def clusters() -> tuple[Cluster, ...]:
    return tuple(
        Cluster(ClusterId(DECK, cid), cid, tuple(QuestionId(DECK, q) for q in qids)) for cid, qids in CLUSTERS.items()
    )


def recall_set(drafts: dict[str, RecallDraft]) -> RecallSet:
    """`drafts` をそのまま想起素材にする。台本は想起素材の検査を通ったものからしか作られない。"""
    materials = tuple(
        RecallMaterial(ClusterId(DECK, cid), QuestionId(DECK, qid), drafts[qid])
        for cid, qids in CLUSTERS.items()
        for qid in qids
    )
    return RecallSet(materials, RecallReport(0, 0, 0, 0, 0))


def qscript(local_id: str, prompt: str, answer: str, discrimination: str | None = None) -> QuestionScript:
    """テキストを直接与えて1問分の台本を作る。"""
    return QuestionScript(
        QuestionId(DECK, local_id),
        Prompt(prompt),
        Pause(3.3),
        Answer(answer),
        None if discrimination is None else Discrimination(discrimination),
    )


@dataclass
class StubChecker:
    """`contradicted` / `uncertain` に含まれる主張にその判定を返し、呼ばれた照合を記録する。"""

    contradicted: frozenset[str] = frozenset()
    uncertain: frozenset[str] = frozenset()
    checked: list[tuple[str, str]] = field(default_factory=list)

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        self.checked.append((reference, statement))
        if statement in self.contradicted:
            return Entailment.CONTRADICTED
        if statement in self.uncertain:
            return Entailment.UNCERTAIN
        return Entailment.CONSISTENT


class Writer:
    """成果物を1行1トラックで書き出す。書かれたかどうかはファイルの有無で判定する。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.result: ScriptSet | None = None

    def __call__(self, result: ScriptSet) -> Path:
        self.result = result
        lines = [f"{t.cluster_id.local_id}\t{t.part}\t{len(t.segments)}" for t in result.tracks]
        self.path.write_text("\n".join(lines), encoding="utf-8")
        return self.path
