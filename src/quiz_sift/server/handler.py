"""HTTP の口(R-12 / ADR-006)。

- **スレッドと接続**: `ThreadingHTTPServer` でリクエストごとにスレッドを立てる。SQLite の接続は
  `DeckScopedStore` が操作ごとに開いて閉じ、サーバもハンドラも接続を持たない(AR-15)。
- **オリジン**: UI は Next.js の `rewrites` で同一オリジンから呼ぶ。CORS ヘッダは付けず、`OPTIONS`
  も実装しない(許可オリジンを持たない)。`POST` は `application/json` だけを受け取るので、別オリジンの
  ページが preflight 無しで送れる形の書き込みは通らない。
- **成果物の配信**: `GET /files/<out/ からの相対パス>` の1本だけ(`files.py`)。
- 待ち受けは `127.0.0.1` だけ。外部公開はしない。
"""

import json
import mimetypes
import shutil
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from quiz_sift.ports.clock import Clock, SystemClock
from quiz_sift.server.api import NotFoundError, QuizApi
from quiz_sift.server.files import NotServableError, resolve_artifact

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
"""API の既定ポート。`web/next.config.mjs` の rewrites の既定値と同じ値にしておく。"""

MAX_BODY_BYTES = 64 * 1024

FILES_PREFIX = "/files/"


class QuizServer(ThreadingHTTPServer):
    """`QuizApi` を1つ持つサーバ。ハンドラスレッドはデーモンにして、停止を待たせない。"""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], api: QuizApi) -> None:
        super().__init__(address, QuizHandler)
        self.api = api


class BadRequestError(ValueError):
    """リクエストの形が規定外。"""


class QuizHandler(BaseHTTPRequestHandler):
    server: QuizServer

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path.startswith(FILES_PREFIX):
            self._serve_file(unquote(url.path.removeprefix(FILES_PREFIX)))
            return
        query = {key: values[-1] for key, values in parse_qs(url.query).items()}
        api = self.server.api
        match _segments(url.path):
            case ["api", "decks"]:
                self._respond(lambda: api.decks())
            case ["api", "decks", deck]:
                self._respond(lambda: api.deck(deck))
            case ["api", "decks", deck, "sessions"]:
                self._respond(lambda: api.sessions(deck))
            case ["api", "decks", deck, "sessions", session]:
                self._respond(lambda: api.session(deck, session))
            case ["api", "decks", deck, "sessions", session, "questions", question]:
                self._respond(lambda: api.present(deck, session, question))
            case ["api", "decks", deck, "report"]:
                self._respond(lambda: api.report(deck, _require(query, "pre"), _require(query, "post")))
            case _:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        api = self.server.api
        match _segments(url.path):
            case ["api", "decks", deck, "sessions"]:
                self._respond(lambda: api.start_session(deck), status=HTTPStatus.CREATED)
            case ["api", "decks", deck, "sessions", session, "answers"]:

                def submit() -> dict[str, Any]:
                    body = self._json_body()
                    return api.submit(deck, session, _require(body, "question_id"), body.get("chosen"))

                self._respond(submit, status=HTTPStatus.CREATED)
            case _ if url.path.startswith(FILES_PREFIX):
                self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read only"}, allow="GET")
            case _:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _respond(self, action: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        try:
            payload = action()
        except NotFoundError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        else:
            self._send_json(status, payload)

    def _json_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip()
        if content_type != "application/json":
            raise BadRequestError(f"Content-Type は application/json に限る: {content_type!r}")
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY_BYTES:
            raise BadRequestError(f"本文の長さが規定外: {length}")
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequestError(f"JSON として読めない: {exc}") from exc
        if not isinstance(body, dict):
            raise BadRequestError("本文は JSON オブジェクトでなければならない")
        return body

    def _serve_file(self, relative: str) -> None:
        try:
            path = resolve_artifact(self.server.api.root, relative)
        except NotServableError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as source:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            shutil.copyfileobj(source, self.wfile)

    def _send_json(self, status: HTTPStatus, payload: object, allow: str | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if allow is not None:
            self.send_header("Allow", allow)
        self.end_headers()
        self.wfile.write(data)


def _segments(path: str) -> list[str]:
    return [unquote(part) for part in path.split("/") if part]


def _require(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise BadRequestError(f"{key} は空でない文字列でなければならない")
    return value


def make_server(root: Path, port: int = DEFAULT_PORT, clock: Clock | None = None) -> QuizServer:
    """`root`(作業ルート)の `data/` と学習記録を扱うサーバを `127.0.0.1:port` に作る。"""
    return QuizServer((HOST, port), QuizApi(root, clock or SystemClock()))
