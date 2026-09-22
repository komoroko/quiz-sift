"""A-4 / A-5 / A-6: 既習フラグ別・事前/事後の正答率と差分、限界開示文。"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from quiz_sift.domain.attempt import AttemptRecord
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import SessionId
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.scoring.report import LIMITS_DISCLOSURE, PrePost, Rate, build_report, render_report
from quiz_sift.store.deck_scope import DeckScopedStore

from .conftest import DECK, OTHER_DECK, POST, PRE, make_deck, wrong_option

AT = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def _log(deck: Deck, session: SessionId, correct_ids: set[str]) -> list[AttemptRecord]:
    """`correct_ids` の問題だけ正解した1セッション分の合成ログ。"""
    return [
        AttemptRecord(
            session_id=session,
            question_id=q.question_id,
            chosen=q.correct_option_id if q.question_id.local_id in correct_ids else wrong_option(q),
            correct=q.question_id.local_id in correct_ids,
            elapsed_seconds=4.0,
            answered_at=AT,
        )
        for q in deck.questions
    ]


def test_report_splits_by_studied_flag(deck: Deck) -> None:
    # studied = q1,q2 / unseen = q3,q4 / unknown = q5,q6。群ごとに違う正答率になるよう答える。
    attempts = _log(deck, PRE, {"q1", "q2", "q3"}) + _log(deck, POST, {"q1", "q2", "q3", "q4", "q5"})
    report = build_report(deck, attempts, PRE, POST)
    assert set(report.by_studied) == set(StudiedFlag)
    assert report.by_studied[StudiedFlag.STUDIED] == PrePost(Rate(2, 2), Rate(2, 2))
    assert report.by_studied[StudiedFlag.UNSEEN] == PrePost(Rate(1, 2), Rate(2, 2))
    assert report.by_studied[StudiedFlag.UNKNOWN] == PrePost(Rate(0, 2), Rate(1, 2))
    text = render_report(report)
    assert "| studied | 100.0% (2/2) | 100.0% (2/2) | +0.0pt |" in text
    assert "| unseen | 50.0% (1/2) | 100.0% (2/2) | +50.0pt |" in text
    assert "| unknown | 0.0% (0/2) | 50.0% (1/2) | +50.0pt |" in text


def test_limits_disclosure_is_always_present(deck: Deck) -> None:
    # hold-out 不在とテスト効果の両方に触れていること。
    assert "hold-out" in LIMITS_DISCLOSURE
    assert "転移" in LIMITS_DISCLOSURE
    assert "テスト効果" in LIMITS_DISCLOSURE
    for attempts in ([], _log(deck, PRE, {"q1"}) + _log(deck, POST, {"q1", "q2"})):
        report = build_report(deck, attempts, PRE, POST)
        assert report.limits == LIMITS_DISCLOSURE
        assert LIMITS_DISCLOSURE in render_report(report)


def test_pre_post_rates_and_delta(deck: Deck) -> None:
    attempts = _log(deck, PRE, {"q1", "q3"}) + _log(deck, POST, {"q1", "q2", "q3", "q4", "q6"})
    report = build_report(deck, attempts, PRE, POST)
    assert report.overall.pre == Rate(2, 6)
    assert report.overall.post == Rate(5, 6)
    assert report.overall.delta == pytest.approx(0.5)
    assert "| 全体 | 33.3% (2/6) | 83.3% (5/6) | +50.0pt |" in render_report(report)


def test_empty_group_is_not_zero_percent(deck: Deck) -> None:
    report = build_report(deck, _log(deck, PRE, {"q1"}), PRE, POST)
    assert report.overall.post.ratio is None
    assert report.overall.delta is None
    assert "| 全体 | 16.7% (1/6) | — (0/0) | — |" in render_report(report)


def test_report_from_store_is_separated_by_deck(tmp_path: Path, deck: Deck) -> None:
    # 2デッキが同じ local_id を持ち、同じストアに記録されていても、集計は混ざらない(R-11)。
    other = make_deck(OTHER_DECK)
    other_pre, other_post = SessionId(OTHER_DECK, PRE.local_id), SessionId(OTHER_DECK, POST.local_id)
    own_store, other_store = DeckScopedStore(DECK, tmp_path), DeckScopedStore(OTHER_DECK, tmp_path)
    for record in _log(deck, PRE, set()) + _log(deck, POST, {"q1"}):
        own_store.record_attempt(record)
    for record in _log(other, other_pre, {"q1", "q2", "q3"}) + _log(other, other_post, set()):
        other_store.record_attempt(record)

    report = build_report(deck, own_store.attempts(), PRE, POST)
    assert (report.overall.pre, report.overall.post) == (Rate(0, 6), Rate(1, 6))

    with pytest.raises(ValueError):
        build_report(deck, other_store.attempts(), PRE, POST)
