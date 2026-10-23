"""LLM(ADR-002: 1社 + タスク別メソッド)。

用途は分類・要約・生成・含意判定に限られ、それぞれ入出力の形が違う。汎用の `complete()` に
せずメソッドを切ることで、呼び出し側の誤用が `mypy strict` で落ちる。プロンプトは実装側
(`adapters/`)に置くので、プロバイダを差し替えるとプロンプトも一緒に動く。

**呼び出し1回ごとのトークン消費は `UsageRecorder` へ渡す**(NFR-7)。戻り値に混ぜないのは、
下流が計測を渡し忘れる経路を作らないため — 計測は実装の構築時に1回だけ結線する。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey


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
    """1問分の想起素材の生成結果(R-4)。検証は `recall/` が行う。

    `answers` は正解 N 件に1要素ずつ対応する配列で、順序に意味を持たせない。
    """

    key: RecallKey
    answers: tuple[AnswerElement, ...]
    discrimination: DiscriminationPoint


class Entailment(Enum):
    """含意判定の3値(NFR-1 / ADR-009)。`uncertain` を `consistent` に潰さない。"""

    CONSISTENT = "consistent"
    CONTRADICTED = "contradicted"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True, repr=False)
class EntailmentStatement:
    """含意判定にかける主張1件(ADR-029)。教材本文を含むので `repr` に出さない(NFR-3)。

    `id` は1回の呼び出しの中で一意で、応答の判定値はこの ID で主張に対応づける。`focus` はその主張が説明する
    正解の本文、`distractors` は識別ポイントが参照する誤答(この問題では正しくない記述)の本文。
    """

    id: str
    statement: str
    focus: str | None = None
    distractors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class Problem:
    """作り直しの前の出力が落ちた検査1件と、その対象(D-245)。教材本文を含むので `repr` に出さない(NFR-3)。

    `check` は検査の名前。対象は検査ごとに使う欄が違う: 語句は `terms`、選択肢は `options`、空だった要素は
    `elements`(`terms[0]`・`answers[1]`・`discrimination` の形)、含意判定は `statements`(主張の本文と判定値)。
    """

    check: str
    terms: tuple[str, ...] = ()
    options: tuple[OptionId, ...] = ()
    elements: tuple[str, ...] = ()
    statements: tuple[tuple[str, Entailment], ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class Retry[T]:
    """作り直しの呼び出しに渡す、前回の出力と、それまでの全回の落ちた検査(D-245)。

    累積するのは、後の回で前の回と同じ問題に戻らないようにするため。教材本文を含むので `repr` に出さない。
    """

    previous: T
    problems: tuple[Problem, ...]


def with_problems[T](retry: "Retry[T] | None", previous: T, problems: Sequence[Problem]) -> "Retry[T]":
    """`retry` までの検査に `problems` を足し、`previous` を前回の出力にした次の `Retry`。同じ検査と対象は重ねない。"""
    earlier = () if retry is None else retry.problems
    return Retry(previous, tuple(dict.fromkeys((*earlier, *problems))))


class Llm(Protocol):
    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        """デッキの問題を論点ごとのグループに分ける(R-3)。

        問題を束ねて受け取る入口は `Deck` だけにする(R-11)。素の問題列を受け取ると、
        別デッキの問題を混ぜて分類させる経路が開く。

        分割呼び出しの失敗は例外にせず `failures` に積む。1回の失敗で全体を捨てると、
        失敗件数という事実そのものが下流のサマリに届かなくなる。
        """
        ...

    def summarize_cluster(self, members: Deck, retry: Retry[str] | None = None) -> str:
        """クラスタの要約をデッキの言語で書く(R-3)。語句の実在性は `grounding/` が判定する。

        `members` はそのクラスタの問題だけを持つ `Deck`。`Cluster` は要約を持って初めて
        構築できるので、要約の入力にはならない。`retry` は作り直しのときの前回の要約と落ちた検査(D-245)。
        """
        ...

    def generate_recall(
        self, question: Question, language: Language, retry: Retry[RecallDraft] | None = None
    ) -> RecallDraft:
        """1問分の想起キー・答え・識別ポイントを `language` で作る(R-4)。

        `retry` は作り直しのときの前回の素材と落ちた検査(D-245)。
        """
        ...

    def describe_term(self, term: str, language: Language) -> str:
        """専門用語の説明を `language` で書く(R-5)。"""
        ...

    def check_entailments(self, reference: str, statements: Sequence[EntailmentStatement]) -> tuple[Entailment, ...]:
        """各主張が `reference`(正解・公式解説)と矛盾しないかを1回で判定し、主張の順の判定値を返す(NFR-1)。

        判定値は主張の ID で対応づけ、ID の欠けや余りは呼び出しの失敗にする(ADR-029)。
        """
        ...
