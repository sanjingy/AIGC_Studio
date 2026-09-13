"""本机运行时的四个端点。

```
POST /local-runtime/poll                       桥接令牌，取一条请求
POST /local-runtime/heartbeat                  桥接令牌，忙碌时续心跳 + 问"还要不要"
POST /local-runtime/requests/{id}/result       桥接令牌，回一条结果
GET  /local-runtime/status                     正常登录态，看本 org 配没配
```

**桥接令牌只开前三条路径。** 它不是登录态、换不出登录态、也访问不了任何
项目接口——那三条路径上根本没有 `CurrentUser` 依赖，令牌进不了别的地方。
反过来，`status` 用的是正常登录态，桥接令牌在那里同样不被接受。
"""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from apps.api.core.config import get_settings
from apps.api.core.errors import AppError
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.local_runtime import service
from apps.api.modules.local_runtime.schemas import (
    HeartbeatIn,
    HeartbeatOut,
    ImageJobOut,
    PollIn,
    PollOut,
    ResultIn,
    StatusOut,
    TextJobOut,
)

router = APIRouter(prefix="/local-runtime", tags=["local-runtime"])


async def require_bridge_token(request: Request) -> None:
    """桥接鉴权。

    关着的时候返 404 而不是 401：那样连"这个部署有没有开试点"都不会泄露。
    比对用 `compare_digest`，逐字符短路比较会把令牌的前缀泄露给计时攻击。
    """
    settings = get_settings()
    if not settings.local_cli_enabled:
        raise AppError("common.not_found", message="local runtime is disabled")

    auth = request.headers.get("Authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise AppError("auth.token.invalid", message="missing bridge token")

    expected = settings.local_cli_token.get_secret_value()
    # 先编码再比：compare_digest 对非 ASCII 的 str 会抛 TypeError，
    # 那会把一次鉴权失败变成 500。
    if not hmac.compare_digest(value.encode("utf-8"), expected.encode("utf-8")):
        # 日志里不写令牌，一个字符都不写。
        raise AppError("auth.token.invalid", message="bridge token rejected")


BridgeAuth = Annotated[None, Depends(require_bridge_token)]


@router.post("/poll", response_model=PollOut, status_code=status.HTTP_200_OK)
async def poll(payload: PollIn, _auth: BridgeAuth) -> PollOut | Response:
    """短轮询取单。没活返 204，连接器等约一秒再来。

    服务端不做长阻塞：一个挂起的请求会占住一个 worker，而这条路径的空闲期
    远长于忙碌期。
    """
    leased = await service.take_request(
        provider=payload.provider,
        kinds=list(payload.kinds),
        version=payload.version,
    )
    if leased is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return PollOut(
        request_id=leased.request_id,
        lease_token=leased.lease_token,
        kind=leased.kind,
        timeout_seconds=leased.timeout_seconds,
        text=(
            TextJobOut(
                system=leased.text.system,
                user=leased.text.user,
                schema_name=leased.text.schema_name,
                max_output_tokens=leased.text.max_output_tokens,
            )
            if leased.text is not None
            else None
        ),
        image=(
            ImageJobOut(
                prompt=leased.image.prompt,
                negative_prompt=leased.image.negative_prompt,
                size=leased.image.size,
                n=leased.image.n,
            )
            if leased.image is not None
            else None
        ),
    )


@router.post("/heartbeat", response_model=HeartbeatOut)
async def heartbeat(payload: HeartbeatIn, _auth: BridgeAuth) -> HeartbeatOut:
    """忙碌心跳。顺便回答"我手上这条还有人等吗"。

    回答 False 时连接器必须立刻停手：那意味着用户已经取消、或者等待方
    早就超时了，继续跑下去只是白烧他自己的订阅额度。
    """
    outstanding = await service.heartbeat(
        provider=payload.provider,
        kinds=list(payload.kinds),
        version=payload.version,
        request_id=payload.request_id,
    )
    return HeartbeatOut(outstanding=outstanding)


@router.post(
    "/requests/{request_id}/result",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def submit_result(request_id: uuid.UUID, payload: ResultIn, _auth: BridgeAuth) -> None:
    """回一条结果。一次性：过期、被取消、重放都拿不到第二次机会（409）。"""
    await service.submit_result(
        request_id=request_id,
        lease_token=payload.lease_token,
        text=payload.text,
        image_base64=payload.image_base64,
        image_mime=payload.image_mime,
        revised_prompt=payload.revised_prompt,
        model_id=payload.model_id,
        tokens_in=payload.tokens_in,
        tokens_out=payload.tokens_out,
        error_code=payload.error_code,
    )


@router.get("/status", response_model=StatusOut)
async def read_status(user: CurrentUser, db: DbSession) -> StatusOut:
    """本 org 配没配本机运行时。**不回令牌、不回路径、不回别人的 id。**"""
    return await service.describe_status(db, org_id=user.org_id)
