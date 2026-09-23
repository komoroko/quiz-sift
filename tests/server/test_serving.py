"""A-4: `out/` の配信は読み取り専用の `GET /files/...` 1本で、`gitignore_guard` の対象配下だけを配信する。"""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from quiz_sift.ports.clock import SystemClock
from quiz_sift.server.handler import make_server
from quiz_sift.store.location import store_path

from .conftest import DECK, Client, write_root


@pytest.fixture
def guarded(tmp_path: Path) -> Iterator[tuple[Client, Path]]:
    # out/ 配下でも追跡対象に戻したファイル(out/tracked.txt)は配信しない。
    root = write_root(tmp_path / "root", gitignore="data/\nout/*\n!out/tracked.txt\n")
    out = root / "out"
    (out / DECK).mkdir(parents=True)
    (out / DECK / "report.md").write_text("# 架空レポート\n", encoding="utf-8")
    (out / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret\n", encoding="utf-8")
    (out / "escape.txt").symlink_to(tmp_path / "secret.txt")
    (out / "data-link").symlink_to(root / "data", target_is_directory=True)
    store_path(root).write_bytes(b"SQLite format 3\x00")
    srv = make_server(root, port=0, clock=SystemClock())
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(srv.server_port), root
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def test_only_guarded_paths_are_served(guarded: tuple[Client, Path]) -> None:
    client, _ = guarded

    response, body = client.request("GET", f"/files/{DECK}/report.md")
    assert response.status == 200
    assert body.decode("utf-8") == "# 架空レポート\n"

    refused = [
        "/files/tracked.txt",  # out/ 配下だが .gitignore の対象ではない
        "/files/escape.txt",  # シンボリックリンクで out/ の外へ出る
        f"/files/data-link/{DECK}/deck.yaml",  # ディレクトリのリンクで data/ へ出る
        "/files/../.gitignore",
        "/files/%2e%2e/.gitignore",
        "/files/..%2f.gitignore",
        f"/files/%2e%2e/data/{DECK}/deck.yaml",
        "/files/learning.sqlite3",  # 学習記録は API を迂回して読ませない
        "/files/learning.sqlite3-wal",
        f"/files/{DECK}",  # ディレクトリ
        "/files/no-such.md",
        f"/data/{DECK}/deck.yaml",  # 配信口は /files/ の1本だけ
        f"/out/{DECK}/report.md",
    ]
    for path in refused:
        response, body = client.request("GET", path)
        assert response.status == 404, path
        assert b"secret" not in body and b"tracked" not in body and b"deck_id" not in body, path

    # 読み取り専用: 書き込み系のメソッドは受け付けない。
    response, _ = client.request("POST", f"/files/{DECK}/report.md", {"x": 1})
    assert response.status == 405
    for method in ("PUT", "DELETE"):
        assert client.request(method, f"/files/{DECK}/report.md")[0].status == 501
