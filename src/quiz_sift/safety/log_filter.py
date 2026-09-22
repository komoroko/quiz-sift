"""教材本文のログ遮断(NFR-2)。

ログハンドラに `MaterialTextFilter` を付け、取り込んだ問題の本文(問題文・選択肢・公式解説)を
登録しておくと、レコードの本文・例外テキスト・スタック情報に現れた登録済みの文字列を伏せる。
ロガーではなくハンドラに付けること: ロガーのフィルタは子ロガーから伝播してきたレコードには効かない。

素の例外トレースバック(`logging` を通らない `sys.excepthook`)にはこのフィルタが効かない。
そちらは本文を持つ dataclass に `repr=False` を指定して塞ぐ(AR-17)。
"""

import logging
from collections.abc import Callable

from quiz_sift.domain.question import Question

MATERIAL_MASK = "[教材本文を伏せた]"


def rewrite_record(record: logging.LogRecord, transform: Callable[[str], str]) -> None:
    """レコードのうちハンドラが書き出す文字列すべてに `transform` をかける。

    メッセージは引数を埋め込んだ後の文字列にしてから変換し、`args` は空にする。例外は
    フォーマッタより先に `exc_text` へ整形しておく。フォーマッタは `exc_text` があればそれを使う。
    """
    record.msg = transform(record.getMessage())
    record.args = None
    if record.exc_info and not record.exc_text:
        record.exc_text = logging.Formatter().formatException(record.exc_info)
    if record.exc_text:
        record.exc_text = transform(record.exc_text)
    if record.stack_info:
        record.stack_info = transform(record.stack_info)


class MaterialTextFilter(logging.Filter):
    """登録された教材本文を伏せる。レコードは落とさない。"""

    def __init__(self) -> None:
        super().__init__()
        self._texts: set[str] = set()

    def register_question(self, question: Question) -> None:
        """問題文・選択肢・公式解説を伏せる対象に加える。"""
        self._texts.add(question.stem)
        self._texts.update(option.text for option in question.options)
        if question.explanation is not None:
            self._texts.add(question.explanation)

    def mask(self, text: str) -> str:
        # 長い順に置き換える。短い本文が長い本文の一部だった場合に、長い方の残りが漏れない。
        for material in sorted(self._texts, key=len, reverse=True):
            text = text.replace(material, MATERIAL_MASK)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        rewrite_record(record, self.mask)
        return True
