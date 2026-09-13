"""真实生成任务：出图。

两条来源，**同一条落地路径**：

* `api`（默认）：Gateway 出图 → 下载 → 交给 asset service 落库落存储。
  必须转存到自己的对象存储，不能直接把上游 URL 存进库——
  DashScope 的图片链接只有 24 小时有效期，直接存等于资产库里
  躺着一堆明天就打不开的死链。
* `local`（试点）：把提示词送到用户自己电脑上的 Codex，拿回**图片字节**，
  同样交给 asset service。这条路没有 URL 可下载，字节直接就在手里。

来源在**建任务时**就钉死在 `input_json.image_source` 里，这里只读不猜。
选了 `local` 就绝不会走到 Gateway 那一支——那是付费的，而用户以为自己
在用订阅额度；反过来也一样，没选本机的任务不会因为本机恰好在线就改走本机。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from adapters.providers.base import ImageRequest
from apps.api.core.db import session_scope
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.asset import service as asset_service
from apps.api.modules.gateway import service as gateway
from apps.api.modules.local_runtime import service as local_runtime
from apps.api.modules.task import service as task_service

log = get_logger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 120
MAX_IMAGE_BYTES = 32 * 1024 * 1024

# 预检用的估算值。一张 1024×1024 的 PNG 出图大约这个量级。
# 它只用来判断"还有没有余量"，不参与任何计费，也不写进库。
TYPICAL_IMAGE_BYTES = 2 * 1024 * 1024


#: `input_json.image_source` 的两个取值。与 `consistency.render` 里那两个
#: 常量是同一组字面量——这里不 import 它，跨模块只为两个字符串建依赖不划算，
#: 但改动必须两处同时改（两边的注释互相点名）。
SOURCE_API = "api"
SOURCE_LOCAL = "local"


#: 资产元数据里提示词的长度上限。
#:
#: 超过就截断并标 `prompt_truncated=True`，**不再无声截断**：截断本身是
#: 必要的（`assets.metadata_json` 是 JSONB，往里塞无上限的文本会把一条
#: 资产行撑到几百 KB），但用户在生成记录详情里看到的必须是"这条被截过"，
#: 而不是一段看起来完整、实际少了一半的提示词。`prompting.records`
#: 读这个键来决定要不要把整条记录标成 `incomplete`。
#:
#: 8000 是按最长的一类（C2 逐镜视频提示词：资产标注句 + 若干切镜 + 台词
#: 音效 + 强制尾行）留的余量，正常产出不会碰到它。
MAX_METADATA_PROMPT_CHARS = 8000


def _prompt_metadata(prompt: str, actual: str) -> dict[str, Any]:
    """资产元数据里的提示词两项，外加"有没有被截过"。

    两条出图路径（平台 Provider / 本机 Codex）共用，因为它们记的是同一件事，
    分开写迟早会一边加了标志位另一边没加。
    """
    truncated = len(prompt) > MAX_METADATA_PROMPT_CHARS or len(actual) > MAX_METADATA_PROMPT_CHARS
    meta: dict[str, Any] = {
        "prompt": prompt[:MAX_METADATA_PROMPT_CHARS],
        # 上游若改写了提示词必须记下来，否则不知道画出来的东西究竟是按
        # 什么描述生成的。上游没回报时是空串，**不拿请求词冒充回传**。
        "actual_prompt": actual[:MAX_METADATA_PROMPT_CHARS],
    }
    if truncated:
        meta["prompt_truncated"] = True
    return meta


async def generate_image(
    payload: dict[str, Any], *, org_id: uuid.UUID, task_id: uuid.UUID | None = None
) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise AppError("provider.params.invalid", message="prompt 不能为空")

    # 配额预检：在调上游之前。生成完了才发现存不下，钱已经花出去了，
    # 那一次调用就是纯亏损。这里还不知道图会有多大，所以只要求
    # "至少还装得下一张典型出图"——精确的检查在 register_generated 里，
    # 那时字节数是确定的。
    raw_owner = payload.get("owner_user_id")
    owner_user_id = uuid.UUID(str(raw_owner)) if raw_owner else None
    async with session_scope() as db:
        await asset_service.ensure_quota(
            db,
            org_id=org_id,
            owner_user_id=owner_user_id or org_id,
            additional_bytes=TYPICAL_IMAGE_BYTES,
        )

    raw_project = payload.get("project_id")
    project_id = uuid.UUID(str(raw_project)) if raw_project else None

    if str(payload.get("image_source") or SOURCE_API) == SOURCE_LOCAL:
        return await _generate_local(
            payload,
            org_id=org_id,
            project_id=project_id,
            owner_user_id=owner_user_id,
            prompt=prompt,
            task_id=task_id,
        )

    # org_id 必须带上：配了自己 Key 的租户要用他自己的那把（ADR-027）。
    # 漏了这个参数就会退回平台 Key——而计费那边已经按 BYOK 折扣算过了，
    # 差额全由平台承担，且不报任何错。
    #
    # project_id 带上是为了项目级模型偏好（ADR-024）。它比 org_id 温和得多：
    # 漏了只是用户在设置页选的出图模型不生效，不会有人多付钱。
    result = await gateway.generate_image(
        ImageRequest(
            prompt=prompt,
            negative_prompt=str(payload.get("negative_prompt", "")),
            size=str(payload.get("size", "1024*1024")),
            n=int(payload.get("n", 1)),
            seed=payload.get("seed"),
        ),
        org_id=org_id,
        project_id=project_id,
    )

    asset_ids: list[str] = []
    for index, url in enumerate(result.urls):
        data = await _download(url)
        asset_id = uuid.uuid4()
        async with session_scope() as db:
            row = await asset_service.register_generated(
                db,
                org_id=org_id,
                project_id=project_id,
                filename=f"generated_{index}.png",
                storage_key=f"{org_id}/{asset_id}/generated_{index}.png",
                mime_type="image/png",
                data=data,
                owner_user_id=owner_user_id,
                metadata={
                    "model_id": result.model_id,
                    **_prompt_metadata(prompt, (result.actual_prompts[index:] or [""])[0]),
                },
            )
        asset_ids.append(str(row.id))

    return {
        "asset_ids": asset_ids,
        "model_id": result.model_id,
        "actual_prompts": result.actual_prompts,
    }


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise AppError("provider.unavailable", message=f"下载生成结果失败 HTTP {resp.status_code}")
    data = resp.content
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("asset.upload.too_large", message=f"生成结果 {len(data)} 字节过大")
    return data


async def _generate_local(
    payload: dict[str, Any],
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    owner_user_id: uuid.UUID | None,
    prompt: str,
    task_id: uuid.UUID | None,
) -> dict[str, Any]:
    """用用户自己电脑上的 Codex 画一张，落进同一个资产库。

    与 API 那条的差别只有"图从哪来"这一处。**刻意不加任何 fallback**：
    本机不可用、跑失败、模型这一轮没画图，全部以失败告终。悄悄改调付费
    接口等于拿用户的钱补一次他以为免费的调用。

    项目 id 是硬前置：白名单是按 (org, project) 配的，不挂项目的出图任务
    （从 `POST /tasks` 直接建的那种）不该有机会走到这里。
    """
    if project_id is None:
        raise AppError(
            "local_runtime.not_configured",
            message="local image generation requires a project",
        )

    # 第二道（第一道在 `task_service.preflight_local_image`，建任务之前）。
    # `pricing._shape` 按 `n` 计份数，而这条路永远只出一张：不挡的话是
    # **按 n 张预扣、只登记一个资产**，且不报任何错。两道都留着——第一道
    # 保证"钱没动就失败"，这一道保证"就算有人绕过建任务也不会静默少给"。
    count = int(payload.get("n", 1))
    if count != 1:
        raise AppError(
            "provider.params.invalid",
            message="local image generation produces exactly one image",
            detail={"n": count},
        )

    image = await local_runtime.complete_image(
        org_id=org_id,
        project_id=project_id,
        prompt=prompt,
        negative_prompt=str(payload.get("negative_prompt", "")),
        size=str(payload.get("size", "1024*1024")),
        n=count,
        is_cancelled=_cancel_watcher(org_id=org_id, task_id=task_id),
    )

    # 扩展名跟着**嗅探出来的** MIME 走，不跟着对端说的走。
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[image.mime_type]
    asset_id = uuid.uuid4()
    async with session_scope() as db:
        row = await asset_service.register_generated(
            db,
            org_id=org_id,
            project_id=project_id,
            filename=f"generated_0.{ext}",
            storage_key=f"{org_id}/{asset_id}/generated_0.{ext}",
            mime_type=image.mime_type,
            data=image.data,
            owner_user_id=owner_user_id,
            metadata={
                "model_id": image.model_id,
                # Codex 会改写提示词（`revisedPrompt`），这条链路上改写是
                # **常态**不是例外，所以与万相那条用同一个字段名。
                **_prompt_metadata(prompt, image.revised_prompt or ""),
                # 这张图花的是用户自己的订阅额度，不是平台上游成本。
                # 成本复盘时必须能把两类分开，所以标在资产上。
                "image_source": SOURCE_LOCAL,
            },
        )

    log.info(
        "generation.local_image_stored",
        org_id=str(org_id),
        project_id=str(project_id),
        asset_id=str(row.id),
        model_id=image.model_id,
        bytes=len(image.data),
    )
    return {
        "asset_ids": [str(row.id)],
        "model_id": image.model_id,
        "actual_prompts": [image.revised_prompt or ""],
        "image_source": SOURCE_LOCAL,
    }


def _cancel_watcher(
    *, org_id: uuid.UUID, task_id: uuid.UUID | None
) -> Callable[[], Awaitable[bool]] | None:
    """ "用户是不是已经取消了" —— 读 `tasks.status`，不另建一份状态。

    本机出图是分钟级的，而它烧的是用户自己的订阅额度。没有这一路，
    取消只会把界面上的状态改掉，桌面上那台机器还要把这一轮跑满。
    """
    if task_id is None:
        return None

    async def _check() -> bool:
        try:
            async with session_scope() as db:
                task = await task_service.get_task(db, org_id=org_id, task_id=task_id)
                return task.status not in ("queued", "running")
        except AppError:
            # 任务查不到了（理论上不会）——当成"没人等了"，宁可停也不要空转。
            return True

    return _check
