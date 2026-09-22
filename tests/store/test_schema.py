"""A-4: スキーマが下流(T-015 / T-016 / T-017)の必要とする列を持ち、2デッキ分の記録が往復する(C-008 / C-010)。"""

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from quiz_sift.store import location
from quiz_sift.store.deck_scope import DeckScopedStore

from .conftest import DeckLog

DOWNSTREAM_COLUMNS = {
    # 選択した選択肢・正誤・所要時間・セッション ID(R-8)
    "attempts": {"deck_id", "session_id", "question_id", "chosen_option_id", "correct", "elapsed_seconds"},
    # 最終学習日時・クラスタ別誤答回数(R-10)
    "cluster_stats": {"deck_id", "cluster_id", "presented", "wrong", "last_studied_at"},
}


def test_schema_round_trips_all_downstream_fields(tmp_path: Path, two_decks: tuple[DeckLog, DeckLog]) -> None:
    for log in two_decks:
        store = DeckScopedStore(log.deck_id, tmp_path)
        for attempt in log.attempts:
            store.record_attempt(attempt)
        for stat in log.cluster_stats:
            store.record_cluster_stat(stat)

    conn = sqlite3.connect(location.store_path(tmp_path))
    try:
        for table, required in DOWNSTREAM_COLUMNS.items():
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert required <= columns, (table, required - columns)
    finally:
        conn.close()

    # 別インスタンス(= 再起動後の別プロセス相当)で読み戻し、全フィールドが一致する。
    for log in two_decks:
        reopened = DeckScopedStore(log.deck_id, tmp_path)
        attempts = reopened.attempts()
        assert attempts == log.attempts
        for got, want in zip(attempts, log.attempts, strict=True):
            assert (got.chosen, got.correct, got.elapsed_seconds) == (want.chosen, want.correct, want.elapsed_seconds)
            assert got.answered_at.utcoffset() == want.answered_at.utcoffset()
        assert reopened.cluster_stats() == log.cluster_stats

    # クラスタ集計は置き換わる(R-10 の順序の根拠は最新の値)。回答は追記のみ。
    deck_a = two_decks[0]
    store = DeckScopedStore(deck_a.deck_id, tmp_path)
    first = deck_a.cluster_stats[0]
    assert first.last_studied_at is not None
    updated = replace(first, presented=6, wrong=2, last_studied_at=first.last_studied_at + timedelta(days=3))
    store.record_cluster_stat(updated)
    assert store.cluster_stats() == (updated, *deck_a.cluster_stats[1:])
    store.record_attempt(deck_a.attempts[0])
    assert store.attempts() == (*deck_a.attempts, deck_a.attempts[0])

    # タイムゾーンの無い日時は、読み戻し後の比較を壊すので記録しない。
    with pytest.raises(ValueError, match="タイムゾーン"):
        store.record_attempt(replace(deck_a.attempts[0], answered_at=datetime(2026, 9, 20, 9, 0)))
