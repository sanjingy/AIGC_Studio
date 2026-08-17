"""Agent 用的文本生成入口。

真实调用走 Gateway（按能力解析 Provider + failover + 熔断），
Mock 用于测试与无 Key 环境。切换只改这一处，runner 不感知。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from adapters.providers.base import TextRequest
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.gateway import service as gateway

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LLMRequest:
    system: str
    user: str
    schema_name: str
    max_output_tokens: int
    temperature: float = 0.7


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    model_id: str


class LLMProvider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...


class GatewayLLM:
    """走 AI Gateway 的真实实现。"""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        resp = await gateway.generate_text(
            TextRequest(
                system=request.system,
                user=request.user,
                json_mode=True,
                max_output_tokens=request.max_output_tokens,
                temperature=request.temperature,
            )
        )
        if resp.reasoning_tokens:
            # 推理 token 计入输出预算且要付费，量大时要能看见
            log.info(
                "llm.reasoning_tokens",
                model_id=resp.model_id,
                reasoning=resp.reasoning_tokens,
                output=resp.tokens_out,
            )
        return LLMResponse(
            text=resp.text,
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            model_id=resp.model_id,
        )


# ---------------------------------------------------------------- Mock


class MockLLM:
    """按 schema 产出合法的假数据。

    用输入的哈希做种子：同样的输入永远得到同样的输出。
    不确定的 Mock 会让测试时红时绿，比没有 Mock 更糟。
    """

    model_id = "mock.llm.v1"

    def __init__(self, *, fail_schema_times: int = 0) -> None:
        self._fail_remaining = fail_schema_times

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            return LLMResponse(
                text='{"broken": true}', tokens_in=10, tokens_out=5, model_id=self.model_id
            )

        seed = int(hashlib.sha256(request.user.encode()).hexdigest()[:8], 16)
        builder = _BUILDERS.get(request.schema_name)
        if builder is None:
            raise AppError(
                "agent.output.schema_invalid",
                message=f"Mock 不支持 schema {request.schema_name}",
            )
        text = json.dumps(builder(seed, request.user), ensure_ascii=False)
        return LLMResponse(
            text=text,
            tokens_in=len(request.system + request.user) // 3,
            tokens_out=len(text) // 3,
            model_id=self.model_id,
        )


def _router(seed: int, user: str) -> dict[str, Any]:
    if len(user.strip()) < 12:
        return {
            "route": "CUSTOM",
            "confidence": 0.3,
            "reason": "需求描述过短，无法判断创作路线",
            "estimated_duration_seconds": 60,
            "estimated_shots": 12,
            "tier": "standard",
            "requires_clarification": True,
            "clarification_question": "想做多长的片子？是从小说改编还是已有剧本？",
        }
    route = "NOVEL_TO_ANIME" if "小说" in user or "漫剧" in user else "SHORT_VIDEO"
    duration = 300 if "5 分钟" in user or "5分钟" in user else 60
    return {
        "route": route,
        "confidence": 0.9,
        "reason": "根据关键词判断为小说改编漫剧" if route == "NOVEL_TO_ANIME" else "默认短视频路线",
        "estimated_duration_seconds": duration,
        "estimated_shots": max(1, duration // 5),
        "tier": "standard",
        "requires_clarification": False,
        "clarification_question": None,
    }


def _story(seed: int, _user: str) -> dict[str, Any]:
    acts = 4 if seed % 2 == 0 else 3
    return {
        "title": "雾港迷案",
        "logline": "一名侦探在雾锁的码头追查一桩十年前的旧案。",
        "central_conflict": "真相与守护之间的两难抉择",
        "acts": [
            {
                "index": i + 1,
                "title": f"第 {i + 1} 幕",
                "summary": f"第 {i + 1} 幕的情节推进与冲突升级。",
                "mood": ["压抑", "紧张", "反转", "释然"][i % 4],
            }
            for i in range(acts)
        ],
    }


def _visual(seed: int, _user: str) -> dict[str, Any]:
    shots = 6 + seed % 4
    return {
        "characters": [
            {
                "ref": "lin_shu",
                "name": "林舒",
                "age_range": "30 出头",
                "hair": "黑色短发，右侧偏分，发梢微卷",
                "eyes": "深褐色，眼尾下垂",
                "face": "瘦削，颧骨明显，左眉有一道旧疤",
                "build": "偏瘦，中等身高",
                "outfit": "深灰长风衣，内搭高领毛衣，黑色皮鞋",
                "distinctive": "常年戴一块停摆的银色怀表",
            }
        ],
        "scenes": [
            {
                "ref": "old_dock",
                "name": "旧码头",
                "setting": "锈蚀的集装箱与断裂的栈桥，海雾弥漫",
                "lighting": "夜晚，远处钠灯的昏黄侧光",
            }
        ],
        "shots": [
            {
                "index": i + 1,
                "scene_ref": "old_dock",
                "character_refs": ["lin_shu"] if i % 2 == 0 else [],
                "shot_size": ["远景", "中景", "特写", "近景"][i % 4],
                "content": f"第 {i + 1} 个镜头的画面内容描述。",
                "dialogue": "「你来晚了。」" if i == 3 else "",
            }
            for i in range(shots)
        ],
    }


def _director(_seed: int, user: str) -> dict[str, Any]:
    if "story" in user:
        return {"next_role": "visual", "gate": "setup", "reason": "故事已完成，进入视觉设计"}
    return {"next_role": "story", "gate": None, "reason": "从故事结构开始"}


def _qa(_seed: int, _user: str) -> dict[str, Any]:
    return {"passed": True, "issues": []}


_BUILDERS = {
    "RouterDecision": _router,
    "StoryOutline": _story,
    "VisualPlan": _visual,
    "DirectorPlan": _director,
    "QAReport": _qa,
}


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """选择文本生成实现。

    规则（按优先级）：
    1. `ENV=test` 一律用 Mock。**这条不能靠 conftest 去设**——
       测试环境有 Key 时，漏设一次就是每跑一遍测试都在真花钱，
       还会把网络抖动和限流带进 CI。默认必须是安全的。
    2. 有 Key 走真实 Gateway
    3. 没 Key 用 Mock，这样本地无凭据也能跑通全链路
    """
    global _provider
    if _provider is None:
        from apps.api.core.config import get_settings

        settings = get_settings()
        if settings.env == "test":
            _provider = MockLLM()
        elif settings.deepseek_api_key.get_secret_value():
            _provider = GatewayLLM()
        else:
            _provider = MockLLM()
        log.info("llm.provider_selected", kind=type(_provider).__name__, env=settings.env)
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    global _provider
    _provider = provider
