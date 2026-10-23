"""A-2: 学習記録へのアクセスは DeckScopedStore(deck_id) 経由に限られ、2デッキの記録が混ざらない(C-011)。"""

import inspect
import typing
from pathlib import Path

import pytest

from quiz_sift.domain.attempt import ClusterStat
from quiz_sift.domain.ids import ClusterId, SessionId
from quiz_sift.store.deck_scope import DeckScopedStore

from .conftest import DeckLog


def _fill(root: Path, log: DeckLog) -> DeckScopedStore:
    store = DeckScopedStore(log.deck_id, root)
    for attempt in log.attempts:
        store.record_attempt(attempt)
    for stat in log.cluster_stats:
        store.record_cluster_stat(stat)
    return store


def test_two_decks_do_not_mix(tmp_path: Path, two_decks: tuple[DeckLog, DeckLog]) -> None:
    deck_a, deck_b = two_decks
    store_a = _fill(tmp_path, deck_a)
    store_b = _fill(tmp_path, deck_b)

    # 同じファイルを共有していても、読み出しはそれぞれのデッキの分だけ。
    assert store_a.attempts() == deck_a.attempts
    assert store_b.attempts() == deck_b.attempts
    for log, store in ((deck_a, store_a), (deck_b, store_b)):
        session = SessionId(log.deck_id, "s1")
        assert store.attempts(session) == tuple(a for a in log.attempts if a.session_id == session)
        assert store.cluster_stats() == log.cluster_stats

    # 正答率の集計も混ざらない(A は s1 全問正解、B は s1 全問誤答)。
    def s1_rate(store: DeckScopedStore) -> float:
        records = store.attempts(SessionId(store.deck_id, "s1"))
        return sum(r.correct for r in records) / len(records)

    assert (s1_rate(store_a), s1_rate(store_b)) == (1.0, 0.0)

    # 別デッキの記録の書き込み・別デッキのセッションの読み出しは拒む。
    with pytest.raises(ValueError, match="別デッキ"):
        store_a.record_attempt(deck_b.attempts[0])
    with pytest.raises(ValueError, match="別デッキ"):
        store_a.record_cluster_stat(ClusterStat(ClusterId(deck_b.deck_id, "c9"), 1, 1, deck_b.attempts[0].answered_at))
    with pytest.raises(ValueError, match="別デッキ"):
        store_a.attempts(SessionId(deck_b.deck_id, "s1"))
    assert store_b.attempts() == deck_b.attempts

    # 生の接続を返す公開 API も、接続を保持する属性も無い。
    for name, member in inspect.getmembers(DeckScopedStore, inspect.isfunction):
        hints = typing.get_type_hints(member)
        assert "Connection" not in repr(hints.get("return")), name
    assert all("conn" not in attribute.lower() for attribute in vars(store_a)), vars(store_a)
