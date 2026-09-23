"""A-3: UI は Next.js の rewrites で同一オリジンから呼び、API は CORS の許可オリジンを持たない。"""

import re

from .conftest import DECK, SERVER_SRC, WEB_ROOT, Client

CORS_HEADER = re.compile(r"access-control-", re.IGNORECASE)


def test_no_cors_allowlist_in_api(client: Client) -> None:
    # API のソースに CORS ヘッダを書く経路が無い。
    for path in SERVER_SRC.glob("*.py"):
        assert CORS_HEADER.search(path.read_text(encoding="utf-8")) is None, path

    # 別オリジンからのリクエストにも許可ヘッダを返さず、preflight(OPTIONS)は実装しない。
    cross = {"Origin": "http://evil.example"}
    response, _ = client.request("GET", "/api/decks", headers=cross)
    assert response.status == 200
    assert not any(CORS_HEADER.match(name) for name, _ in response.getheaders())
    preflight = {**cross, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    response, _ = client.request("OPTIONS", f"/api/decks/{DECK}/sessions", headers=preflight)
    assert response.status == 501
    assert not any(CORS_HEADER.match(name) for name, _ in response.getheaders())

    # UI 側は rewrites で /api と /files を API へ転送し、呼び出しは同一オリジンの相対パスだけ。
    config = (WEB_ROOT / "next.config.mjs").read_text(encoding="utf-8")
    assert re.search(r'source:\s*"/api/:path\*"', config)
    assert re.search(r'source:\s*"/files/:path\*"', config)
    client_code = (WEB_ROOT / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "http://" not in client_code and "https://" not in client_code
    assert "fetch(path" in client_code


def test_rewrites_default_port_matches_api() -> None:
    from quiz_sift.server.handler import DEFAULT_PORT

    config = (WEB_ROOT / "next.config.mjs").read_text(encoding="utf-8")
    assert f'"http://127.0.0.1:{DEFAULT_PORT}"' in config
