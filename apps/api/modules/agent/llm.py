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
from apps.api.modules.local_runtime import service as local_runtime
from apps.api.modules.prompting import rules

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

    # 哪个项目在跑。Gateway 拿它读项目级模型偏好（ADR-024）。
    # 不挂项目的调用（资产库里那条直接生成角色档案的路径）为 None，
    # 走 Gateway 的默认优先级。
    project_id: uuid.UUID | None = None

    # 这一步允不允许用推理模型。False 时项目级偏好里的推理模型会被丢掉，
    # 按默认优先级走。ADR-024 硬约束 2：`no_reasoning_roles` 里的 role
    # 用上 deepseek-v4-* 会**返回空内容且不报错**。
    # 判断放在调用方是因为只有它知道自己是什么 role。
    allow_reasoning: bool = True


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
            project_id=request.project_id,
            allow_reasoning=request.allow_reasoning,
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


# ---------------------------------------------------------------- 本地 CLI


class LocalCLILLM:
    """把这一步交给用户桌面上的官方 CLI 跑（试点，默认关闭）。

    与 GatewayLLM 的区别只有一处但很关键：**它不 failover**。
    选中了本地就只有本地——失败必须以失败告终。退回 Gateway 是拿用户的钱
    去补一次他以为在用订阅额度的调用；退回 Mock 更糟，那会把假档案写进库。
    """

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if request.org_id is None or request.project_id is None:
            # 走不到：RoutingLLM 只在 applies_to 为真时才进来，而它要求两者都有。
            # 留着这一手是因为 `python -O` 会把 assert 整条删掉，那样这里就成了
            # 一个静默把 None 传下去的洞。
            raise AppError(
                "common.forbidden",
                message="local runtime requires both org_id and project_id",
            )
        result = await local_runtime.complete_text(
            org_id=request.org_id,
            project_id=request.project_id,
            system=request.system,
            user=request.user,
            schema_name=request.schema_name,
            max_output_tokens=request.max_output_tokens,
        )
        return LLMResponse(
            text=result.text,
            # 本地 CLI 通常不回报用量，此时是 0。0 的含义是"未回报"——
            # 不要拿它去算成本，也不要因为它是 0 就以为这次调用不花额度。
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            model_id=result.model_id,
        )


class RoutingLLM:
    """按**每一条请求**决定走本地还是走原来的实现。

    为什么不在 `get_provider()` 里一次性选完：命中条件是 (org, project)，
    而那两个值只有请求本身知道。同一个部署里，白名单内的项目走桌面 CLI、
    其余项目照常走 Mock / Gateway，两条路必须同时成立。

    只有试点开着时 `get_provider()` 才会返回它——关着的时候整条链路与
    接入之前逐字相同，包括 `ENV=test` 拿到的仍然是 MockLLM 本体。
    """

    def __init__(self, fallback: LLMProvider) -> None:
        self._fallback = fallback
        self._local = LocalCLILLM()

    @property
    def fallback(self) -> LLMProvider:
        return self._fallback

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if local_runtime.applies_to(request.org_id, request.project_id):
            return await self._local.complete(request)
        return await self._fallback.complete(request)


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
        "era": "现代",
        "region": "日本",
        "ethnicity": "东亚面孔",
        "era_evidence": "警视厅、资料馆等称谓与地名",
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
                "build": "",
                "outfit": "深灰西装外套，白衬衫，无领带",
                "distinctive": "左手常握成拳",
                "height": "中等身高",
                "body_type": "精瘦结实",
                "posture": "紧绷戒备",
                "nationality": "日本",
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
                "build": "",
                "outfit": "一袭白衣",
                "distinctive": "无框眼镜",
                "height": "高挑",
                "body_type": "纤细单薄",
                "posture": "沉稳压场",
                "nationality": "日本",
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
        "global_tone": "柔和自然的冷调，低饱和",
        "era": "现代",
        "scenes": [
            {
                "ref": "gate",
                "name": "资料馆门口",
                "time_slot": "上午",
                "setting": "爬满爬山虎的水泥墙围出的院落，锈迹斑斑的滑动铁门",
                "lighting_states": [
                    {
                        "name": "上午",
                        "description": "均匀自然日光自左上方射入，阴影清晰短小",
                    },
                    {
                        "name": "傍晚",
                        "description": "低角度侧光从围墙西侧射入，阴影拉长，明暗对比强",
                    },
                ],
                "default_lighting": "上午",
                "key_elements": ["铁门", "门柱牌子", "红砖建筑", "停车场"],
                "camera_axis": {
                    "position": "铁门外的路面",
                    "facing": "朝向建筑正面",
                    "far_end": "红砖三层建筑的正门石阶",
                },
                "fixed_references": [
                    {
                        "name": "锈迹铁门",
                        "description": "画面正前方的双开滑动铁门，右扇下缘锈穿一个巴掌大的洞",
                    },
                    {
                        "name": "门柱牌子",
                        "description": "铁门右侧砖柱上齐胸高的白底黑字铜牌，右下角螺丝缺一颗",
                    },
                ],
            },
            {
                "ref": "office",
                "name": "馆长室",
                "time_slot": "室内不分时",
                "setting": "约八叠大小，两侧顶天书架，正中黑檀木书桌",
                "lighting_states": [
                    {
                        "name": "常态",
                        "description": "窗帘遮蔽，顶部吊灯与桌面台灯为主，整体偏暗",
                    },
                ],
                "default_lighting": "常态",
                "key_elements": ["黑檀木书桌", "书架", "窗帘"],
                "camera_axis": {
                    "position": "房门内侧",
                    "facing": "朝向书桌",
                    "far_end": "书桌后的窗帘墙",
                },
                "fixed_references": [
                    {
                        "name": "黑檀木书桌",
                        "description": (
                            "房间正中的宽面书桌，右前角一盏铜制台灯，桌面右侧压着一摞卷宗"
                        ),
                    },
                    {
                        "name": "顶天书架",
                        "description": "沿左右两墙各一排到顶书架，左排第三格空着一段，露出墙面",
                    },
                ],
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
                # 门口那半段戏在上午拍，馆长室只有一个状态。**引用的都是
                # 对应场景真的声明过的名字**——Mock 也是下游的输入样例，
                # 这里写个不存在的名字等于教后来的人怎么写错。
                "lighting_ref": "上午" if i < shots // 2 else "常态",
                "content": f"第 {i + 1} 镜的画面内容。",
                "speaker_ref": "guan_zhang" if i == shots - 2 else "",
                "dialogue": "有问题想问你。" if i == shots - 2 else "",
                "sfx": "门锁转动声" if i == 1 else "",
            }
            for i in range(shots)
        ],
    }


# ---------------------------------------------------------------- 成品提示词
#
# 下面四个 builder 与前面那些的性质不同，值得单独说一句。
#
# 前面的 builder 只要**形状**合法（schema 过得去）就够了。这四个不行：
# `prompting.service._verify` 会拿 `rules.check_style` / `rules.check_output`
# 逐条查产出——风格词有没有被原样保留、四格格名齐不齐、三重否定在不在、
# 视频提示词最后一行是不是那句强制声明。形状合法但内容不合格，
# **整条新链路的测试全红**，而且红在校验器里，看不出是 Mock 的锅。
#
# 所以它们要从 `user`（就是 `service._user_input()` 产出的上下文 JSON）里
# 把风格词、分镜行、角色档案解出来，再拼一段**真的能通过校验**的假提示词。
# 假的是文采，不是结构。


def _ctx(user: str) -> dict[str, Any]:
    """把送进来的上下文 JSON 解回来。解不动就当空的。

    解不动只会让下面的 builder 产出一段缺风格词的提示词，然后被校验器拒掉
    ——那正是真模型写错时该发生的事，所以这里不抛异常。
    """
    try:
        payload = json.loads(user)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _style_words(ctx: dict[str, Any]) -> str:
    """把风格块里所有必须原样出现的词拼成一段。

    `check_style` 查三样：逐个风格描述词、全局色调、渲染方式。这里一次性
    全写进去——真模型被要求"原样照抄"，Mock 也照抄，不然测出来的是
    Mock 的文采而不是链路。
    """
    style = dict(ctx.get("style") or {})
    fields = ("tokens", "color_grading", "render_mode", "line_weight")
    parts = [str(style.get(f, "") or "").strip() for f in fields]
    return "，".join(p for p in parts if p)


def _pad(text: str, minimum: int) -> str:
    """补到 schema 的最小长度。

    补的是**中性说明句**而不是随机字符：`_check_shot_image` 会拒绝「定格」
    「字幕」「背景音乐」这类词，随手塞字有概率撞上；空话则不影响任何一条
    校验，也不会让人误以为 Mock 写出了什么真东西。
    """
    filler = "画面整洁，构图稳定，细节按上述设定执行。"
    while len(text) < minimum:
        text = f"{text}\n{filler}"
    return text


def _character_prompt(seed: int, user: str) -> dict[str, Any]:
    del seed
    ctx = _ctx(user)
    character = dict(ctx.get("character") or {})
    era = dict(ctx.get("era") or {})
    appearance = dict(character.get("appearance") or {})
    human = str(character.get("kind", "人类")) == "人类"

    # 身份与配件都走 `rules` 里那一份定义，不在这里抄第二遍字段名——
    # 抄了就会出现"Mock 认为档案里有、校验器认为没有"，而那种红是查不出来的。
    del appearance
    identity = rules.known_identity(character, era)
    if not identity:
        identity = str(character.get("species", "") or "").strip() or "非人类"

    # 配件照抄档案原话。**不一律写「无」**——那正是校验器要抓的退化：
    # 关键配件往往就是这个角色的识别度所在。
    accessories = rules.known_accessories(character) or "无"

    lines = [
        f"{identity}，{character.get('name', '角色')}，全身立绘，中性站姿，纯色背景。",
        _style_words(ctx),
    ]
    if accessories != "无":
        lines.append(f"关键配件：{accessories}。")
    if human:
        # 四铁律里校验器盯着的两条：必须写「无任何表情」，不许出现背包书包。
        lines.append("面部无任何表情，直视镜头，中性光照，五官清晰。")
    else:
        lines.append("完整展示体表特征与轮廓，中性光照。")
    return {
        "template": "人类" if human else "非人类",
        "prompt": _pad("\n".join(p for p in lines if p.strip()), 40),
        "subject_identity": identity,
        "accessories": accessories,
    }


def _scene_prompt(seed: int, user: str) -> dict[str, Any]:
    del seed
    ctx = _ctx(user)
    scene = dict(ctx.get("scene") or {})
    name = str(scene.get("name", "") or "场景")

    # 元素锁定清单：四格只是机位不同，这些必须字字相同。schema 要求至少两项，
    # 不够就拿场景本身的描述凑——凑不出来才说明档案确实是空的。
    elements = [str(e).strip() for e in (scene.get("key_elements") or []) if str(e).strip()]
    elements += [str(r).strip() for r in (scene.get("fixed_references") or []) if str(r).strip()]
    for extra in (str(scene.get("setting", "") or "").strip(), f"{name}的地面与墙面"):
        if len(elements) >= 2:
            break
        if extra and extra not in elements:
            elements.append(extra)
    elements = list(dict.fromkeys(elements))[:16]

    quadrants = {
        "top_left": "场景正中天花板垂直俯视全景",
        "top_right": "沿摄影主轴平视正视图",
        "bottom_left": "右前角朝左后角的对角线视图",
        "bottom_right": "右后角朝左前角的对角线视图",
    }
    body = [
        f"{name}概念图，2x2 四宫格布局，四格机位固定。",
        _style_words(ctx),
        "元素锁定清单：" + "、".join(elements) + "。四格中以上元素完全一致，朝向不变。",
    ]
    body += [f"{label}：{quadrants[field]}。" for field, label in rules.QUADRANT_LABELS]
    # 三重否定一句都不能少：只写一句实测仍会漏进人影。
    body.append("。".join(rules.NO_PEOPLE_CLAUSES) + "。")
    body.append("无文字标注。")
    return {
        "prompt": _pad("\n".join(p for p in body if p.strip()), 80),
        "fixed_elements": elements,
        "quadrants": quadrants,
    }


def _shot_frame_prompt(seed: int, user: str) -> dict[str, Any]:
    del seed
    ctx = _ctx(user)
    shot = dict(ctx.get("shot") or {})
    scene = dict(ctx.get("scene") or {})
    anchor = dict(ctx.get("anchor_card") or {})
    characters = [dict(c) for c in (ctx.get("characters") or []) if isinstance(c, dict)]

    # 景别照抄分镜表。特写类要带局部说明，而局部说明**替换**"特写"二字，
    # 所以这里拼成「手部特写」而不是「特写，手部特写」。
    storyboard_size = str(shot.get("shot_size", "") or "").strip() or "中景"
    shot_size = storyboard_size
    if storyboard_size in rules.DETAIL_SHOT_SIZES:
        shot_size = f"面部{storyboard_size}"
    angle = str(shot.get("angle", "") or "").strip()
    names = "、".join(str(c.get("name", "") or c.get("ref", "")) for c in characters)

    references = [str(r).strip() for r in (anchor.get("fixed_references") or []) if str(r).strip()]
    facing = f"{names}面向摄影主轴左前方" if characters else "无人物出场"
    behind = (
        f"身后是{scene.get('name', '场景')}的"
        f"{references[0] if references else '远景末端'}一侧空间"
    )
    lighting = str((ctx.get("lighting") or {}).get("description", "") or "自然光")
    pose_lighting = f"{'保持静止姿态，' if characters else '空镜，'}{lighting}"

    body = [
        f"{shot_size}。",
        f"{angle}。" if angle else "",
        _style_words(ctx),
        str(shot.get("content", "") or ""),
        f"角色朝向：{facing}。",
        f"身后背景：{behind}。",
        f"姿态与光影：{pose_lighting}。",
    ]
    return {
        "prompt": _pad("\n".join(p for p in body if p.strip()), 40),
        "shot_size": shot_size,
        "angle": angle,
        "facing": facing,
        "behind": behind,
        "pose_lighting": pose_lighting,
    }


def _shot_video_prompt(seed: int, user: str) -> dict[str, Any]:
    del seed
    ctx = _ctx(user)
    shot = dict(ctx.get("shot") or {})
    scene = dict(ctx.get("scene") or {})
    characters = [dict(c) for c in (ctx.get("characters") or []) if isinstance(c, dict)]
    lighting = str((ctx.get("lighting") or {}).get("description", "") or "自然光")
    scene_name = str(scene.get("name", "") or "场景")

    names = "、".join(str(c.get("name", "") or c.get("ref", "")) for c in characters) or "空镜"
    asset_line = (
        f"@是{names}，@是{scene_name}，{_style_words(ctx)}，在{scene_name}，{lighting}。"
    )

    dialogue = str(shot.get("dialogue", "") or "").strip()
    camera_move = str(shot.get("camera_move", "") or "").strip()
    sfx = str(shot.get("sfx", "") or "").strip()
    content = str(shot.get("content", "") or "").strip() or "画面推进"

    # 每个切镜独立一行，一行只放一个【切镜】。无台词最少两个，有台词最少三个。
    cuts = [
        f"【切镜】{camera_move or '固定机位'}，{content}。",
        f"【切镜】{names}动作连续，{lighting}。",
    ]
    if sfx:
        cuts[1] = f"【切镜】{names}动作连续，{lighting}，音效：{sfx}。"
    if dialogue:
        cuts.append(f"【切镜】{names}开口，台词：{dialogue}")

    # 最后一行强制声明不可省略、不可移到代码块外侧。它自己带着「背景音乐」
    # 与「字幕」各一次，所以上面几行一个字都不许再提这两个词。
    prompt = "\n".join([asset_line, *cuts, rules.VIDEO_TAIL])
    return {
        "prompt": prompt,
        "asset_line": asset_line,
        "cuts": cuts,
        "anchor_card": f"{scene_name}空间锚点：{lighting}",
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
    "CharacterPortraitPrompt": _character_prompt,
    "SceneViewsPrompt": _scene_prompt,
    "ShotFramePrompt": _shot_frame_prompt,
    "ShotVideoPrompt": _shot_video_prompt,
}


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """选择文本生成实现。

    规则（按优先级）：
    1. `ENV=test` 一律用 Mock。**这条不能靠 conftest 去设**——
       测试环境有 Key 时，漏设一次就是每跑一遍测试都在真花钱，
       还会把网络抖动和限流带进 CI。默认必须是安全的。
    2. 其余**一律**走真实选择器 `GatewayLLM`。

    **第 2 条以前是"有平台 Key 才走 Gateway，没有就退回 Mock"，那是个洞**：

    - 平台没配 Key、但这个 org 自己配了 BYOK Key 的租户，本该走他自己的
      Key，实际拿到的是 `MockLLM` 的假档案——他配了 Key，也付了 Credits。
    - 只开本地 CLI 订阅、不配任何平台 Key 的部署同理：`RoutingLLM` 确实
      包在外面，但没命中白名单的项目退回的是 Mock 而不是错误。

    平台 Key、org 的 BYOK Key、模型目录、熔断与 failover 全都是
    `gateway._resolve` 的职责，在这里再判一次"有没有 Key"必然对不上。
    一把可用的都没有时，Gateway 抛 `provider.unavailable`（中文文案），
    用户看到的是"没有可用的模型"，而不是一份不知道哪来的假档案。

    `CLAUDE.md` 承诺的"没有 Key 也能跑通全链路"因此只在 `ENV=test` 成立。
    这是有意的收紧：**本轮契约只认 ENV=test 这一个 Mock 入口**，不另开
    "非 test 也能出占位内容"的开关——多一个入口就多一条让假内容漏给用户
    的路，而那正是 ABC_AUDIT 记下的那次事故。

    3. 本地 CLI 试点开着时，外面再包一层逐请求路由（见 RoutingLLM）。
       **默认关着**，关着时前两条的行为逐字不变。
    """
    global _provider
    if _provider is None:
        from apps.api.core.config import get_settings

        settings = get_settings()
        base: LLMProvider = MockLLM() if settings.env == "test" else GatewayLLM()
        # 3. 本地 CLI 试点开着时，在上面选出来的实现外面包一层逐请求路由。
        #    **关着时一层都不包**：这条规则保证试点不动任何既有行为，
        #    也保证 ENV=test 拿到的就是 MockLLM 本体而不是某个壳。
        _provider = RoutingLLM(base) if settings.local_cli_enabled else base
        log.info(
            "llm.provider_selected",
            kind=type(_provider).__name__,
            env=settings.env,
            local_cli=settings.local_cli_enabled,
        )
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    global _provider
    _provider = provider
