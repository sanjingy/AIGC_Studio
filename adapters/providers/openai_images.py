"""OpenAI 兼容的出图 Provider：协议 `openai_images`（ADR-039 第 2、6、7 条）。

覆盖 OpenAI 图片兼容中转、火山方舟 Seedream、智谱 CogView——三家都是
`POST {base_url}/images/generations`，返回 `data[]` 里带 `url` 或 `b64_json`。

**只按 `image_generation` 的契约调用**，端点自己声明什么都不采信。

几处刻意的取舍（各家官方文档核对于 2026-10-05，见设计文档 §4）：

- **不发 `response_format`**。OpenAI 的 GPT image 模型不支持它（固定回 `b64_json`），
  智谱没有这个字段，Seedream 默认 `url`。两种返回都解析，就不必替上游选。
- **一次只要一张**。智谱"目前数组中只包含一张图片"、Seedream 组图要另开参数；
  `n>1` 时逐张请求，而不是发一个上游可能静默忽略的 `n`。
- **`negative_prompt` / `seed` 不发**：这个协议没有统一字段，发了要么 400 要么被忽略。
  风格锁定在正向提示词里（ADR-036 原样注入），不靠负向词。
- **提示词改写**：这个协议没有统一的关闭开关；Seedream 的 `optimize_prompt_options`
  只有 standard / fast 两档、关不掉，智谱没有改写参数。能做的是把上游回传的
  `revised_prompt` 照记下来（ADR-039 第 7 条），没有就记空串，不拿请求词冒充。
- **预设可以带少量请求体覆盖**（`request_overrides`），键走 :data:`OVERRIDE_KEYS` 白名单。
  目前只有 Seedream 的 `watermark=false`：它默认在右下角打"AI 生成"，而平台自己在渲染
  管线里打 AIGC 标识，两层叠加位置样式都不可控。

安全与 `openai_compat` 相同：构造时规整地址，每次请求前做解析校验，不跟随跳转。
结果里的下载地址同样是用户可控的出网地址，由 Worker 下载时校验（`worker/jobs/generation.py`）。
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

import httpx

from adapters.providers import endpoint_url
from adapters.providers.base import ImageRequest, ImageResult, KeySource, signal_key_source
from adapters.providers.openai_compat import failure_detail, raise_for_upstream, refuse_redirect
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger

log = get_logger(__name__)

PROVIDER_ID = "provider.org"

#: 预设里允许出现的请求体覆盖键。**白名单**：预设文件随代码发布，但它是数据，
#: 不该成为往上游请求里塞任意字段的通道。
OVERRIDE_KEYS: frozenset[str] = frozenset({"watermark"})

REQUEST_TIMEOUT_SECONDS = 180
VERIFY_TIMEOUT_SECONDS = 20
MAX_IMAGES_PER_CALL = 4
#: 单张 `b64_json` 解码后的上限，与 Worker 下载 URL 的上限一致
MAX_INLINE_BYTES = 32 * 1024 * 1024

_CONTENT_HINTS = ("sensitive", "content_filter", "content policy", "moderation", "敏感", "违规")


def clean_overrides(raw: dict[str, Any] | None) -> dict[str, Any]:
    """只留白名单键、只留标量值。不认识的键直接报错，不静默丢——预设写错要在加载时就炸。"""
    out: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        if key not in OVERRIDE_KEYS:
            raise ValueError(
                f"request_overrides 不允许的键 {key!r}（允许：{sorted(OVERRIDE_KEYS)}）"
            )
        if not isinstance(value, (bool, int, str)):
            raise ValueError(f"request_overrides.{key} 只能是标量")
        out[key] = value
    return out


def to_openai_size(size: str) -> str:
    """`1024*1024`（万相写法）→ `1024x1024`（OpenAI 兼容写法）。已是 x 写法的原样返回。"""
    return size.replace("*", "x").replace("×", "x").strip()


class OpenAIImagesProvider:
    provider_id = PROVIDER_ID

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        key_source: KeySource = KeySource.ORG,
        transport: httpx.AsyncBaseTransport | None = None,
        provider_id: str | None = None,
        request_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._base_url = endpoint_url.normalize_base_url(base_url)
        self._api_key = api_key
        self.model_id = model_id
        self.key_source = key_source
        self._transport = transport
        self._overrides = clean_overrides(request_overrides)
        if provider_id:
            self.provider_id = provider_id

    def _signal_call(self) -> None:
        signal_key_source(
            provider_id=self.provider_id, model_id=self.model_id, key_source=self.key_source
        )

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=self._transport)

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def request_body(self, request: ImageRequest) -> dict[str, object]:
        """单张图的请求体。单独拿出来，测试断言"该发的发了、不该发的没发"。"""
        body: dict[str, object] = {
            "model": self.model_id,
            "prompt": request.prompt,
            "size": to_openai_size(request.size),
        }
        body.update(self._overrides)
        return body

    async def generate_image(self, request: ImageRequest) -> ImageResult:
        self._signal_call()
        count = max(1, min(int(request.n or 1), MAX_IMAGES_PER_CALL))
        if request.negative_prompt or request.seed is not None:
            log.info(
                "openai_images.fields_not_sent",
                model_id=self.model_id,
                negative_prompt=bool(request.negative_prompt),
                seed=request.seed is not None,
            )
        await endpoint_url.assert_public_host(self._base_url)

        urls: list[str] = []
        inline: list[bytes] = []
        url_prompts: list[str] = []
        inline_prompts: list[str] = []
        async with self._client(REQUEST_TIMEOUT_SECONDS) as client:
            for _ in range(count):
                for item in await self._generate_one(client, request):
                    revised = str(item.get("revised_prompt") or "")
                    b64 = item.get("b64_json")
                    url = item.get("url")
                    if isinstance(b64, str) and b64:
                        inline.append(_decode_b64(b64))
                        inline_prompts.append(revised)
                    elif isinstance(url, str) and url:
                        urls.append(url)
                        url_prompts.append(revised)
        if not urls and not inline:
            raise AppError("provider.unavailable", message="上游没有返回任何图片")
        return ImageResult(
            urls=urls,
            model_id=self.model_id,
            actual_prompts=url_prompts + inline_prompts,
            inline=inline,
        )

    async def _generate_one(
        self, client: httpx.AsyncClient, request: ImageRequest
    ) -> list[dict[str, Any]]:
        try:
            resp = await client.post(
                f"{self._base_url}/images/generations",
                headers={**self._auth, "Content-Type": "application/json"},
                json=self.request_body(request),
            )
        except httpx.TimeoutException as exc:
            raise AppError("provider.transient.timeout", message=f"出图上游超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise AppError("provider.unavailable", message=f"出图上游连不上：{exc}") from exc

        refuse_redirect(resp)
        _raise_for_status(resp, model_id=self.model_id)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise AppError("provider.unavailable", message="出图上游返回的不是 JSON") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise AppError("provider.unavailable", message="出图上游返回里没有 data 数组")
        items = [item for item in data if isinstance(item, dict)]
        if not items:
            raise AppError("provider.unavailable", message="出图上游返回的 data 为空")
        return items

    async def verify_key(self) -> str:
        """测试连接：`GET {base_url}/models`，不出图、不花钱。列表只作提示，不采信。"""
        self._signal_call()
        await endpoint_url.assert_public_host(self._base_url)
        try:
            async with self._client(VERIFY_TIMEOUT_SECONDS) as client:
                resp = await client.get(f"{self._base_url}/models", headers=self._auth)
        except httpx.TimeoutException as exc:
            raise AppError("provider.transient.timeout", message=f"出图上游超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise AppError("provider.unavailable", message=f"出图上游连不上：{exc}") from exc

        refuse_redirect(resp)
        if resp.status_code == 404:
            # 不少出图上游不实现 /models。404 说明地址通了、鉴权没被拒，
            # 不能据此判定 Key 好坏，如实告诉用户。
            return "地址可达；该上游不提供模型列表，Key 是否可用要以第一次出图为准"
        _raise_for_status(resp, model_id=self.model_id)
        # 与文本连接同理：读到模型列表不等于出图接口能用，别画成"连接正常"
        return (
            "模型列表接口鉴权通过。只读取了模型列表（GET /models，不发出图请求），没有试调用 "
            "POST /images/generations；该模型能否出图，以第一次出图为准"
        )


def _decode_b64(text: str) -> bytes:
    # 少数中转会回 data URL 形式（`data:image/png;base64,...`）
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    if len(text) > MAX_INLINE_BYTES * 4 // 3 + 16:
        raise AppError("asset.upload.too_large", message="上游回传的图片过大")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError(
            "provider.unavailable", message="上游回传的 b64_json 不是合法 Base64"
        ) from exc


def _raise_for_status(resp: httpx.Response, *, model_id: str) -> None:
    """状态码 → 错误目录。与文本连接同一套分法（`raise_for_upstream`），只多一条：
    400 / 422 的正文带内容审核字样时归 `provider.content.rejected`。"""
    if resp.status_code in (400, 422):
        text = resp.text[:300]
        if any(hint in text.lower() for hint in _CONTENT_HINTS):
            raise AppError(
                "provider.content.rejected",
                message=text,
                detail=failure_detail(resp, model_id=model_id),
            )
    raise_for_upstream(resp, model_id=model_id)
