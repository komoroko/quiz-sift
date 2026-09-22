"""認証情報のログ遮断(C-015 / NFR-3 / AR-28)。値はすべて合成のダミー。"""

import io
import json
import logging
from collections.abc import Iterator

import pytest

from quiz_sift.safety.secret_mask import SECRET_MASK, SecretMaskFilter, mask_secrets

API_KEY = "sk-ant-api03-SyntheticDummyKey_0123456789-abcdefXYZ"
SESSION = "SyntheticSession0123456789abcdef"
ACCESS = "SyntheticAccessTokenValue987"
CSRF = "SyntheticCsrf42"
PASSWORD = "SyntheticPassw0rd!"
# 名前が認証情報らしくない cookie。Cookie / Set-Cookie ヘッダとして伏せなければ漏れる。
UD_USER = "SyntheticUdCacheUser77"
JWT = "SyntheticJwtHeader.SyntheticJwtBody.Sig"
# 値の中の引用符で止まると後半が漏れる。repr は反対側の引用符で囲み、JSON はバックスラッシュで逃がす。
QUOTED_PASSWORD_TAIL = "PassTailQ1"
QUOTED_COOKIE_TAIL = "CookieTailQ2"
SECRETS = (API_KEY, SESSION, ACCESS, CSRF, PASSWORD, UD_USER, JWT, QUOTED_PASSWORD_TAIL, QUOTED_COOKIE_TAIL)


@pytest.fixture
def captured() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretMaskFilter())
    parent = logging.getLogger("quiz_sift_test_secret")
    parent.addHandler(handler)
    parent.setLevel(logging.DEBUG)
    yield logging.getLogger("quiz_sift_test_secret.child"), stream
    parent.removeHandler(handler)


def test_credentials_and_cookies_are_masked(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    logger.info("key=%s", API_KEY)
    logger.info("ANTHROPIC_API_KEY=%s", API_KEY)
    logger.info("headers %s", {"x-api-key": API_KEY, "Authorization": f"Bearer {ACCESS}"})
    logger.info("Authorization: Bearer %s", ACCESS)
    logger.info("Cookie: ud_cache_user=%s; sessionid=%s; access_token=%s; csrftoken=%s", UD_USER, SESSION, ACCESS, CSRF)
    logger.info("Set-Cookie: ud_cache_user=%s; Path=/; HttpOnly", UD_USER)
    logger.info("Set-Cookie: dj_session_id=%s; Path=/; HttpOnly", SESSION)
    logger.info("response headers\nset-cookie: access_token=%s; Secure\nContent-Type: text/html", ACCESS)
    logger.info("request %s", {"Cookie": f"ud_cache_user={UD_USER}; sessionid={SESSION}"})
    # CDP の Network.getCookies が返す cookie オブジェクトは名前と値が別の項目に分かれる。
    logger.info("cookies %s", [{"name": "sessionid", "value": SESSION, "domain": ".udemy.com"}])
    logger.info('cookies [{"name": "access_token", "value": "%s"}]', ACCESS)
    logger.info("cookies %s", [{"name": "ud_cache_user", "value": UD_USER}, {"name": "ud_user_jwt", "value": JWT}])
    logger.info("ud_user_jwt=%s; Path=/", JWT)
    logger.info("login password=%s", PASSWORD)
    # %r と f"{x=}" は値を引用符で囲む: sessionid='…'
    logger.info("sessionid=%r", SESSION)
    sessionid, token = SESSION, ACCESS
    logger.info(f"auth failed {sessionid=} {token=}")
    logger.info('sessionid: %r / password: "%s"', SESSION, PASSWORD)
    logger.info("csrftoken=%r; next", "Synthetic'Quoted" + CSRF)
    logger.info("form %s", {"password": PASSWORD})
    logger.info("form %s", {"password": "Synth'" + QUOTED_PASSWORD_TAIL})
    logger.info("form %s", json.dumps({"password": 'Synth"' + QUOTED_PASSWORD_TAIL}))
    logger.info("cookies %s", [{"name": "ud_cache_user", "value": "Synth'" + QUOTED_COOKIE_TAIL}])
    logger.info("cookies %s", json.dumps([{"name": "ud_cache_user", "value": 'Synth"' + QUOTED_COOKIE_TAIL}]))
    try:
        raise RuntimeError(f"auth failed with sessionid={SESSION} {token=}")
    except RuntimeError:
        logger.exception("CDP 取得に失敗した")

    output = stream.getvalue()
    for secret in SECRETS:
        assert secret not in output, secret
    assert SECRET_MASK in output
    # 認証情報でない部分は残る。
    assert "Content-Type: text/html" in output
    assert ".udemy.com" in output
    assert "CDP 取得に失敗した" in output


def test_non_secret_text_is_left_alone() -> None:
    text = "処理した問題数 3 / deck=deckA / model=claude-sonnet-5"
    assert mask_secrets(text) == text


def test_masking_is_idempotent() -> None:
    once = mask_secrets(f"Cookie: sessionid={SESSION}\nsessionid={SESSION}")
    assert mask_secrets(once) == once
