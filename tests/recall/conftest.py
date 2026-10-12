"""3クラスタ・5問の合成フィクスチャと、想起素材を返すスタブ LLM(R-4)。合成なので NFR-2 の対象外。

「S3」と「データ」は s3 クラスタと iam クラスタ(q04)の両方に出現する。汎用語だけのキーを作るのに使う。
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ports.llm import Entailment, LlmCallError, RecallDraft
from quiz_sift.recall.generate import RecallSet, generate_recall_set

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


def option(qid: str, label: str) -> OptionId:
    return OptionId(QuestionId(DECK, qid), label)


def draft(qid: str, terms: tuple[str, ...], answer: str, discrimination: str, distractors: str) -> RecallDraft:
    """`answer` は正解のラベル(答えの要素は正解の選択肢の本文)、`distractors` は参照する誤答のラベルの列。"""
    (_, _, texts, _, _) = next(row for row in ROWS if row[0] == qid)
    return RecallDraft(
        RecallKey(terms),
        (AnswerElement(option(qid, answer), texts["ABCD".index(answer)]),),
        DiscriminationPoint(discrimination, tuple(option(qid, label) for label in distractors)),
    )


# すべての検査を通る想起素材。キーの語句は問題文に実在し、識別ポイントは誤答選択肢を ID で参照する。
GOOD = {
    "q01": draft(
        "q01",
        ("アクセス頻度", "即座", "ストレージクラス"),
        "B",
        "標準-IA は低頻度向けでもミリ秒で取り出せるが、S3 Glacier Deep Archive は取り出しに数時間かかる",
        "C",
    ),
    "q02": draft(
        "q02",
        ("90 日後", "Glacier"),
        "A",
        "移行を自動で行うのはライフサイクルルールで、バケットポリシーはアクセス制御しかしない",
        "B",
    ),
    "q03": draft(
        "q03", ("明示的な拒否", "許可"), "B", "明示的な拒否は常に許可より優先され、書いた順序は関係しない", "C"
    ),
    "q04": draft(
        "q04",
        ("EC2 インスタンス", "安全にアクセス"),
        "A",
        "一時的な認証情報が自動で配られるロールを使い、アクセスキーを AMI に埋め込むような固定の認証情報は避ける",
        "B",
    ),
    "q05": draft(
        "q05",
        ("プライマリ障害", "フェイルオーバー"),
        "A",
        "スタンバイへ自動で切り替わるのはマルチ AZ 配置で、リードレプリカは読み取りの負荷分散のためのもの",
        "B",
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
    """`drafts` の想起素材を返し、`contradicted` / `uncertain` に含まれる主張にその判定を返す。

    `rejections` にある主張は、その回数だけ `contradicted` を返してから `consistent` を返す。`failing_checks` の
    主張の判定は `LlmCallError` を上げる。判定にかけた主張は reference と組で `checked` に残る。

    `sequences` にある問題は、呼ばれるたびにその列を順に返す(尽きたら最後を返し続ける)。`failing` の問題は
    `LlmCallError` を上げる。呼ばれた問題は `recalled` に残る。
    """

    drafts: dict[str, RecallDraft]
    contradicted: frozenset[str] = frozenset()
    uncertain: frozenset[str] = frozenset()
    sequences: dict[str, tuple[RecallDraft, ...]] = field(default_factory=dict)
    failing: frozenset[str] = frozenset()
    rejections: dict[str, int] = field(default_factory=dict)
    failing_checks: frozenset[str] = frozenset()
    checked: list[tuple[str, str]] = field(default_factory=list)
    recalled: list[str] = field(default_factory=list)

    def generate_recall(self, question: Question, language: Language) -> RecallDraft:
        qid = question.question_id.local_id
        self.recalled.append(qid)
        if qid in self.failing:
            raise LlmCallError("合成の呼び出しの失敗")
        sequence = self.sequences.get(qid, (self.drafts[qid],))
        return sequence[min(self.recalled.count(qid), len(sequence)) - 1]

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        self.checked.append((reference, statement))
        if statement in self.failing_checks:
            raise LlmCallError("合成の判定の失敗")
        judged = sum(s == statement for _, s in self.checked)
        if statement in self.contradicted or judged <= self.rejections.get(statement, 0):
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
            "\t".join(
                (
                    m.question_id.local_id,
                    "/".join(m.draft.key.terms),
                    "/".join(e.text for e in m.draft.answers),
                    m.draft.discrimination.text,
                )
            )
            for m in result.materials
        ]
        self.path.write_text("\n".join(lines), encoding="utf-8")
        return self.path


def generate(
    llm: StubLlm,
    deck: Deck,
    clusters: tuple[Cluster, ...],
    writer: Writer,
    stop_seen: Callable[[], bool] = lambda: False,
) -> Path:
    """`generate_recall_set` を、残りを止めない計測と型名だけの失敗種別で呼ぶ。"""
    return generate_recall_set(
        llm, deck, clusters, writer, stop_seen=stop_seen, failure_kind=lambda error: type(error).__name__
    )
