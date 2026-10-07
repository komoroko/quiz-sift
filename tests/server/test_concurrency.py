"""A-2: `ThreadingHTTPServer` で、SQLite の接続はリクエストごとに開いて閉じる(AR-15)。"""

import json
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path

from quiz_sift.server.handler import QuizServer

from .conftest import DECK, QUESTIONS, Client

SESSIONS = 8


def test_concurrent_requests_do_not_raise(server: QuizServer, client: Client, root: Path) -> None:
    assert isinstance(server, ThreadingHTTPServer)

    sessions = [client.json("POST", f"/api/decks/{DECK}/sessions", expect=201)["session_id"] for _ in range(SESSIONS)]

    def answer_session(session: str) -> list[int]:
        base = f"/api/decks/{DECK}/sessions/{session}"
        statuses = []
        for qid, answer in QUESTIONS.items():
            statuses.append(client.request("GET", f"{base}/questions/{qid}")[0].status)
            statuses.append(
                client.request("POST", f"{base}/answers", {"question_id": qid, "chosen": [answer]})[0].status
            )
            statuses.append(client.request("GET", f"/api/decks/{DECK}/sessions")[0].status)
        return statuses

    # 別スレッドのハンドラが同じ接続を使い回せば sqlite3.ProgrammingError で応答が返らず、ここで例外になる。
    with ThreadPoolExecutor(max_workers=SESSIONS) as pool:
        results = list(pool.map(answer_session, sessions))

    assert all(status in (200, 201) for statuses in results for status in statuses), results
    listed = client.json("GET", f"/api/decks/{DECK}/sessions")
    assert sorted(item["session_id"] for item in listed) == sorted(sessions)
    assert all(item["answered"] == len(QUESTIONS) for item in listed)
    assert json.dumps(listed)  # 応答が JSON として完結している
