"""3クラスタ・5問の合成フィクスチャと、想起素材を返すスタブ LLM(R-4)。合成なので NFR-2 の対象外。

「S3」と「データ」は s3 クラスタと iam クラスタ(q04)の両方に出現する。汎用語だけのキーを作るのに使う。
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.recall import RecallKey
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ports.llm import Entailment, RecallDraft
from quiz_sift.recall.generate import RecallSet

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
    (
        "q04",
        "EC2 インスタンスから S3 のデータへ安全にアクセスさせる方法はどれか。",
        [
            "IAM ロールをインスタンスに付与する",
            "アクセスキーを AMI に埋め込む",
            "バケットを公開する",
            "ルートユーザーの認証情報を使う",
        ],
        0,
        "IAM ロールを使うと一時的な認証情報が自動で配られる。",
    ),
    (
        "q05",
        "RDS でプライマリ障害時に自動でフェイルオーバーさせるには何を使うか。",
        ["マルチ AZ 配置", "リードレプリカ", "スナップショット", "パラメータグループ"],
        0,
        "マルチ AZ 配置はスタンバイへ自動でフェイルオーバーする。",
    ),
]
CLUSTERS = {"c001": ("q01", "q02"), "c002": ("q03", "q04"), "c003": ("q05",)}

# すべての検査を通る想起素材。
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
    "q04": RecallDraft(
        RecallKey(("EC2 インスタンス", "IAM ロール")),
        "IAM ロールをインスタンスに付与する",
        "一時的な認証情報が自動で配られるロールを使い、「アクセスキーを AMI に埋め込む」ような固定の認証情報は避ける",
    ),
    "q05": RecallDraft(
        RecallKey(("フェイルオーバー", "マルチ AZ")),
        "マルチ AZ 配置",
        "スタンバイへ自動で切り替わるのはマルチ AZ 配置で、「リードレプリカ」は読み取りの負荷分散のためのもの",
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


def question(deck: Deck, local_id: str) -> Question:
    return next(q for q in deck.questions if q.question_id.local_id == local_id)


@dataclass
class StubLlm:
    """`drafts` の想起素材を返し、`contradicted` / `uncertain` に含まれる主張にその判定を返す。"""

    drafts: dict[str, RecallDraft]
    contradicted: frozenset[str] = frozenset()
    uncertain: frozenset[str] = frozenset()
    checked: list[tuple[str, str]] = field(default_factory=list)

    def generate_recall(self, question: Question, language: Language) -> RecallDraft:
        return self.drafts[question.question_id.local_id]

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        self.checked.append((reference, statement))
        if statement in self.contradicted:
            return Entailment.CONTRADICTED
        if statement in self.uncertain:
            return Entailment.UNCERTAIN
        return Entailment.CONSISTENT


class Writer:
    """成果物を1行1素材で書き出す。書かれたかどうかはファイルの有無で判定する。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.result: RecallSet | None = None

    def __call__(self, result: RecallSet) -> Path:
        self.result = result
        lines = [
            f"{m.question_id.local_id}\t{'/'.join(m.draft.key.terms)}\t{m.draft.answer}\t{m.draft.discrimination}"
            for m in result.materials
        ]
        self.path.write_text("\n".join(lines), encoding="utf-8")
        return self.path
