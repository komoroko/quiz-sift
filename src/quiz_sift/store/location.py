"""学習記録ストアの所在と形式(R-12 / ADR-005 / ADR-021)。

所在(`store_path`)とスキーマ(`SCHEMA`)をこの1箇所で定義し、`sqlite3` を import するのも
接続を開くのもこのモジュールだけにする。CLI と server が別々に所在を決めると、同じ学習記録を
見ているつもりで別のファイルへ書く経路が開く。

接続は1操作ごとに開いて閉じる(AR-15)。`ThreadingHTTPServer` のハンドラスレッドで起動時の接続を
使い回すと `check_same_thread` に当たって落ちる。WAL は CLI と server の**プロセス間**の同時アクセスの
ためのもので、スレッドの制約には効かない。
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

STORE_FILENAME = "learning.sqlite3"

SCHEMA_VERSION = 2
"""`PRAGMA user_version` に書くスキーマの版。v2 で `chosen_option_id` を集合の `chosen_option_ids` に変えた。"""

SCHEMA = """
CREATE TABLE attempts (
    deck_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    chosen_option_ids TEXT NOT NULL,
    correct INTEGER NOT NULL CHECK (correct IN (0, 1)),
    elapsed_seconds REAL NOT NULL CHECK (elapsed_seconds >= 0),
    answered_at TEXT NOT NULL
);
CREATE INDEX attempts_by_session ON attempts (deck_id, session_id);
CREATE TABLE cluster_stats (
    deck_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    presented INTEGER NOT NULL CHECK (presented >= 0),
    wrong INTEGER NOT NULL CHECK (wrong >= 0 AND wrong <= presented),
    last_studied_at TEXT,
    PRIMARY KEY (deck_id, cluster_id)
);
"""
"""学習記録の形式。`attempts` は追記のみ(既存回答の上書きは不可逆なので経路を置かない)。
`chosen_option_ids` は選んだ選択肢の local_id を整列した JSON 配列。
`cluster_stats` はクラスタごとの集計で、R-10 の並べ替えの根拠として上書き更新する。
"""


class LegacyStoreError(RuntimeError):
    """同じ所在に版の違う学習記録ストアがある(D-203 / D-204)。

    製品は旧ストアを読み替えも削除もしない。所在と版を示して、人に別名への退避を求める。
    """

    def __init__(self, path: Path, version: int) -> None:
        super().__init__(
            f"学習記録ストア {path} は旧形式(user_version = {version}、この版が読むのは {SCHEMA_VERSION})。"
            f"記録は消さずに止まった。別名へ退避してから(例: mv {path} {path}.v{version})やり直すこと。"
        )
        self.path = path
        self.version = version


def store_path(root: Path) -> Path:
    """`root`(リポジトリの作業ルート)に対する学習記録ファイルの所在。

    `out/` は `.gitignore` 対象で、本人の学習記録は追跡しない。全デッキで1ファイルを共有し、
    分離は `deck_id` 列と `DeckScopedStore` が受け持つ(R-11)。
    """
    return root / "out" / STORE_FILENAME


_initialized: set[Path] = set()
_initialize_lock = threading.Lock()


def _initialize(path: Path) -> None:
    """版の確認・スキーマ作成・WAL への切り替えを、このプロセスで所在ごとに1度だけ直列に行う。

    テーブルの作成と `user_version` の書き込みは1つのトランザクションで行う(D-222)。分けると、
    途中のクラッシュや別プロセスの同時の初回接続が「テーブルあり・版 0」を見て旧形式と誤判定する。
    版が `SCHEMA_VERSION` でなく、テーブルがあるか版が 0 でないものは旧形式として `LegacyStoreError`
    で止まる(fail closed)。WAL への切り替えはファイルのヘッダを書き換えるので、版を確かめた後に行う。

    `PRAGMA journal_mode = WAL` は排他ロックを要し、他の接続と競合すると busy handler を待たずに
    `database is locked` を返しうる。空のファイルへ複数のハンドラスレッドが同時に初回接続すると
    この競合が起きるので、操作ごとの接続ではなくここで1本の接続から済ませる。
    """
    with _initialize_lock:
        if path in _initialized:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                (version,) = conn.execute("PRAGMA user_version").fetchone()
                (tables,) = conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()
                if version != SCHEMA_VERSION:
                    if tables or version != 0:
                        raise LegacyStoreError(path, version)
                    # executescript はトランザクションを先に commit するので、文ごとに流す。
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            conn.execute("PRAGMA journal_mode = WAL")
        finally:
            conn.close()
        _initialized.add(path)


@contextmanager
def connect(root: Path) -> Iterator[sqlite3.Connection]:
    """1操作分の接続を開き、正常終了なら commit、例外なら rollback して必ず閉じる。

    store/ の内部専用。store/ の外へ接続を渡す API は置かない。
    """
    path = store_path(root).resolve()
    _initialize(path)
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
