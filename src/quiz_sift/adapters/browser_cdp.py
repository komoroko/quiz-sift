"""ログイン済みの Chrome へ CDP で接続してページの HTML を取る(ADR-007)。

人がデバッグポート付きで起動した Chrome(`--remote-debugging-port=9222`)に接続する。
パスワードもセッションもプログラムは持たない — ログインはブラウザ側にある。

このモジュールは HTML を取ってくるだけで、問題の抽出は `extract.udemy.extract`(純粋関数)が行う。
Udemy へのページ取得はアカウントにアクセス履歴を残し、戻せない(T-019 の承認が覆う)。

WebSocket クライアントは標準ライブラリだけで書いてある。依存を足すとサンドボックスイメージの
焼き直しが要るうえ、CDP に要るのはテキストフレームの送受信だけで足りる。
"""

import base64
import json
import os
import socket
import struct
import time
import urllib.request
from urllib.parse import urlsplit

from quiz_sift.extract.failures import FetchFailure, FetchFailureReason
from quiz_sift.extract.udemy import FetchedPage

DEFAULT_ENDPOINT = "http://127.0.0.1:9222"
LOGIN_PATH_MARKERS = ("/join/", "/login")
"""リダイレクト先のパスにこれが含まれていればログイン切れとみなす。"""
_POLL_INTERVAL = 0.5

_OP_CONTINUATION = 0x0
_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


class CdpError(Exception):
    """CDP との通信が途切れた、または CDP がエラーを返した。"""


def fetch(
    url: str, *, ready_selector: str, endpoint: str = DEFAULT_ENDPOINT, timeout: float = 60.0
) -> FetchedPage | FetchFailure:
    """新しいタブで `url` を開き、`ready_selector` が現れたら `outerHTML` を返す。タブは最後に閉じる。"""
    try:
        target = _http_json(f"{endpoint}/json/new", method="PUT")
        target_id, ws_url = str(target["id"]), str(target["webSocketDebuggerUrl"])
    except (OSError, ValueError, KeyError):
        return FetchFailure(url, FetchFailureReason.DISCONNECTED)
    try:
        with CdpSession(ws_url, timeout=timeout) as session:
            session.call("Page.navigate", {"url": url})
            deadline = time.monotonic() + timeout
            query = f"document.querySelector({json.dumps(ready_selector)}) !== null"
            while not session.evaluate(query):
                if _is_login_page(str(session.evaluate("location.href"))):
                    return FetchFailure(url, FetchFailureReason.AUTH_EXPIRED)
                if time.monotonic() > deadline:
                    return FetchFailure(url, FetchFailureReason.NOT_READY)
                time.sleep(_POLL_INTERVAL)
            return FetchedPage(url, str(session.evaluate("document.documentElement.outerHTML")))
    except (OSError, CdpError):
        return FetchFailure(url, FetchFailureReason.DISCONNECTED)
    finally:
        try:
            _http_json(f"{endpoint}/json/close/{target_id}")
        except (OSError, ValueError):
            pass


def _is_login_page(href: str) -> bool:
    path = urlsplit(href).path
    return any(marker in path for marker in LOGIN_PATH_MARKERS)


def _http_json(url: str, *, method: str = "GET") -> dict[str, object]:
    with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=10) as response:
        body = response.read().decode("utf-8")
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        # /json/close は JSON ではなく "Target is closing" を返す。
        return {}
    if not isinstance(value, dict):
        raise ValueError("CDP の HTTP 応答がオブジェクトではない")
    return value


class CdpSession:
    """1タブへの CDP WebSocket 接続。"""

    def __init__(self, ws_url: str, *, timeout: float) -> None:
        parts = urlsplit(ws_url)
        if parts.scheme != "ws" or parts.hostname is None:
            raise CdpError(f"ws:// の URL ではない: {parts.scheme}")
        self._sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout)
        self._next_id = 0
        self._handshake(parts.netloc, parts.path or "/")

    def __enter__(self) -> "CdpSession":
        return self

    def __exit__(self, *exc: object) -> None:
        self._sock.close()

    def evaluate(self, expression: str) -> object:
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        inner = result.get("result")
        if not isinstance(inner, dict):
            raise CdpError("Runtime.evaluate の応答に result が無い")
        if "exceptionDetails" in result:
            raise CdpError("Runtime.evaluate が例外を返した")
        return inner.get("value")

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        """コマンドを送り、同じ id の応答を待つ。間に届くイベントは捨てる。"""
        self._next_id += 1
        request_id = self._next_id
        send_frame(self._sock, json.dumps({"id": request_id, "method": method, "params": params}).encode("utf-8"))
        while True:
            message = json.loads(recv_message(self._sock))
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                raise CdpError(f"{method} が失敗した: {message['error']}")
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise CdpError(f"{method} の応答がオブジェクトではない")
            return result

    def _handshake(self, host: str, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise CdpError("WebSocket のハンドシェイク中に切断された")
            response += chunk
        status_line = response.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line + b" ":
            raise CdpError(f"WebSocket に切り替わらなかった: {status_line.decode('latin-1')}")


def send_frame(sock: socket.socket, payload: bytes, *, opcode: int = _OP_TEXT) -> None:
    """マスク付きの単一フレームを送る(クライアントからのフレームはマスク必須。RFC 6455 5.3)。"""
    header = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header += bytes([0x80 | length])
    elif length < 1 << 16:
        header += bytes([0x80 | 126]) + struct.pack("!H", length)
    else:
        header += bytes([0x80 | 127]) + struct.pack("!Q", length)
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    sock.sendall(header + mask + masked)


def recv_message(sock: socket.socket) -> str:
    """テキストメッセージを1つ受け取る。分割フレームはつなぎ、ping には pong を返す。"""
    parts: list[bytes] = []
    while True:
        first, second = _recv_exact(sock, 2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", _recv_exact(sock, 2))
        elif length == 127:
            (length,) = struct.unpack("!Q", _recv_exact(sock, 8))
        mask = _recv_exact(sock, 4) if second & 0x80 else b""
        payload = _recv_exact(sock, length)
        if mask:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        if opcode == _OP_CLOSE:
            raise CdpError("CDP が接続を閉じた")
        if opcode == _OP_PING:
            send_frame(sock, payload, opcode=_OP_PONG)
            continue
        if opcode not in (_OP_TEXT, _OP_CONTINUATION):
            continue
        parts.append(payload)
        if first & 0x80:
            return b"".join(parts).decode("utf-8")


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    buffer = bytearray()
    while len(buffer) < size:
        chunk = sock.recv(size - len(buffer))
        if not chunk:
            raise CdpError("CDP の接続が切れた")
        buffer += chunk
    return bytes(buffer)
