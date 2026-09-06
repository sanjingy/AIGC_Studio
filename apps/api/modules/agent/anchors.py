"""空间锚点卡：哪些场景要用户在门③ 一次性确认（ADR-037 第 3 条）。

—— 这个文件存在的全部原因是一处顺序矛盾 ——

ADR-037 给的判据是客观的，但它的三个输入全都长在分镜表上：

    该场景出现 3 个及以上镜号 / 有 2 人及以上同时在场 / 场景内有明显位移

而门③ 在 `scenes` 之后、`storyboard` **之前**。判据要的数据在被判据决定的
那一步之后才产生。照字面实现的话，门③ 打开时手里一条镜号都没有。

**怎么解的**：把判据的输入从分镜表换成**剧本**，剧本在 `scenes` 的上游，
门③ 打开时它一定在。换的是数据来源，不是判据本身——三条判据一条不少：

    ≥3 个镜号    ← 该地点下的节拍总数 ≥ 3
    ≥2 人同场    ← 单场戏里同时出现的角色数 ≥ 2
    明显位移      ← 该地点跨 ≥2 场戏，或单个地点里的动作节拍 ≥ 2

第一条是这次替换里唯一需要论证的：**节拍到镜号的映射是单调的**。
一个节拍至少拆一个镜号（分镜提示词要求逐节点拆镜，动作和台词各占一条），
所以"节拍数"是"镜号数"的下界。用同一个阈值 3 去卡下界，只会**多**判出
需要锚点卡的场景，不会漏判到本该出卡却没出的地步——除非剧本把三个镜号
压进了一个节拍，那属于剧本本身写得太粗，锚点卡救不了。

**误差往哪边倒是有意选的**：多出一张锚点卡的代价是用户在门③ 上多看两行；
少出一张的代价是那个场景的空间关系从头到尾没被锁过，同一个房间的书桌
这镜在左边下镜在右边，而这要到成片剪在一起才看得出来。

—— 还剩下的那点残差怎么办 ——

下界毕竟是下界。分镜跑完之后，真实的镜号数、真实的同框人数、真实的运镜
才第一次可知。所以这里还提供 `reconcile_after_storyboard()`：分镜产出之后
用**真判据**重算一遍，把"分镜跑完才够格、但门③ 上没出卡"的场景列出来，
挂进门④ 的摘要里。

这不是第五道门，也不是把门③ 往后挪。门③ 仍然按 ADR-037 一次性确认固定层
（那一层只依赖场景本身，跟拆多少镜没关系），门④ 只是顺带告诉用户
"这几个场景比预估的更吃重，锚点你可能想回头看一眼"。加一道门要用户多点
一次，而这条信息本来就该和分镜表一起看——它就是从分镜表算出来的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.schemas import coerce_fixed_references

# 三条判据的阈值。**这是产品判据，不是运营参数**，所以写成常量而不是入库。
#
# 判据出自 ADR-037 第 3 条的正文，改动它等于改动这条 ADR，要走同一套流程
# （提 ADR → 改文档 → 改测试）。它和"价格 / 汇率 / 废片率"那类**上游一变
# 就要热更新、且改动不需要任何设计讨论**的系数不是一回事——仓库里已经有
# 同类先例：`tests/eval/eval_suite.py` 的 ROUTE_ACCURACY_MIN 也是文档明文
# 给定的判定阈值，同样写成常量并在注释里说明了为什么。
MIN_SHOTS_FOR_CARD = 3
MIN_CAST_FOR_CARD = 2
MIN_ACTION_BEATS_FOR_MOVEMENT = 2
MIN_SCRIPT_SCENES_FOR_MOVEMENT = 2


@dataclass(frozen=True, slots=True)
class AnchorSignals:
    """一个场景在剧本里的三项可数事实。门③ 上要给用户看，不能只给结论。"""

    beats: int = 0
    script_scenes: int = 0
    max_cast: int = 0
    action_beats: int = 0


@dataclass(frozen=True, slots=True)
class AnchorCard:
    ref: str
    name: str
    camera_axis: dict[str, str] = field(default_factory=dict)
    # 结构化锚点：每条是 {name, description, origin}。门③ 上要分两列显示——
    # 名称是用户扫一眼就能点数的把手，描述是他真正要核对的那句话。
    fixed_references: list[dict[str, str]] = field(default_factory=list)
    # 为什么这个场景要出卡。空列表 = 不出卡，用内联描述即可。
    reasons: list[str] = field(default_factory=list)
    signals: AnchorSignals = field(default_factory=AnchorSignals)

    @property
    def needs_card(self) -> bool:
        return bool(self.reasons)

    @property
    def incomplete(self) -> bool:
        """出了卡但锚点是空的。

        `camera_axis` / `fixed_references` 在 schema 上一直存在、一直没人填。
        真填不上时门③ 要能指出来是哪个场景缺，否则用户确认的是一张空卡，
        而空卡和没有卡对下游是一回事。
        """
        axis_filled = any(str(v).strip() for v in self.camera_axis.values())
        return self.needs_card and not (axis_filled and self.fixed_references)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "name": self.name,
            "camera_axis": self.camera_axis,
            "fixed_references": self.fixed_references,
            "reasons": self.reasons,
            "signals": {
                "beats": self.signals.beats,
                "script_scenes": self.signals.script_scenes,
                "max_cast": self.signals.max_cast,
                "action_beats": self.signals.action_beats,
            },
            "incomplete": self.incomplete,
        }


def _fixed_references(scene: dict[str, Any]) -> list[dict[str, str]]:
    """场景档案上的固定参照物，规范成 `{name, description, origin}` 一列。

    形状归一化复用 `agents.schemas` 的纯函数，理由同
    `consistency/service.py::_spatial`：合法形状是 schema 层的问题，抄一份
    过来就会有两套判断。存量项目的扁平字符串在这里也能显示成一张有名称的
    卡，而不是一行没有标题的句子。
    """
    return [
        {
            "name": str(item.get("name", "")),
            "description": str(item.get("description", "")),
            "origin": str(item.get("origin", "authored")),
        }
        for item in coerce_fixed_references(scene.get("fixed_references"))
    ]


def _script_scenes(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        sc
        for ep in state.get("screenplay", {}).get("episodes", [])
        if isinstance(ep, dict)
        for sc in ep.get("scenes", [])
        if isinstance(sc, dict)
    ]


def _cast_of(scene: dict[str, Any]) -> set[str]:
    """这一场戏里出现了哪些角色。

    `character_refs` 是编剧填的名单，但它会漏——模型经常只填主要角色，
    而台词节拍里明明还有第三个人说话。两边取并集，宁可多算：
    这个数只用来判"要不要出锚点卡"，多判一张的代价远小于漏判。
    """
    cast = {str(r).strip() for r in scene.get("character_refs", []) or [] if str(r).strip()}
    for beat in scene.get("beats", []) or []:
        if isinstance(beat, dict) and (ref := str(beat.get("character_ref", "")).strip()):
            cast.add(ref)
    return cast


def _signals_by_location(state: dict[str, Any]) -> dict[str, AnchorSignals]:
    """按地点名聚合剧本里的三项可数事实。"""
    beats: dict[str, int] = {}
    scenes: dict[str, int] = {}
    cast: dict[str, int] = {}
    actions: dict[str, int] = {}

    for scene in _script_scenes(state):
        loc = str(scene.get("location", "")).strip()
        if not loc:
            continue
        rows = [b for b in scene.get("beats", []) or [] if isinstance(b, dict)]
        beats[loc] = beats.get(loc, 0) + len(rows)
        scenes[loc] = scenes.get(loc, 0) + 1
        # 同框人数取**单场戏的最大值**，不是跨场累加：判据问的是
        # "有没有 2 人同时在场"，两场戏各来一个人不构成同框。
        cast[loc] = max(cast.get(loc, 0), len(_cast_of(scene)))
        actions[loc] = actions.get(loc, 0) + sum(1 for b in rows if b.get("kind") == "action")

    return {
        loc: AnchorSignals(
            beats=beats.get(loc, 0),
            script_scenes=scenes.get(loc, 0),
            max_cast=cast.get(loc, 0),
            action_beats=actions.get(loc, 0),
        )
        for loc in set(beats) | set(scenes) | set(cast) | set(actions)
    }


def _reasons(signals: AnchorSignals) -> list[str]:
    """满足哪几条判据。返回人能读的短句，门③ 上直接展示。"""
    out: list[str] = []
    if signals.beats >= MIN_SHOTS_FOR_CARD:
        out.append(f"剧本里有 {signals.beats} 个节拍，预计至少 {MIN_SHOTS_FOR_CARD} 个镜号")
    if signals.max_cast >= MIN_CAST_FOR_CARD:
        out.append(f"单场最多 {signals.max_cast} 人同时在场")
    if (
        signals.script_scenes >= MIN_SCRIPT_SCENES_FOR_MOVEMENT
        or signals.action_beats >= MIN_ACTION_BEATS_FOR_MOVEMENT
    ):
        out.append(f"{signals.script_scenes} 场戏、{signals.action_beats} 个动作节拍，存在位移")
    return out


def anchor_cards(state: dict[str, Any]) -> list[AnchorCard]:
    """门③ 要一次性展示的全部场景。

    **返回全部场景，不只是要出卡的那些**：调用方要能同时说清"这几个出卡、
    这几个用内联描述"。ADR-037 禁止的是逐个确认，不是不给用户看全景。

    顺序按场景档案里的顺序，不排序——那是模型按剧本地点出现顺序给的，
    用户在门③ 上看到的排列和他读剧本的顺序一致。
    """
    signals = _signals_by_location(state)
    cards: list[AnchorCard] = []

    for scene in state.get("scenes", {}).get("scenes", []):
        if not isinstance(scene, dict):
            continue
        ref = str(scene.get("ref", "")).strip()
        if not ref:
            continue
        name = str(scene.get("name", ref)).strip()
        # 场景档案的 name 就是剧本里的地点名（`_input_for` 把地点清单发给了
        # 场景 Agent）。对不上时按空信号处理——判不出来就不出卡，
        # 而不是凭空造一张没有依据的卡。
        sig = signals.get(name, AnchorSignals())
        axis = scene.get("camera_axis")
        cards.append(
            AnchorCard(
                ref=ref,
                name=name,
                camera_axis={
                    k: str(v) for k, v in (axis if isinstance(axis, dict) else {}).items()
                },
                fixed_references=_fixed_references(scene),
                reasons=_reasons(sig),
                signals=sig,
            )
        )
    return cards


def gate_payload(state: dict[str, Any]) -> dict[str, Any]:
    """门③ 的摘要。出卡的和不出卡的分开列，一屏看完。"""
    cards = anchor_cards(state)
    needed = [c for c in cards if c.needs_card]
    return {
        "scenes_total": len(cards),
        "cards": [c.as_dict() for c in needed],
        # 不出卡的只给名字：它们走内联描述，用户不需要在门③ 上逐个看空间关系
        "inline": [{"ref": c.ref, "name": c.name} for c in cards if not c.needs_card],
        # 出了卡但锚点字段是空的。这是"确认了一张空卡"的唯一预警。
        "incomplete_refs": [c.ref for c in needed if c.incomplete],
        # 判据是从剧本推的下界，不是分镜实测值。前端要如实说明，
        # 否则用户会以为这些数字就是最终镜号数。
        "criteria_source": "screenplay",
    }


def reconcile_after_storyboard(state: dict[str, Any]) -> list[dict[str, Any]]:
    """分镜跑完之后用**真判据**重算，列出门③ 上漏掉的场景。

    这是上面那个下界估计的残差补偿，挂在门④ 的摘要里。返回空列表是常态，
    也是预期——真判据比剧本估计更宽松的情况应该很少，多了说明剧本的
    节拍粒度太粗，那是另一个问题。
    """
    shots = [s for s in state.get("storyboard", {}).get("shots", []) if isinstance(s, dict)]
    if not shots:
        return []

    by_ref: dict[str, list[dict[str, Any]]] = {}
    for shot in shots:
        if ref := str(shot.get("scene_ref", "")).strip():
            by_ref.setdefault(ref, []).append(shot)

    already = {c.ref for c in anchor_cards(state) if c.needs_card}
    known = {
        str(s.get("ref", "")).strip(): str(s.get("name", ""))
        for s in state.get("scenes", {}).get("scenes", [])
        if isinstance(s, dict)
    }

    gaps: list[dict[str, Any]] = []
    for ref, rows in by_ref.items():
        if ref in already or ref not in known:
            continue
        max_cast = max(
            (len([r for r in s.get("character_refs", []) or [] if str(r).strip()]) for s in rows),
            default=0,
        )
        moves = sum(1 for s in rows if str(s.get("camera_move", "")).strip())
        reasons: list[str] = []
        if len(rows) >= MIN_SHOTS_FOR_CARD:
            reasons.append(f"实际拆出 {len(rows)} 个镜号")
        if max_cast >= MIN_CAST_FOR_CARD:
            reasons.append(f"单镜最多 {max_cast} 人同框")
        if moves >= MIN_ACTION_BEATS_FOR_MOVEMENT:
            reasons.append(f"{moves} 个镜头有运镜，存在位移")
        if reasons:
            gaps.append({"ref": ref, "name": known[ref], "reasons": reasons})

    return sorted(gaps, key=lambda g: str(g["ref"]))
