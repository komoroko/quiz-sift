"""抽出の失敗(R-2 / AR-5)。要素レベルの失敗と取得レベルの失敗を別の型で受ける。

- `ExtractionFailure` — ページは取れたが、ある問題の要素が見つからない・空・一意に決まらない。
- `FetchFailure` — ページそのものが取れなかった(CDP の切断、認証切れ、ボット検知で準備が整わない、
  問題が1問も無いページ)。

どちらも「抽出できなかった」件数に算入する。取得失敗を要素失敗に混ぜたり黙って捨てたりすると、
取れなかったページの分だけ問題が消えたことが報告から見えなくなる。
"""

from dataclasses import dataclass
from enum import Enum


class ExtractField(Enum):
    """抽出できなかったフィールド。"""

    STEM = "stem"
    OPTIONS = "options"
    """選択肢が4件でない。"""
    OPTION_TEXT = "option_text"
    ANSWER = "answer"
    """正解マークも受験者の選択も一意に決まらない。"""


@dataclass(frozen=True, slots=True)
class ExtractionFailure:
    """要素レベルの失敗1件。`question_index` は全ページ通しの問題ブロックの0始まりの位置。"""

    question_index: int
    missing_field: ExtractField
    element_path: str


class FetchFailureReason(Enum):
    """ページを取れなかった理由。"""

    DISCONNECTED = "disconnected"
    """CDP に接続できない、または接続が切れた。"""
    AUTH_EXPIRED = "auth_expired"
    """ログインページへ飛ばされた。"""
    NOT_READY = "not_ready"
    """時間内に問題ブロックが現れなかった。ボット検知のチャレンジページはここに落ちる。"""
    NO_QUESTIONS = "no_questions"
    """取れた HTML に問題ブロックが1つも無い。"""


@dataclass(frozen=True, slots=True)
class FetchFailure:
    """取得レベルの失敗1件。"""

    url: str
    reason: FetchFailureReason
