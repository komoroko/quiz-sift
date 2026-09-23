"""ローカル API を tmp_path の作業ルートに立てる共通部品(R-12)。

問題本文は架空のもの(NFR-2 の対象外)。サーバは空きポートで別スレッドに立て、テストの終わりに止める。
"""

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPResponse
from pathlib import Path
from typing import Any

import pytest

from quiz_sift.ports.clock import SystemClock
from quiz_sift.server.handler import QuizServer, make_server

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = REPO_ROOT / "web"
SERVER_SRC = REPO_ROOT / "src" / "quiz_sift" / "server"

DECK = "fixture-deck"
QUESTIONS = {"q1": "A", "q2": "B", "q3": "C", "q4": "D"}


def write_root(root: Path, gitignore: str = "data/\nout/\n") -> Path:
    """`.gitignore` とデッキ1つを持つ作業ルートを組み立てる。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    deck_dir = root / "data" / DECK
    deck_dir.mkdir(parents=True)
    (deck_dir / "deck.yaml").write_text(f"deck_id: {DECK}\ntitle: 架空デッキ\nlanguage: ja\n", encoding="utf-8")
    rows = [
        {
            "id": qid,
            "stem": f"架空の問題 {qid}",
            "options": [{"id": label, "text": f"選択肢 {label}"} for label in "ABCD"],
            "answer": answer,
            "explanation": f"解説 {qid}",
        }
        for qid, answer in QUESTIONS.items()
    ]
    (deck_dir / "q.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")
    return root


@dataclass
class Client:
    """実ソケット越しに API を叩く最小のクライアント。1リクエスト1接続。"""

    port: int

    def request(
        self, method: str, path: str, body: object = None, headers: dict[str, str] | None = None
    ) -> tuple[HTTPResponse, bytes]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            data = None if body is None else json.dumps(body).encode("utf-8")
            sent = dict(headers or {})
            if data is not None:
                sent.setdefault("Content-Type", "application/json")
            conn.request(method, path, body=data, headers=sent)
            response = conn.getresponse()
            return response, response.read()
        finally:
            conn.close()

    def json(self, method: str, path: str, body: object = None, expect: int = 200) -> Any:
        response, data = self.request(method, path, body)
        assert response.status == expect, (response.status, data)
        return json.loads(data)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return write_root(tmp_path)


@pytest.fixture
def server(root: Path) -> Iterator[QuizServer]:
    srv = make_server(root, port=0, clock=SystemClock())
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


@pytest.fixture
def client(server: QuizServer) -> Client:
    return Client(server.server_port)
