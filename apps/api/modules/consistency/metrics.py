"""一致性度量（17_ConsistencyEngine.md §6）。

QA 不能靠"感觉像"。没有可计算的指标就无法回答三个问题：
这一镜要不要重生成、真实废片率是多少、改了提示词到底变好还是变坏。

用 DashScope 的多模态 embedding（1024 维）算余弦相似度。
这是**代理指标**不是人脸识别：它衡量的是整体视觉相似度，
包含构图和色调，不只是脸。阈值需要用真实数据校准，
校准前不要把它当成绝对判据。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

from apps.api.core.config import get_settings
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger

log = get_logger(__name__)

EMBED_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/embeddings"
    "/multimodal-embedding/multimodal-embedding"
)
EMBED_MODEL = "multimodal-embedding-v1"

# 阈值来自 2026-08-17 的实测标定（scripts/validation_slice.py + 对照实验）：
#
#   不同角色 / 同镜别        0.30      ← 身份确实不同
#   同一角色 / 差异大的镜别   0.65–0.68  ← 远景、特写手部、多人同框
#   同一角色 / 相近镜别       0.81–0.84
#
# 关键发现：**构图差异对整图 embedding 的影响，和身份差异是同一个量级。**
# 一个全局阈值必然误判——0.75 会把所有远景和特写都判成"角色崩了"。
#
# 所以拆成两个：
#   REJECT 只挡真正的身份错误，取值远低于同角色最差值
#   PASS   只在"镜别相近"时才有意义，落在中间地带的一律交人工
#
# 真正的解法是按镜别分别建基准（三视图 + 表情集），
# 让比对发生在同类镜别之间——17_ConsistencyEngine.md §3 描述的角色资产
# 流水线，现在有数据证明它是必需的而非锦上添花。M2 落地。
CHARACTER_SIMILARITY_PASS = 0.78
CHARACTER_SIMILARITY_REJECT = 0.50


@dataclass(frozen=True, slots=True)
class Similarity:
    value: float
    passed: bool
    must_regenerate: bool


async def embed_image_url(url: str) -> list[float]:
    """对一张图取 embedding。"""
    key = get_settings().dashscope_api_key.get_secret_value()
    if not key:
        raise AppError("provider.unavailable", message="未配置 DASHSCOPE_API_KEY")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            EMBED_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": EMBED_MODEL, "input": {"contents": [{"image": url}]}},
        )
    if resp.status_code != 200:
        raise AppError(
            "provider.unavailable", message=f"embedding HTTP {resp.status_code}: {resp.text[:200]}"
        )

    payload = resp.json()
    items = (payload.get("output") or {}).get("embeddings") or []
    if not items or "embedding" not in items[0]:
        raise AppError("provider.unavailable", message=f"embedding 响应异常: {payload}")
    return [float(x) for x in items[0]["embedding"]]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"维度不一致：{len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def judge_similarity(value: float) -> Similarity:
    """把相似度翻译成动作。

    三档而不是两档：中间地带交给人看，不要替用户做"勉强能用"的决定。
    """
    return Similarity(
        value=value,
        passed=value >= CHARACTER_SIMILARITY_PASS,
        must_regenerate=value < CHARACTER_SIMILARITY_REJECT,
    )
