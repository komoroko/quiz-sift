"""A-3: WAL が有効で、接続は操作ごとに開かれて閉じられ、スレッドをまたいで使い回されない(C-012)。"""

import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from quiz_sift.store import location
from quiz_sift.store.deck_scope import DeckScopedStore

from .conftest import DeckLog


class TrackedConnection(sqlite3.Connection):
    """開いたスレッドと閉じたスレッドを覚える接続。閉じられなければ `closed_in` は `None` のまま。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.opened_in = threading.get_ident()
        self.closed_in: int | None = None

    def close(self) -> None:
        super().close()
        self.closed_in = threading.get_ident()


def test_wal_and_per_request_connections(
    tmp_path: Path, two_decks: tuple[DeckLog, DeckLog], monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[TrackedConnection] = []
    lock = threading.Lock()
    real_connect = sqlite3.connect

    def tracking_connect(*args: Any, **kwargs: Any) -> TrackedConnection:
        # check_same_thread を外すと、スレッドをまたいだ使い回しが検出されずに通ってしまう。
        assert kwargs.get("check_same_thread", True) is True
        conn = real_connect(*args, factory=TrackedConnection, **kwargs)
        assert isinstance(conn, TrackedConnection)
        with lock:
            opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)

    deck_a, deck_b = two_decks
    stores = {log.deck_id: DeckScopedStore(log.deck_id, tmp_path) for log in two_decks}
    # 1つのインスタンスを複数のハンドラスレッドから同時に使う(ThreadingHTTPServer の形)。
    writes: list[Callable[[], object]] = [
        partial(stores[a.session_id.deck_id].record_attempt, a) for a in deck_a.attempts + deck_b.attempts
    ]
    writes += [
        partial(stores[s.cluster_id.deck_id].record_cluster_stat, s)
        for s in deck_a.cluster_stats + deck_b.cluster_stats
    ]
    reads: list[Callable[[], object]] = [store.attempts for store in stores.values()]
    reads += [store.cluster_stats for store in stores.values()]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for job in writes, reads:
            for future in [pool.submit(op) for op in job]:
                future.result()

    # 1操作 = 1接続。どれも開いたスレッドで閉じられ、残っている接続は無い。
    assert len(opened) == len(writes) + len(reads)
    assert len({conn.opened_in for conn in opened}) > 1
    assert all(conn.closed_in == conn.opened_in for conn in opened)
    # 並行書き込みの到着順は決まらないので、中身だけを突き合わせる。
    for log in two_decks:
        assert sorted(stores[log.deck_id].attempts(), key=repr) == sorted(log.attempts, key=repr)

    # WAL はファイルに残る設定なので、外から開いた別の接続でも wal と読める。
    monkeypatch.undo()
    probe = sqlite3.connect(location.store_path(tmp_path))
    try:
        assert probe.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        probe.close()
