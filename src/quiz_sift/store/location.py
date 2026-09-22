"""学習記録ストアの所在と形式(R-12 / ADR-005)。

所在(`store_path`)とスキーマ(`SCHEMA`)をこの1箇所で定義し、`sqlite3` を import するのも
接続を開くのもこのモジュールだけにする。CLI と server が別々に所在を決めると、同じ学習記録を
見ているつもりで別のファイルへ書く経路が開く。

接続は1操作ごとに開いて閉じる(AR-15)。`ThreadingHTTPServer` のハンドラスレッドで起動時の接続を
使い回すと `check_same_thread` に当たって落ちる。WAL は CLI と server の**プロセス間**の同時アクセスの
ためのもので、スレッドの制約には効かない。
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

STORE_FILENAME = "learning.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    deck_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    chosen_option_id TEXT NOT NULL,
    correct INTEGER NOT NULL CHECK (correct IN (0, 1)),
    elapsed_seconds REAL NOT NULL CHECK (elapsed_seconds >= 0),
    answered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_by_session ON attempts (deck_id, session_id);
CREATE TABLE IF NOT EXISTS cluster_stats (
    deck_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    presented INTEGER NOT NULL CHECK (presented >= 0),
    wrong INTEGER NOT NULL CHECK (wrong >= 0 AND wrong <= presented),
    last_studied_at TEXT,
    PRIMARY KEY (deck_id, cluster_id)
);
"""
"""学習記録の形式。`attempts` は追記のみ(既存回答の上書きは不可逆なので経路を置かない)。
`cluster_stats` はクラスタごとの集計で、R-10 の並べ替えの根拠として上書き更新する。
"""


def store_path(root: Path) -> Path:
    """`root`(リポジトリの作業ルート)に対する学習記録ファイルの所在。

    `out/` は `.gitignore` 対象で、本人の学習記録は追跡しない。全デッキで1ファイルを共有し、
    分離は `deck_id` 列と `DeckScopedStore` が受け持つ(R-11)。
    """
    return root / "out" / STORE_FILENAME


@contextmanager
def connect(root: Path) -> Iterator[sqlite3.Connection]:
    """1操作分の接続を開き、正常終了なら commit、例外なら rollback して必ず閉じる。

    store/ の内部専用。store/ の外へ接続を渡す API は置かない。
    """
    path = store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        with conn:
            yield conn
    finally:
        conn.close()
