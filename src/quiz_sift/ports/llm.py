"""LLM(ADR-002: 1社 + タスク別メソッド)。

用途は分類・要約・生成・含意判定に限られ、それぞれ入出力の形が違う。汎用の `complete()` に
せずメソッドを切ることで、呼び出し側の誤用が `mypy strict` で落ちる。プロンプトは実装側
(`adapters/`)に置くので、プロバイダを差し替えるとプロンプトも一緒に動く。

**呼び出し1回ごとのトークン消費は `UsageRecorder` へ渡す**(NFR-7)。戻り値に混ぜないのは、
下流が計測を渡し忘れる経路を作らないため — 計測は実装の構築時に1回だけ結線する。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import RecallKey


class LlmCallError(Exception):
    """LLM 呼び出しが結果を返せなかった(API エラー・拒否・出力の打ち切り・壊れた応答)。

    呼び出し側が SDK の例外型を import しなくて済むよう、実装はこの型に包んで送出する。
    """


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """呼び出し1回分のトークン数。**input / output の2値に畳まない**(AR-29)。

    キャッシュの書き込みと読み出しは入力とは単価が違い、adaptive thinking(ADR-002)の
    思考トークンは出力側に乗る。畳むと推定コストが系統的にずれる。
    """

    input_tokens: int
    """キャッシュを経由しない入力トークン。"""
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    output_tokens: int
    """思考トークンを含む出力トークン。"""

    def __post_init__(self) -> None:
        counts = (
            self.input_tokens,
            self.cache_creation_input_tokens,
            self.cache_read_input_tokens,
            self.output_tokens,
        )
        if any(n < 0 for n in counts):
            raise ValueError("トークン数に負の値がある")


class UsageRecorder(Protocol):
    def record(self, model: str, usage: TokenUsage) -> None:
        """`model` への呼び出し1回と、その消費トークンを記録する。"""
        ...


@dataclass(frozen=True, slots=True, repr=False)
class GroupProposal:
    """LLM が返したグループ1つ。**生の応答のまま**で、排他性も網羅性も保証しない。

    排他分割と「その他」への振り分けは `cluster/` の純粋関数が行う(ADR-003)。
    """

    label: str
    question_ids: tuple[QuestionId, ...]


@dataclass(frozen=True, slots=True)
class CallFailure:
    """分割呼び出しのうち失敗した1回。どの問題が分類されずに残ったかを持つ(AR-5)。"""

    question_ids: tuple[QuestionId, ...]
    reason: str


@dataclass(frozen=True, slots=True, repr=False)
class ClassificationResult:
    """`classify_clusters` の結果。`failures` が空でない実行は下流へ進ませない(AR-5 / S-1)。"""

    assignments: tuple[GroupProposal, ...]
    failures: tuple[CallFailure, ...]


@dataclass(frozen=True, slots=True, repr=False)
class RecallDraft:
    """1問分の想起素材の生成結果(R-4)。検証は `recall/` が行う。"""

    key: RecallKey
    answer: str
    discrimination: str
    """似た選択肢との切り分け(識別ポイント)。"""


class Entailment(Enum):
    """含意判定の3値(NFR-1 / ADR-009)。`uncertain` を `consistent` に潰さない。"""

    CONSISTENT = "consistent"
    CONTRADICTED = "contradicted"
    UNCERTAIN = "uncertain"


class Llm(Protocol):
    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        """デッキの問題を論点ごとのグループに分ける(R-3)。

        問題を束ねて受け取る入口は `Deck` だけにする(R-11)。素の問題列を受け取ると、
        別デッキの問題を混ぜて分類させる経路が開く。

        分割呼び出しの失敗は例外にせず `failures` に積む。1回の失敗で全体を捨てると、
        失敗件数という事実そのものが下流のサマリに届かなくなる。
        """
        ...

    def summarize_cluster(self, members: Deck) -> str:
        """クラスタの要約をデッキの言語で書く(R-3)。語句の実在性は `grounding/` が判定する。

        `members` はそのクラスタの問題だけを持つ `Deck`。`Cluster` は要約を持って初めて
        構築できるので、要約の入力にはならない。
        """
        ...

    def generate_recall(self, question: Question, language: Language) -> RecallDraft:
        """1問分の想起キー・答え・識別ポイントを `language` で作る(R-4)。"""
        ...

    def describe_term(self, term: str, language: Language) -> str:
        """専門用語の説明を `language` で書く(R-5)。"""
        ...

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        """`statement` が `reference`(正解・公式解説)と矛盾しないかを判定する(NFR-1)。"""
        ...
