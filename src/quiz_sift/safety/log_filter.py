"""教材本文のログ遮断(NFR-2)。

ログハンドラに `MaterialTextFilter` を付け、取り込んだ問題の本文(問題文・選択肢・公式解説)を
登録しておくと、レコードの本文・例外テキスト・スタック情報に現れた登録済みの文字列を伏せる。
ロガーではなくハンドラに付けること: ロガーのフィルタは子ロガーから伝播してきたレコードには効かない。

素の例外トレースバック(`logging` を通らない `sys.excepthook`)にはこのフィルタが効かない。
そちらは本文を持つ dataclass に `repr=False` を指定して塞ぐ(AR-17)。
"""

import json
import logging
from collections.abc import Callable

from quiz_sift.domain.question import Question

MATERIAL_MASK = "[教材本文を伏せた]"


def escaped_forms(text: str) -> set[str]:
    """本文そのものと、ログに出るときエスケープされた形(repr と JSON)を返す。

    `%r` / `!r` / `repr()` は改行・バックスラッシュ・引用符をエスケープするので、本文そのままの
    文字列では一致しない。repr の中身は外側の引用符で変わるため両方を入れる。末尾に足した引用符が
    外側の引用符を決める: `"` を含む文字列は `'` で囲まれ、`'` だけを含む文字列は `"` で囲まれる。
    """
    forms = {
        text,
        repr(text + '"')[1:-2],
        json.dumps(text)[1:-1],
        json.dumps(text, ensure_ascii=False)[1:-1],
    }
    if '"' not in text:
        forms.add(repr(text + "'")[1:-2])
    return forms


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
        # 長い順。短い本文が長い本文の一部だった場合に、長い方の残りが漏れない。
        self._ordered: list[str] = []

    def register_question(self, question: Question) -> None:
        """問題文・選択肢・公式解説を、repr と JSON でエスケープされた形も含めて伏せる対象に加える。"""
        texts: list[str] = [question.stem, *(option.text for option in question.options)]
        if question.explanation is not None:
            texts.append(question.explanation)
        for text in texts:
            # 空文字列を置き換えると全文字の間に伏せ字が入る。
            if text:
                self._texts.update(escaped_forms(text))
        self._ordered = sorted(self._texts, key=len, reverse=True)

    def mask(self, text: str) -> str:
        for material in self._ordered:
            text = text.replace(material, MATERIAL_MASK)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        rewrite_record(record, self.mask)
        return True
