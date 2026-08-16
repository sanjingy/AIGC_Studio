"""校验错误响应。

回归用例：自定义校验器抛 ValueError 时，pydantic 会把异常对象放进 errors()
的 ctx 里。若直接序列化整个 errors()，422 会变成 500 —— 上线后表现为
"密码太弱" 提示不出来，只给一个服务器错误。
"""

from __future__ import annotations

from httpx import AsyncClient

BASE = "/api/v1/auth"


async def test_custom_validator_returns_422_not_500(client: AsyncClient) -> None:
    r = await client.post(
        f"{BASE}/register",
        json={"email": "a@example.com", "password": "12345678", "display_name": "x"},
    )
    assert r.status_code == 422, f"应为 422，实际 {r.status_code}"


async def test_validation_payload_is_serializable_and_useful(client: AsyncClient) -> None:
    r = await client.post(
        f"{BASE}/register",
        json={"email": "not-an-email", "password": "abcdefgh", "display_name": ""},
    )
    assert r.status_code == 422

    body = r.json()["error"]
    assert body["code"] == "common.validation_failed"
    errors = body["detail"]["errors"]

    # 每条都要能定位到字段
    fields = {e["field"] for e in errors}
    assert "email" in fields
    assert all(isinstance(e["message"], str) and e["message"] for e in errors)
    assert all({"field", "message", "type"} == set(e) for e in errors)


async def test_missing_body_is_422(client: AsyncClient) -> None:
    r = await client.post(f"{BASE}/register", json={})
    assert r.status_code == 422
