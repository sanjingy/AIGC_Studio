"""注册 / 登录 / 刷新 / 登出 的端到端行为。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from apps.api.modules.auth.deps import ACCESS_COOKIE, REFRESH_COOKIE
from tests.conftest import unique_email

pytestmark = pytest.mark.integration

BASE = "/api/v1/auth"


async def test_register_creates_session_and_org(
    client: AsyncClient, credentials: dict[str, str]
) -> None:
    r = await client.post(f"{BASE}/register", json=credentials)
    assert r.status_code == 201, r.text

    body = r.json()
    assert body["user"]["email"] == credentials["email"]
    assert body["user"]["role"] == "owner"
    # 个人用户也要有 org，不做特例
    assert body["user"]["org_id"]

    assert ACCESS_COOKIE in r.cookies
    assert REFRESH_COOKIE in r.cookies


async def test_tokens_never_appear_in_response_body(
    client: AsyncClient, credentials: dict[str, str]
) -> None:
    """令牌只走 httpOnly Cookie。出现在响应体里就等于交给了 XSS。"""
    r = await client.post(f"{BASE}/register", json=credentials)
    raw = r.text
    assert r.cookies[ACCESS_COOKIE] not in raw
    assert r.cookies[REFRESH_COOKIE] not in raw
    assert "access_token" not in raw
    assert "refresh_token" not in raw


async def test_cookies_are_httponly(client: AsyncClient, credentials: dict[str, str]) -> None:
    r = await client.post(f"{BASE}/register", json=credentials)
    cookie_headers = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie"]
    assert len(cookie_headers) == 2
    for header in cookie_headers:
        assert "HttpOnly" in header, header
        assert "SameSite=lax" in header.lower() or "samesite=lax" in header.lower()


async def test_duplicate_email_rejected(client: AsyncClient, credentials: dict[str, str]) -> None:
    assert (await client.post(f"{BASE}/register", json=credentials)).status_code == 201
    r = await client.post(f"{BASE}/register", json=credentials)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "auth.email.taken"


async def test_login_and_me(client: AsyncClient, credentials: dict[str, str]) -> None:
    await client.post(f"{BASE}/register", json=credentials)
    client.cookies.clear()

    r = await client.post(
        f"{BASE}/login",
        json={"email": credentials["email"], "password": credentials["password"]},
    )
    assert r.status_code == 200

    me = await client.get(f"{BASE}/me")
    assert me.status_code == 200
    assert me.json()["email"] == credentials["email"]


async def test_wrong_password_rejected(client: AsyncClient, credentials: dict[str, str]) -> None:
    await client.post(f"{BASE}/register", json=credentials)
    r = await client.post(
        f"{BASE}/login", json={"email": credentials["email"], "password": "wrong-pass-9"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "auth.credentials.invalid"


async def test_unknown_email_gives_same_error_as_wrong_password(
    client: AsyncClient, credentials: dict[str, str]
) -> None:
    """错误码必须一致，否则可以用来枚举哪些邮箱注册过。"""
    await client.post(f"{BASE}/register", json=credentials)

    wrong_pw = await client.post(
        f"{BASE}/login", json={"email": credentials["email"], "password": "wrong-pass-9"}
    )
    unknown = await client.post(
        f"{BASE}/login", json={"email": unique_email(), "password": "wrong-pass-9"}
    )

    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert wrong_pw.json()["error"]["user_message"] == unknown.json()["error"]["user_message"]


async def test_me_requires_auth(client: AsyncClient) -> None:
    r = await client.get(f"{BASE}/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "auth.token.invalid"


async def test_refresh_rotates_token(client: AsyncClient, credentials: dict[str, str]) -> None:
    await client.post(f"{BASE}/register", json=credentials)
    old_refresh = client.cookies[REFRESH_COOKIE]

    r = await client.post(f"{BASE}/refresh")
    assert r.status_code == 200
    assert client.cookies[REFRESH_COOKIE] != old_refresh, "刷新必须轮换令牌"

    assert (await client.get(f"{BASE}/me")).status_code == 200


async def test_logout_invalidates_refresh(client: AsyncClient, credentials: dict[str, str]) -> None:
    await client.post(f"{BASE}/register", json=credentials)
    stale = client.cookies[REFRESH_COOKIE]

    assert (await client.post(f"{BASE}/logout")).status_code == 204

    # 即使客户端把令牌留着，服务端也必须拒绝
    client.cookies.set(REFRESH_COOKIE, stale)
    assert (await client.post(f"{BASE}/refresh")).status_code == 401


@pytest.mark.parametrize(
    "password",
    ["short1", "12345678", "abcdefgh"],
    ids=["too-short", "digits-only", "letters-only"],
)
async def test_weak_passwords_rejected(client: AsyncClient, password: str) -> None:
    r = await client.post(
        f"{BASE}/register",
        json={"email": unique_email(), "password": password, "display_name": "x"},
    )
    assert r.status_code == 422


async def test_email_is_normalized(client: AsyncClient) -> None:
    """大小写和空格不该产生两个账号。"""
    email = unique_email()
    await client.post(
        f"{BASE}/register",
        json={"email": f"  {email.upper()}  ", "password": "correct-horse-9", "display_name": "x"},
    )
    client.cookies.clear()

    r = await client.post(f"{BASE}/login", json={"email": email, "password": "correct-horse-9"})
    assert r.status_code == 200
