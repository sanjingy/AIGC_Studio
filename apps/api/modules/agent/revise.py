"""聊天修订：用一句话改掉某个阶段的产出。

三条规则决定了这里的所有设计：

1. **聊天是输入，不是真相。** 对话存进 `conversation_messages`，
   状态仍只认 `projects.current_state_json`（ADR-008）。
2. **修订产出必须还是同一个 schema。** 走同一个 Agent、同一个
   `output_schema`，不因为"是聊天"就退化成自由文本——退化了下游全崩。
3. **局部改，不是整体重跑。** 用户说"把二号角色改成女的"，
   不该顺手把他已经满意的其他角色也重写一遍。做法是把当前产出整份
   喂回去并要求只改相关部分，然后 **diff 出实际改动范围给用户看**——
   光靠提示词约束是不够的，得让用户能一眼看出它多改了什么。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agents import registry
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.agent import repository as repo
from apps.api.modules.agent import runner
from apps.api.modules.project import service as project_service

log = get_logger(__name__)

# 可修订的阶段产出，以及它们的上下游顺序。
# 顺序用来判断"改了上游，下游是不是过期了"。
REVISABLE_ROLES = ("story", "visual")

MAX_INSTRUCTION_CHARS = 2000

# diff 报告的条目上限。改了 200 个镜头时列 200 条没人会看，
# 只会把真正重要的那几条淹掉。
_MAX_DIFF_ITEMS = 12


@dataclass(frozen=True, slots=True)
class ReviseResult:
    target_role: str
    revision: int
    changed_fields: list[str]
    output: dict[str, Any]
    run_id: uuid.UUID
    stale_roles: list[str]


def _identify(item: Any, index: int) -> str:
    """给列表元素找一个人能认出来的标识。"""
    if isinstance(item, dict):
        for key in ("ref", "name", "index", "id"):
            if key in item:
                return str(item[key])
    return str(index)


def _diff_list(key: str, old: list[Any], new: list[Any]) -> list[str]:
    old_map = {_identify(v, i): v for i, v in enumerate(old)}
    new_map = {_identify(v, i): v for i, v in enumerate(new)}

    out: list[str] = []
    for ident in new_map.keys() - old_map.keys():
        out.append(f"{key}[{ident}] 新增")
    for ident in old_map.keys() - new_map.keys():
        out.append(f"{key}[{ident}] 删除")
    out.extend(
        f"{key}[{ident}]"
        for ident in old_map.keys() & new_map.keys()
        if old_map[ident] != new_map[ident]
    )
    return sorted(out)


def diff_fields(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """列出实际改了什么。

    这是"局部改"这条规则的**验证手段**，不是展示装饰。
    模型说它只改了一处，diff 说它改了七处，以 diff 为准。
    """
    changed: list[str] = []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if before == after:
            continue
        if isinstance(before, list) and isinstance(after, list):
            changed.extend(_diff_list(key, before, after))
        else:
            changed.append(key)

    if len(changed) > _MAX_DIFF_ITEMS:
        head = changed[:_MAX_DIFF_ITEMS]
        head.append(f"…另有 {len(changed) - _MAX_DIFF_ITEMS} 处")
        return head
    return changed


def downstream_of(role: str) -> list[str]:
    idx = REVISABLE_ROLES.index(role)
    return list(REVISABLE_ROLES[idx + 1 :])


# 修订的角色指令：告诉模型这次是改稿不是创作。
#
# **单靠它不足以修复"原样返回"**。2026-08-18 对照实验（deepseek-chat，
# 同一份产出、同一条要求，各跑一次）：
#
#     A 旧消息 + 无契约  → 原样返回，0 处改动   ← 上线时的版本
#     B 新消息 + 无契约  → 6 处改动 ✅
#     C 旧消息 + 有契约  → 原样返回，0 处改动
#     D 新消息 + 有契约  → 6 处改动 ✅
#
# 决定性的是用户消息的结构（见 build_revise_input），不是这段契约。
# 保留它是因为它把任务性质说对了，对其他模型可能有用——
# 但不要以为有了它就能随便改消息结构。
REVISE_CONTRACT = """
---
本次任务是**修订已有产出**，不是重新创作。

用户会给你一份【当前产出】和一条【修改要求】。规则：

- 修改要求的优先级**高于**当前产出里的一切内容。两者冲突时以修改要求为准。
- 修改要求没有涉及的字段原样保留，包括未提及的角色、场景、镜头及其 ref/index。
- 改动要前后自洽：改了设定，被它影响的描述也要跟着改。
- **禁止原样返回当前产出。** 修改要求里明确点名的改动必须落实，
  即使你认为原产出已经够好。
"""


def build_revise_input(current: dict[str, Any], instruction: str) -> str:
    """修订请求的用户消息。

    当前产出整份带上，而不是只带被改的那一块：模型需要上下文才能
    保证改动前后自洽（改了角色性别，台词里的称呼也得跟着改）。

    **结构是这里唯一重要的东西，不要"顺手优化"。**

    上线的第一版把「其余字段原样保留」放在了整条消息的最后一句，
    结果模型对着"故事完全不对，请重写"这样的要求原样返回了当前产出，
    一个字没改。原因很直白：两条指令冲突时，靠后的那条赢——
    而"保留"恰好排在"重写"后面。

    所以现在的顺序是固定的：

        修改要求 → 当前产出 → 重申"以修改要求为准"

    末句必须重申修改要求的优先级，**不能以"原样保留"收尾**。
    "只改相关部分"这层意思放在系统契约里（REVISE_CONTRACT），
    它在系统提示词中，不会和用户的具体要求抢最后一句的位置。
    """
    return (
        f"【修改要求】\n{instruction}\n\n"
        f"【当前产出】\n{json.dumps(current, ensure_ascii=False)}\n\n"
        "请按上面的【修改要求】修改【当前产出】，输出修改后的完整 JSON。"
        "修改要求与当前产出冲突时，以修改要求为准。"
    )


async def revise(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    target_role: str,
    instruction: str,
) -> ReviseResult:
    if target_role not in REVISABLE_ROLES:
        raise AppError(
            "common.validation_failed",
            message=f"{target_role} 的产出不支持聊天修订",
            detail={"revisable": list(REVISABLE_ROLES)},
        )
    instruction = instruction.strip()
    if not instruction:
        raise AppError("common.validation_failed", message="修改要求不能为空")
    if len(instruction) > MAX_INSTRUCTION_CHARS:
        raise AppError(
            "common.validation_failed",
            message=f"修改要求最长 {MAX_INSTRUCTION_CHARS} 字",
        )

    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    state: dict[str, Any] = dict(project.current_state_json or {})
    current = state.get(target_role)
    if not isinstance(current, dict) or not current:
        raise AppError(
            "common.not_found",
            message=f"还没有 {target_role} 的产出，先生成一版再改",
        )

    spec = registry.default_for(target_role)

    # 先把用户这句话记下来再去跑模型。跑失败时对话里也该留着
    # "我提过这个要求"，否则用户会以为自己没发出去。
    revision = await repo.next_revision(db, org_id=org_id, project_id=project_id, role=target_role)
    await repo.add_message(
        db,
        org_id=org_id,
        project_id=project_id,
        target_role=target_role,
        author="user",
        text=instruction,
        revision=revision,
    )
    await db.commit()

    result = await runner.run_agent(
        db,
        org_id=org_id,
        project_id=project_id,
        spec=spec,
        user_input=build_revise_input(current, instruction),
        variables=_variables_for(state),
        system_suffix=REVISE_CONTRACT,
    )
    output = result.output.model_dump(mode="json")
    changed = diff_fields(current, output)

    state[target_role] = output
    # 整体重新赋值，不原地改字典——JSONB 原地修改 SQLAlchemy 检测不到，
    # 会静默不写库，表现为"改了但没变"。
    project.current_state_json = dict(state)

    stale = [r for r in downstream_of(target_role) if r in state]
    await repo.add_message(
        db,
        org_id=org_id,
        project_id=project_id,
        target_role=target_role,
        author="assistant",
        text=_summarize(changed, stale),
        run_id=result.run_id,
        revision=revision,
        changed_fields=changed,
    )
    await db.commit()

    log.info(
        "agent.revised",
        project_id=str(project_id),
        role=target_role,
        revision=revision,
        changed=len(changed),
    )
    return ReviseResult(
        target_role=target_role,
        revision=revision,
        changed_fields=changed,
        output=output,
        run_id=result.run_id,
        stale_roles=stale,
    )


def _summarize(changed: list[str], stale: list[str]) -> str:
    if not changed:
        # 不要把原因归给用户的措辞。产出与上一版完全一致，最常见的原因是
        # 模型把当前产出原样吐了回来——那是我们的问题，不是用户没说清。
        head = "产出与上一版完全一致，这次修改没有生效。再发一次，或把要求说得更具体。"
    else:
        head = f"已修改 {len(changed)} 处：{'、'.join(changed)}"
    if stale:
        head += f"。下游产出（{'、'.join(stale)}）是基于旧版生成的，需要重新生成才会同步。"
    return head


def _variables_for(state: dict[str, Any]) -> dict[str, Any]:
    router = state.get("router", {})
    return {
        "target_duration_seconds": router.get("estimated_duration_seconds", 60),
        "target_shots": router.get("estimated_shots", 12),
    }
