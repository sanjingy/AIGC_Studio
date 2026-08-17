"""阿里百炼（DashScope）文生图 Provider。

接口形态由实测确认（2026-08-17），不是照文档写的——
官方文档同时存在两代接口，wan2.6 那代走工作区域名和 chat 式响应，
经典接口走 `dashscope.aliyuncs.com` 且结果在 `output.results[].url`。
这里用的是后者，已实测跑通（wan2.2-t2i-flash，约 9 秒出图）。

异步两段式：提交拿 task_id → 轮询直到终态。
"""

from __future__ import annotations

import asyncio

import httpx

from adapters.providers.base import ImageRequest, ImageResult
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://dashscope.aliyuncs.com"
SUBMIT_PATH = "/api/v1/services/aigc/text2image/image-synthesis"

_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"})
_POLL_INTERVAL_SECONDS = 2.0
_POLL_TIMEOUT_SECONDS = 300


class DashScopeImageProvider:
    provider_id = "provider.dashscope"

    def __init__(self, *, api_key: str, model_id: str = "wan2.2-t2i-flash") -> None:
        self._api_key = api_key
        self.model_id = model_id

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def generate_image(self, request: ImageRequest) -> ImageResult:
        parameters: dict[str, object] = {
            "size": request.size,
            "n": request.n,
            # 关掉提示词扩写。默认开启时上游会把我们的提示词重写成
            # 一整段摄影术语——实测把 "a single red maple leaf" 扩成了
            # 带景深、布光、色调的完整描述。那会直接覆盖掉
            # 17_ConsistencyEngine.md §4 要求的系统级风格锁定，
            # 导致每一镜的画风由上游随机决定。
            "prompt_extend": False,
            # 我们自己在渲染管线里打 AIGC 标识（18_Compliance.md），
            # 不用上游的水印，否则位置样式不可控且会叠加两层
            "watermark": False,
        }
        if request.seed is not None:
            parameters["seed"] = request.seed

        body: dict[str, object] = {
            "model": self.model_id,
            "input": {"prompt": request.prompt},
            "parameters": parameters,
        }
        if request.negative_prompt:
            body["input"] = {**body["input"], "negative_prompt": request.negative_prompt}  # type: ignore[dict-item]

        async with httpx.AsyncClient(timeout=60) as client:
            task_id = await self._submit(client, body)
            return await self._poll(client, task_id)

    async def _submit(self, client: httpx.AsyncClient, body: dict[str, object]) -> str:
        try:
            resp = await client.post(
                f"{BASE_URL}{SUBMIT_PATH}",
                headers={**self._headers, "X-DashScope-Async": "enable"},
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise AppError(
                "provider.transient.timeout", message=f"dashscope submit: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError("provider.unavailable", message=f"dashscope: {exc}") from exc

        _raise_for_status(resp)
        task_id = ((resp.json().get("output") or {}).get("task_id")) or ""
        if not task_id:
            raise AppError("provider.unavailable", message=f"无 task_id: {resp.text[:200]}")
        return str(task_id)

    async def _poll(self, client: httpx.AsyncClient, task_id: str) -> ImageResult:
        waited = 0.0
        while waited < _POLL_TIMEOUT_SECONDS:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            waited += _POLL_INTERVAL_SECONDS

            try:
                resp = await client.get(f"{BASE_URL}/api/v1/tasks/{task_id}", headers=self._headers)
            except httpx.HTTPError:
                # 轮询期间的网络抖动不该让整个任务失败，下一轮再试
                continue

            _raise_for_status(resp)
            output = resp.json().get("output") or {}
            status = str(output.get("task_status", ""))

            if status not in _TERMINAL:
                continue
            if status != "SUCCEEDED":
                raise AppError(
                    "provider.unavailable",
                    message=f"任务 {status}: {output.get('message', '')[:200]}",
                )

            results = output.get("results") or []
            urls = [r["url"] for r in results if r.get("url")]
            if not urls:
                # 上游把内容拦了时常表现为 SUCCEEDED 但结果里带 code
                codes = [r.get("code") for r in results if r.get("code")]
                if codes:
                    raise AppError("provider.content.rejected", message=f"上游拒绝: {codes}")
                raise AppError("provider.unavailable", message="结果为空")

            return ImageResult(
                urls=urls,
                model_id=self.model_id,
                actual_prompts=[r.get("actual_prompt", "") for r in results],
            )

        raise AppError(
            "provider.transient.timeout",
            message=f"轮询超过 {_POLL_TIMEOUT_SECONDS}s，task_id={task_id}",
        )


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    detail = resp.text[:300]
    if resp.status_code == 429:
        raise AppError("provider.rate_limit.exceeded", message=detail)
    if resp.status_code in (401, 403):
        raise AppError("provider.account.insufficient", message=f"auth failed: {detail}")
    if resp.status_code == 400:
        # 内容安全拦截走 400 且带特定 code
        if "DataInspection" in detail or "content" in detail.lower():
            raise AppError("provider.content.rejected", message=detail)
        raise AppError("provider.params.invalid", message=detail)
    raise AppError("provider.unavailable", message=f"HTTP {resp.status_code}: {detail}")
