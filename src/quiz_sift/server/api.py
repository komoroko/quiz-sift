"""API の中身(R-12)。HTTP を知らない層で、ハンドラはここを呼んで結果を JSON にするだけ。

`Quizmaster` は出題から回答までの計測をメモリに持つので、デッキごとに1つをサーバの寿命の間
保持する。ハンドラは並行に走るので、その状態はデッキごとのロックの内側でだけ触る。学習記録の
接続は `DeckScopedStore` が操作ごとに開いて閉じる(AR-15)。
"""

import re
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quiz_sift.decks.registry import discover_decks, load_deck_manifest
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId, SessionId
from quiz_sift.domain.question import Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ingest.deck import ingest_deck
from quiz_sift.ports.clock import Clock
from quiz_sift.scoring.report import PrePost, Rate, build_report, render_report
from quiz_sift.scoring.session import Quizmaster
from quiz_sift.store.deck_scope import DeckScopedStore

LOCAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
"""URL から受け取るセッション ID の形。区切り文字を含む値は受け取らない。"""


class NotFoundError(LookupError):
    """指定されたデッキ・問題が無い。"""


@dataclass(slots=True)
class _Loaded:
    title: str
    deck: Deck
    store: DeckScopedStore
    quizmaster: Quizmaster
    lock: threading.Lock


def _rate(rate: Rate) -> dict[str, Any]:
    return {"correct": rate.correct, "answered": rate.answered, "ratio": rate.ratio}


def _pre_post(pair: PrePost) -> dict[str, Any]:
    return {"pre": _rate(pair.pre), "post": _rate(pair.post), "delta": pair.delta}


class QuizApi:
    """`root`(作業ルート)の `data/` のデッキを出題し、回答を学習記録へ書く。"""

    def __init__(self, root: Path, clock: Clock) -> None:
        self.root = root
        self._clock = clock
        self._loaded: dict[DeckId, _Loaded] = {}
        self._lock = threading.Lock()

    @property
    def data_root(self) -> Path:
        return self.root / "data"

    def decks(self) -> list[dict[str, Any]]:
        return [
            {"deck_id": manifest.deck_id, "title": manifest.title, "language": manifest.language.value}
            for manifest in discover_decks(self.data_root)
        ]

    def deck(self, deck_id: str) -> dict[str, Any]:
        loaded = self._load(deck_id)
        return {
            "deck_id": loaded.deck.deck_id,
            "title": loaded.title,
            "question_ids": [question.question_id.local_id for question in loaded.deck.questions],
        }

    def start_session(self, deck_id: str) -> dict[str, Any]:
        loaded = self._load(deck_id)
        local_id = f"{self._clock.now().strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
        return {"session_id": SessionId(loaded.deck.deck_id, local_id).local_id}

    def sessions(self, deck_id: str) -> list[dict[str, Any]]:
        """回答のあるセッションを、最初の回答の記録順に返す。"""
        loaded = self._load(deck_id)
        counts: dict[str, int] = {}
        for attempt in loaded.store.attempts():
            counts[attempt.session_id.local_id] = counts.get(attempt.session_id.local_id, 0) + 1
        return [{"session_id": session, "answered": answered} for session, answered in counts.items()]

    def session(self, deck_id: str, session_id: str) -> dict[str, Any]:
        loaded = self._load(deck_id)
        session = self._session_id(loaded, session_id)
        answered = [attempt.question_id.local_id for attempt in loaded.store.attempts(session)]
        return {
            "session_id": session.local_id,
            "question_ids": [question.question_id.local_id for question in loaded.deck.questions],
            "answered": answered,
        }

    def present(self, deck_id: str, session_id: str, question_id: str) -> dict[str, Any]:
        """問題を出題する。選択肢は `presentation` の順に、ID と本文の組で返す。"""
        loaded = self._load(deck_id)
        session = self._session_id(loaded, session_id)
        question = self._question(loaded, question_id)
        with loaded.lock:
            presented = loaded.quizmaster.present(session, question.question_id)
        texts = {option.option_id: option.text for option in question.options}
        return {
            "question_id": question.question_id.local_id,
            "stem": question.stem,
            "options": [
                {"option_id": option_id.local_id, "text": texts[option_id]} for option_id in presented.presentation
            ],
        }

    def submit(self, deck_id: str, session_id: str, question_id: str, chosen: object) -> dict[str, Any]:
        """回答を採点して学習記録へ追記する。`chosen` は選択肢 ID の文字列だけを受け取る。"""
        if not isinstance(chosen, str):
            raise ValueError(f"chosen は選択肢 ID の文字列で与える: {type(chosen).__name__}")
        loaded = self._load(deck_id)
        session = self._session_id(loaded, session_id)
        question = self._question(loaded, question_id)
        if question.answer_count != 1:
            # 応答の形(`correct_option_id` が1つ)は正解1件の問題にしか答えられない。複数正解は T-065 で受ける。
            raise ValueError("正解が複数ある問題の回答はまだ受け付けない")
        (correct,) = question.correct_option_ids
        with loaded.lock:
            # 再起動で出題状態が消えても、同じセッションで同じ問題に2度答えた記録は作らない。
            if any(a.question_id == question.question_id for a in loaded.store.attempts(session)):
                raise ValueError("このセッションで回答済みの問題")
            attempt = loaded.quizmaster.submit(session, question.question_id, OptionId(question.question_id, chosen))
        return {
            "correct": attempt.correct,
            "correct_option_id": correct.local_id,
            "explanation": question.explanation,
        }

    def report(self, deck_id: str, pre: str, post: str) -> dict[str, Any]:
        loaded = self._load(deck_id)
        pre_session = self._session_id(loaded, pre)
        post_session = self._session_id(loaded, post)
        report = build_report(loaded.deck, loaded.store.attempts(), pre_session, post_session)
        return {
            "deck_id": report.deck_id,
            "pre_session": report.pre_session.local_id,
            "post_session": report.post_session.local_id,
            "overall": _pre_post(report.overall),
            "by_studied": {flag.value: _pre_post(report.by_studied[flag]) for flag in StudiedFlag},
            "limits": report.limits,
            "markdown": render_report(report),
        }

    def _load(self, deck_id: str) -> _Loaded:
        with self._lock:
            loaded = self._loaded.get(DeckId(deck_id))
            if loaded is not None:
                return loaded
            known = {manifest.deck_id for manifest in discover_decks(self.data_root)}
            if deck_id not in known:
                raise NotFoundError(f"デッキが無い: {deck_id}")
            manifest = load_deck_manifest(self.data_root, DeckId(deck_id))
            deck = ingest_deck(self.data_root, manifest.deck_id).deck
            store = DeckScopedStore(deck.deck_id, self.root)
            loaded = _Loaded(manifest.title, deck, store, Quizmaster(deck, store, self._clock), threading.Lock())
            self._loaded[deck.deck_id] = loaded
            return loaded

    @staticmethod
    def _session_id(loaded: _Loaded, local_id: str) -> SessionId:
        if not LOCAL_ID_PATTERN.match(local_id):
            raise ValueError(f"セッション ID の形が規定外: {local_id!r}")
        return SessionId(loaded.deck.deck_id, local_id)

    @staticmethod
    def _question(loaded: _Loaded, local_id: str) -> Question:
        question_id = QuestionId(loaded.deck.deck_id, local_id)
        for question in loaded.deck.questions:
            if question.question_id == question_id:
                return question
        raise NotFoundError(f"このデッキに無い問題: {local_id}")
