"""CDP アダプタのうち、ブラウザ無しで確かめられる部分(WebSocket のフレームとログイン切れの判定)。

実際の Chrome への接続は T-019 の実取得が受け持つ。
"""

import json
import socket
import struct
import threading

import pytest

from quiz_sift.adapters.browser_cdp import CdpError, _is_login_page, recv_message, send_frame


@pytest.mark.parametrize("size", [5, 200, 70_000])
def test_masked_frames_round_trip(size: int) -> None:
    client, server = socket.socketpair()
    with client, server:
        text = json.dumps({"id": 1, "result": {"value": "あ" * size}})
        # 大きいフレームはソケットのバッファに収まらないので、送信を別スレッドにして詰まりを避ける。
        sender = threading.Thread(target=send_frame, args=(client, text.encode("utf-8")))
        sender.start()
        assert recv_message(server) == text
        sender.join()


def test_fragmented_message_and_ping_from_server() -> None:
    client, server = socket.socketpair()
    with client, server:
        # サーバからのフレームはマスクされない。ping を挟んだ2分割のテキストメッセージ。
        server.sendall(bytes([0x01, 3]) + b'{"a')
        server.sendall(bytes([0x89, 2]) + b"hi")
        server.sendall(bytes([0x80, 5]) + b'": 1}')
        assert recv_message(client) == '{"a": 1}'
        pong = server.recv(64)
        assert pong[0] == 0x8A
        mask = pong[2:6]
        assert bytes(b ^ mask[i % 4] for i, b in enumerate(pong[6:])) == b"hi"


def test_close_and_disconnect_raise() -> None:
    client, server = socket.socketpair()
    with client, server:
        server.sendall(bytes([0x88, 0]))
        with pytest.raises(CdpError):
            recv_message(client)
    client, server = socket.socketpair()
    with client:
        server.sendall(bytes([0x81]) + struct.pack("!B", 10) + b"abc")
        server.close()
        with pytest.raises(CdpError):
            recv_message(client)


def test_login_redirect_is_detected() -> None:
    assert _is_login_page("https://www.udemy.com/join/login-popup/?next=/course/x/")
    assert not _is_login_page("https://www.udemy.com/course/x/learn/quiz/1/result/2")
