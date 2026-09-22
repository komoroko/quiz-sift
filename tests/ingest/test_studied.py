"""既習フラグ列の読み取りと解決順(受入基準 A-6)。"""

from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ingest.deck import ingest_deck

from .conftest import DECK, WriteDeck, question_row

ROWS = [
    question_row("per-studied", studied="studied"),
    question_row("per-unseen", studied="unseen"),
    question_row("absent"),
    question_row("null", studied=None),
]


def resolved(write_deck: WriteDeck, default_studied: str | None) -> dict[str, StudiedFlag]:
    data_root = write_deck({"q.jsonl": ROWS}, default_studied=default_studied)
    return {q.question_id.local_id: q.studied for q in ingest_deck(data_root, DECK).deck.questions}


def test_per_question_flag_overrides_deck_default(write_deck: WriteDeck) -> None:
    """受入基準 A-6: 個別 > デッキ既定 > unknown。既習フラグ列を無視する実装はここで赤になる。"""
    # デッキ既定と逆の個別指定が勝つ。列を読まずデッキ既定へ落とす実装では両方 UNSEEN / STUDIED に揃う。
    assert resolved(write_deck, "unseen") == {
        "per-studied": StudiedFlag.STUDIED,
        "per-unseen": StudiedFlag.UNSEEN,
        "absent": StudiedFlag.UNSEEN,
        "null": StudiedFlag.UNSEEN,
    }
    assert resolved(write_deck, "studied") == {
        "per-studied": StudiedFlag.STUDIED,
        "per-unseen": StudiedFlag.UNSEEN,
        "absent": StudiedFlag.STUDIED,
        "null": StudiedFlag.STUDIED,
    }
    # 個別指定もデッキ既定も無いときに限り unknown。既定値で埋めない。
    assert resolved(write_deck, None) == {
        "per-studied": StudiedFlag.STUDIED,
        "per-unseen": StudiedFlag.UNSEEN,
        "absent": StudiedFlag.UNKNOWN,
        "null": StudiedFlag.UNKNOWN,
    }
