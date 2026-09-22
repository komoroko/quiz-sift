"""1デッキに閉じた学習記録へのアクセス(R-11 / AR-18)。

`deck_id` 列はただの列で、`WHERE deck_id = ?` を1本書き忘れた集計は型検査に引っかからない。
学習記録の読み書きをこのクラスに集め、すべてのクエリを `self.deck_id` で絞る。書き込みは
別デッキの記録を拒む。
"""

from datetime import datetime
from pathlib import Path

from quiz_sift.domain.attempt import AttemptRecord, ClusterStat
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId, SessionId
from quiz_sift.store.location import connect


def _to_text(moment: datetime) -> str:
    # タイムゾーン無しの日時が混ざると、読み戻した後の比較(R-10 のタイブレーク)が TypeError で落ちる。
    if moment.tzinfo is None:
        raise ValueError("タイムゾーンの無い日時は記録しない")
    return moment.isoformat()


class DeckScopedStore:
    """`deck_id` 1つ分の学習記録。接続は操作ごとに開いて閉じ、インスタンスは接続を保持しない。"""

    def __init__(self, deck_id: DeckId, root: Path) -> None:
        self.deck_id = deck_id
        self._root = root

    def record_attempt(self, attempt: AttemptRecord) -> None:
        """回答1件を追記する。"""
        if attempt.session_id.deck_id != self.deck_id:
            raise ValueError(f"別デッキの回答は記録しない: {attempt.session_id.deck_id}")
        with connect(self._root) as conn:
            conn.execute(
                "INSERT INTO attempts (deck_id, session_id, question_id, chosen_option_id, correct,"
                " elapsed_seconds, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self.deck_id,
                    attempt.session_id.local_id,
                    attempt.question_id.local_id,
                    attempt.chosen.local_id,
                    int(attempt.correct),
                    attempt.elapsed_seconds,
                    _to_text(attempt.answered_at),
                ),
            )

    def attempts(self, session_id: SessionId | None = None) -> tuple[AttemptRecord, ...]:
        """このデッキの回答を記録順に返す。`session_id` を渡すとそのセッションに絞る。"""
        if session_id is not None and session_id.deck_id != self.deck_id:
            raise ValueError(f"別デッキのセッションは読まない: {session_id.deck_id}")
        query = (
            "SELECT session_id, question_id, chosen_option_id, correct, elapsed_seconds, answered_at"
            " FROM attempts WHERE deck_id = ?"
        )
        params: tuple[str, ...] = (self.deck_id,)
        if session_id is not None:
            query += " AND session_id = ?"
            params += (session_id.local_id,)
        with connect(self._root) as conn:
            rows = conn.execute(query + " ORDER BY rowid", params).fetchall()
        records = []
        for session, question, chosen, correct, elapsed, answered_at in rows:
            question_id = QuestionId(self.deck_id, question)
            records.append(
                AttemptRecord(
                    session_id=SessionId(self.deck_id, session),
                    question_id=question_id,
                    chosen=OptionId(question_id, chosen),
                    correct=bool(correct),
                    elapsed_seconds=elapsed,
                    answered_at=datetime.fromisoformat(answered_at),
                )
            )
        return tuple(records)

    def record_cluster_stat(self, stat: ClusterStat) -> None:
        """クラスタ1つ分の出題回数・誤答回数・最終学習日時を書く。既にあれば置き換える。"""
        if stat.cluster_id.deck_id != self.deck_id:
            raise ValueError(f"別デッキのクラスタは記録しない: {stat.cluster_id.deck_id}")
        last = None if stat.last_studied_at is None else _to_text(stat.last_studied_at)
        with connect(self._root) as conn:
            conn.execute(
                "INSERT INTO cluster_stats (deck_id, cluster_id, presented, wrong, last_studied_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (deck_id, cluster_id) DO UPDATE SET"
                " presented = excluded.presented, wrong = excluded.wrong,"
                " last_studied_at = excluded.last_studied_at",
                (self.deck_id, stat.cluster_id.local_id, stat.presented, stat.wrong, last),
            )

    def cluster_stats(self) -> tuple[ClusterStat, ...]:
        """このデッキのクラスタ集計をクラスタ ID 昇順で返す。"""
        with connect(self._root) as conn:
            rows = conn.execute(
                "SELECT cluster_id, presented, wrong, last_studied_at FROM cluster_stats"
                " WHERE deck_id = ? ORDER BY cluster_id",
                (self.deck_id,),
            ).fetchall()
        return tuple(
            ClusterStat(
                cluster_id=ClusterId(self.deck_id, cluster),
                presented=presented,
                wrong=wrong,
                last_studied_at=None if last is None else datetime.fromisoformat(last),
            )
            for cluster, presented, wrong, last in rows
        )
