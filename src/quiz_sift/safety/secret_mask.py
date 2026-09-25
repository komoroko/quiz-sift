"""認証情報のログ遮断(NFR-3)。

`SecretMaskFilter` をログハンドラに付けると、API キーと Cookie / Set-Cookie / セッション ID 系の値を
伏せる。Udemy の認証情報はプログラムが保存しないが、CDP 接続はセッションクッキーを読み出せる
(AR-28)ので、Cookie 系をマスク対象から外さない。`claude` の子プロセスのログ(NFR-8)に向けて、
OAuth トークン(`sk-ant-oat…`)と資格情報ファイル `.credentials.json` のパスも伏せる。

伏せるのは値の形ではなく、値の置かれ方で決める: 既知のキー形式(API キーと OAuth トークンの
`sk-ant-…`、`Bearer …`)と、資格情報ファイルのパス、
名前が認証情報らしい項目の値(`name=value`、`"name": "value"`、`Name: value` の行)、CDP の
cookie オブジェクト `{"name": ..., "value": ...}` の値(名前を問わない)。値は引用符で囲まれて
いてもよい(`%r` や `f"{sessionid=}"` は `sessionid='…'` を出す)。余計に伏せる向きの誤りは許し、
漏らす向きの誤りを避ける。
"""

import logging
import re

from quiz_sift.safety.log_filter import rewrite_record

SECRET_MASK = "***"

_SECRET_NAME = r"[\w.-]*(?:cookie|session|token|jwt|csrf|password|passwd|secret|api[_-]?key|authorization)[\w.-]*"
# 引用符で囲まれた値(repr のバックスラッシュエスケープを含む)。
_QUOTED_VALUE = r"'(?:[^'\\\r\n]|\\.)*'" + r'|"(?:[^"\\\r\n]|\\.)*"'
# 同じく、開き引用符を `q` に取って置換後も同じ引用符で囲めるようにした形。
_QUOTED_VALUE_Q = r"""(?P<q>["'])(?:(?!(?P=q))[^\\\r\n]|\\.)*(?P=q)"""

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # API キー(sk-ant-api…)と OAuth トークン(sk-ant-oat…)。
    (re.compile(r"sk-ant-[\w-]+"), SECRET_MASK),
    # 資格情報ファイルのパス(`~/.claude/.credentials.json` など)。ディレクトリ部分ごと伏せる。
    (re.compile(r"""[^\s'"=:]*\.credentials\.json"""), SECRET_MASK),
    (re.compile(r"(?i)\b(bearer\s+)[\w.~+/-]+=*"), rf"\g<1>{SECRET_MASK}"),
    # CDP の cookie オブジェクト: 名前と値が別の項目に分かれている。名前が認証情報らしくない
    # cookie もセッションを担いうるので、名前を問わず値を伏せる。
    (
        re.compile(rf"""(?i)(["']name["']\s*:\s*(?:{_QUOTED_VALUE})\s*,\s*["']value["']\s*:\s*){_QUOTED_VALUE_Q}"""),
        rf"\g<1>\g<q>{SECRET_MASK}\g<q>",
    ),
    # "name": "value" / 'name': 'value'
    (
        re.compile(rf"""(?i)(["']{_SECRET_NAME}["']\s*:\s*){_QUOTED_VALUE_Q}"""),
        rf"\g<1>\g<q>{SECRET_MASK}\g<q>",
    ),
    # ヘッダ行 Name: value(値は行末まで。Cookie は `; ` で複数の値を持つ)と Name: 'value'
    (
        re.compile(rf"""(?im)(\b{_SECRET_NAME}\s*:[ \t]*)(?:{_QUOTED_VALUE}|(?!["'\s])[^\r\n]+)"""),
        rf"\g<1>{SECRET_MASK}",
    ),
    # name=value と name='value'
    (
        re.compile(rf"""(?i)(\b{_SECRET_NAME}\s*=\s*)(?!{re.escape(SECRET_MASK)})(?:{_QUOTED_VALUE}|[^\s;&,'"]+)"""),
        rf"\g<1>{SECRET_MASK}",
    ),
)


def mask_secrets(text: str) -> str:
    """`text` 中の認証情報を伏せた文字列を返す。"""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretMaskFilter(logging.Filter):
    """認証情報を伏せる。レコードは落とさない。"""

    def filter(self, record: logging.LogRecord) -> bool:
        rewrite_record(record, mask_secrets)
        return True
