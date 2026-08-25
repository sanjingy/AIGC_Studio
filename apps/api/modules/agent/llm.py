"""Agent 用的文本生成入口。

真实调用走 Gateway（按能力解析 Provider + failover + 熔断），
Mock 用于测试与无 Key 环境。切换只改这一处，runner 不感知。
"""

from __future__ import annotations

import hashlib
import json
import uuid
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
    # 谁在跑这次生成。Gateway 拿它决定用平台的 Key 还是这个 org 自己的
    # （ADR-027）。默认 None = 没有租户上下文（脚本、内部估算），走平台档；
    # 漏传只会让用户白配了 Key，不会让别人的 Key 被用掉。
    org_id: uuid.UUID | None = None


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
            ),
            org_id=request.org_id,
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


def _plot_index(seed: int, _user: str) -> dict[str, Any]:
    count = 5 + seed % 3
    return {
        "genre": "悬疑",
        "logline": "被调职的刑警在旧资料馆接受了一场突如其来的观察力考验。",
        "synopsis": "主角初到新单位报到，沿途遇见门卫与清洁工，最终在馆长室被问及一路上的细节。",
        "central_conflict": "新人被迫在毫无准备的情况下证明自己的观察力",
        "characters": [
            {"name": "主角", "aliases": ["新人"]},
            {"name": "馆长", "aliases": []},
        ],
        "nodes": [{"index": i + 1, "summary": f"节点 {i + 1}"} for i in range(count)],
        "scene_count": 3,
        "dialogue_chars": 240,
    }


def _screenplay(seed: int, _user: str) -> dict[str, Any]:
    scenes = 2 + seed % 2
    return {
        "title": "资料馆的第一天",
        "synopsis": "主角报到当日经历的一场观察力考验。",
        "episodes": [
            {
                "index": 1,
                "title": "第一集",
                "scenes": [
                    {
                        "id": f"1-{i + 1}",
                        "location": "资料馆门口" if i == 0 else "馆长室",
                        "time_mood": "冬日上午 - 压抑",
                        "character_refs": ["zhu_jue", "guan_zhang"],
                        "beats": [
                            {
                                "kind": "action",
                                "character_ref": "",
                                "emotion": "",
                                "text": f"第 {i + 1} 场的第一个动作。",
                            },
                            {
                                "kind": "dialogue",
                                "character_ref": "guan_zhang",
                                "emotion": "冷淡",
                                "text": "有问题想问你。",
                            },
                        ],
                        "hook": "他还不知道考验已经开始了。" if i == scenes - 1 else "",
                    }
                    for i in range(scenes)
                ],
            }
        ],
        "node_coverage": [
            {"node_index": i + 1, "scene_id": f"1-{min(i, scenes - 1) + 1}", "merged_into": None}
            for i in range(5)
        ],
    }


def _character_sheets(_seed: int, _user: str) -> dict[str, Any]:
    return {
        "characters": [
            {
                "ref": "zhu_jue",
                "name": "主角",
                "kind": "人类",
                "camp": "正派",
                "identity": "被调职的刑警",
                "relations": "馆长的新下属",
                "personality": ["隐忍", "敏锐"],
                "power_position": "弱势被压迫者",
                "arc_stage": "潜伏期",
                "present_state": "初来乍到，强撑镇定",
                "age_range": "30 出头",
                "hair": "黑色短发，额前碎发",
                "eyes": "深褐色，眼神沉稳",
                "face": "轮廓分明，下颌线清晰",
                "build": "中等身高，结实精干",
                "outfit": "深灰西装外套，白衬衫，无领带",
                "distinctive": "左手常握成拳",
                "ethnicity": "东亚面孔",
                "skin": "自然健康肤色",
                "shoes": "黑色皮鞋",
                "accessories": "无",
                "inferred": ["outfit"],
            },
            {
                "ref": "guan_zhang",
                "name": "馆长",
                "kind": "人类",
                "camp": "中立",
                "identity": "资料馆馆长",
                "relations": "主角的上司",
                "personality": ["冷峻", "锐利"],
                "power_position": "强势压迫者",
                "arc_stage": "潜伏期",
                "present_state": "端坐读书，不动声色",
                "age_range": "年龄难辨",
                "hair": "黑色长发，垂至腰际",
                "eyes": "瞳仁大而深",
                "face": "人偶般冷峻，五官精致",
                "build": "修长纤细，站姿挺拔",
                "outfit": "一袭白衣",
                "distinctive": "无框眼镜",
                "ethnicity": "东亚面孔",
                "skin": "自然白皙",
                "shoes": "白色平底鞋",
                "accessories": "无框眼镜",
                "inferred": ["shoes"],
            },
        ]
    }


def _scene_sheets(_seed: int, _user: str) -> dict[str, Any]:
    return {
        "era": "现代",
        "scenes": [
            {
                "ref": "gate",
                "name": "资料馆门口",
                "time_slot": "上午",
                "setting": "爬满爬山虎的水泥墙围出的院落，锈迹斑斑的滑动铁门",
                "lighting": "上午均匀自然日光，阴影清晰短小",
                "key_elements": ["铁门", "门柱牌子", "红砖建筑", "停车场"],
                "camera_axis": {
                    "position": "铁门外的路面",
                    "facing": "朝向建筑正面",
                    "far_end": "红砖三层建筑的正门石阶",
                },
                "fixed_references": ["铁门在画面正前方", "门柱牌子在铁门右侧"],
            },
            {
                "ref": "office",
                "name": "馆长室",
                "time_slot": "室内不分时",
                "setting": "约八叠大小，两侧顶天书架，正中黑檀木书桌",
                "lighting": "窗帘遮蔽，室内灯光为主，整体偏暗",
                "key_elements": ["黑檀木书桌", "书架", "窗帘"],
                "camera_axis": {
                    "position": "房门内侧",
                    "facing": "朝向书桌",
                    "far_end": "书桌后的窗帘墙",
                },
                "fixed_references": ["书桌在房间正中", "书架沿左右两墙"],
            },
        ],
    }


def _storyboard(seed: int, _user: str) -> dict[str, Any]:
    shots = 8 + seed % 4
    sizes = ["全景", "中景腰部", "近景胸像", "特写", "中全景", "极近特写"]
    moves = ["固定镜头", "缓缓推近镜头", "跟随主体移动", "向左横移"]
    return {
        "nodes": [
            {"index": 1, "scene_ref": "gate", "summary": "门口报到"},
            {"index": 2, "scene_ref": "office", "summary": "馆长的提问"},
        ],
        "shots": [
            {
                "index": i + 1,
                "node_index": 1 if i < shots // 2 else 2,
                "scene_ref": "gate" if i < shots // 2 else "office",
                "character_refs": ["zhu_jue"] if i % 2 == 0 else ["zhu_jue", "guan_zhang"],
                "shot_size": sizes[i % len(sizes)],
                "angle": "斜前方45度" if i % 3 == 0 else "",
                "camera_move": moves[i % len(moves)],
                "content": f"第 {i + 1} 镜的画面内容。",
                "speaker_ref": "guan_zhang" if i == shots - 2 else "",
                "dialogue": "有问题想问你。" if i == shots - 2 else "",
                "sfx": "门锁转动声" if i == 1 else "",
            }
            for i in range(shots)
        ],
    }


_BUILDERS = {
    "RouterDecision": _router,
    "StoryOutline": _story,
    "VisualPlan": _visual,
    "DirectorPlan": _director,
    "QAReport": _qa,
    "PlotIndex": _plot_index,
    "Screenplay": _screenplay,
    "CharacterSheets": _character_sheets,
    "SceneSheets": _scene_sheets,
    "Storyboard": _storyboard,
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
