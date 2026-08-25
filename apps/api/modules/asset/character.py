"""从一段参考描述直接生成角色档案。

用户手里常常先有一个角色，而不是先有一部小说：一段设定、一段外貌描写、
一句"我要这样一个反派"。让他必须先建项目、传原文、跑完路由和剧本才能拿到
一份角色档案，是把系统的编排顺序当成了用户的创作顺序。

三个决定：

**复用 `visual.character.v1`，不新增一个"简易版角色 Agent"。**
输入形态不同（参考描述 vs 剧本），但要产出的东西完全一样——同一个
`CharacterSheets` schema、同一套"先分析性格再推导外观""禁止写画风"的规则。
两份相似的角色提示词一定会分叉：改了一处忘了另一处，两条入口生成的角色
就不是一个标准，而这正是 ADR-020 拆分三份提示词时明确要避免的失败模式
（拆开的是**职责不同**的提示词，不是同一职责的两个副本）。输入形态的差异
用 `system_suffix` 抹平——`runner.run_agent` 的文档里写明了这个参数就是
用来"改变任务性质而不换 Agent"的，聊天修订用的也是它。

**产出存进 `character_entries`，不存 `agent_runs`。**
那张表的 `project_id` 是 NOT NULL，而这条路径压根没有项目。把它改成可空
会波及所有"按项目查档案"的代码，为一个新入口去松一条被四处依赖的约束，
风险远大于多一张表。

**走正常的 Credits 预扣/结算。** 这是一次真实的 LLM 调用，成本和编排链路里
那次一模一样。不计费的入口迟早会被当成免费通道刷。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.agent import service as agent_service
from apps.api.modules.asset import repository as repo
from apps.api.modules.asset.models import CharacterEntry
from apps.api.modules.billing import pricing
from apps.api.modules.billing import service as billing

log = get_logger(__name__)

#: 复用编排链路上的同一个角色 Agent。钉死到具体 id 而不是按 role 取默认——
#: 同一个 role 下有多个 Agent，`default_for` 只能取一个（见 orchestrator._SPEC_OF）。
CHARACTER_AGENT_ID = "visual.character.v1"

#: 参考描述的长度上限。比修订指令的 2000 字宽（那是"改哪里"，这是一份完整设定），
#: 对齐编排链路里喂给视觉 Agent 的素材节选长度（orchestrator.SOURCE_EXCERPT_CHARS），
#: 让两条入口的输入规模保持在同一量级。
MAX_REFERENCE_CHARS = 6000

#: 这笔账挂在哪种业务对象上。流水里写 "task" 会指向一个不存在的 task 行，
#: 对账时查不下去。
_REF_TYPE = "character_entry"

# 输入形态的差异全部由这段承担：Agent 的提示词说的是"扫描剧本全文"，
# 而这里根本没有剧本。不明说的话，模型会去找剧情、找场次、找它以为
# 应该存在的其他角色，然后凭空编出来一堆。
REFERENCE_CONTRACT = """
---
本次输入**不是剧本**，而是用户直接写的一段角色参考描述。

- 只为描述里真实出现的角色建档，**不要虚构额外角色**来凑数。
  用户写了一个角色就产出一个，写了三个就产出三个。
- 描述没写到的字段照常按性格与处境推导，并把推导出来的字段名列进 inferred，
  规则与从剧本建档时完全一致。
- 没有剧情上下文时，`present_state` 写这个角色最具代表性的状态，
  不要为了填满字段而编造情节。
"""


@dataclass(frozen=True, slots=True)
class GeneratedCharacter:
    entry: CharacterEntry
    reserved: int
    cost: int


def build_reference_input(description: str) -> str:
    """参考描述的用户消息。

    结构照抄修订那条路径的教训（`agent/revise.py` 的 build_revise_input）：
    **要求放在前面，素材放在中间，末句重申要做什么**。模型对最后一句的
    服从度最高，用素材收尾会让它把素材当成待复述的内容。
    """
    return (
        "【角色参考描述】\n"
        f"{description}\n\n"
        "请只依据上面这段描述建立角色视觉档案，不要虚构描述里没有的角色。"
    )


def title_of(output: dict[str, object]) -> str:
    """列表标题：第一个角色名，多角色时标出总数。"""
    characters = output.get("characters")
    names = (
        [str(c.get("name") or "").strip() for c in characters if isinstance(c, dict)]
        if isinstance(characters, list)
        else []
    )
    names = [n for n in names if n]
    if not names:
        return "未命名角色"
    if len(names) == 1:
        return names[0][:120]
    return f"{names[0]} 等 {len(names)} 个角色"[:120]


async def generate_from_reference(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    description: str,
) -> GeneratedCharacter:
    description = description.strip()
    if not description:
        raise AppError("common.validation_failed", message="角色描述不能为空")
    if len(description) > MAX_REFERENCE_CHARS:
        raise AppError(
            "common.validation_failed",
            message=f"角色描述最长 {MAX_REFERENCE_CHARS} 字",
            detail={"max_chars": MAX_REFERENCE_CHARS, "got": len(description)},
        )

    spec = agent_service.get_spec(CHARACTER_AGENT_ID)

    # 条目 id 先生成：预扣要挂在它上面，而预扣必须发生在调模型之前。
    # 跑完再扣的话，余额不足时钱已经花在上游了。
    entry_id = uuid.uuid4()
    amount = await pricing.estimate_agent_run(db, budget_credits=spec.budget_credits, org_id=org_id)
    # attempt 用默认的 0：这条路径没有重试概念，一次调用一条账。
    # 预扣与结算必须用同一个编号，差一位会导致"跑成功了但钱没扣"。
    await billing.reserve(db, org_id=org_id, amount=amount, task_id=entry_id, ref_type=_REF_TYPE)

    try:
        completion = await agent_service.generate_structured(
            agent_id=CHARACTER_AGENT_ID,
            user_input=build_reference_input(description),
            system_suffix=REFERENCE_CONTRACT,
        )
    except AppError:
        # 失败不收钱：预扣原样退回。不退的话用户余额里会挂着一笔
        # 永远不会结算的钱，而他什么都没拿到。
        await billing.release(db, org_id=org_id, task_id=entry_id, ref_type=_REF_TYPE)
        raise

    output = completion.output.model_dump(mode="json")
    cost = await pricing.text_run_cost(
        db, model_id=completion.model_id, tokens_out=completion.tokens_out, org_id=org_id
    )
    await billing.settle(db, org_id=org_id, task_id=entry_id, actual_cost=cost, ref_type=_REF_TYPE)

    entry = await repo.create_character_entry(
        db,
        org_id=org_id,
        owner_user_id=owner_user_id,
        entry_id=entry_id,
        title=title_of(output),
        source_text=description,
        agent_id=CHARACTER_AGENT_ID,
        output_json=output,
        resolved_prompt=completion.system_prompt,
        model_id=completion.model_id,
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        cost=cost,
    )
    await db.commit()

    log.info(
        "asset.character_entry_created",
        entry_id=str(entry_id),
        model_id=completion.model_id,
        reserved=amount,
        cost=cost,
    )
    return GeneratedCharacter(entry=entry, reserved=amount, cost=cost)


async def list_entries(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID, limit: int = 100
) -> list[CharacterEntry]:
    return await repo.list_character_entries(
        db, org_id=org_id, owner_user_id=owner_user_id, limit=limit
    )


async def get_entry(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID, entry_id: uuid.UUID
) -> CharacterEntry:
    entry = await repo.get_character_entry(db, org_id=org_id, entry_id=entry_id)
    # 跨租户与跨用户都返回 404，不返回 403
    if entry is None or entry.owner_user_id != owner_user_id:
        raise AppError("common.not_found", message=f"character entry {entry_id}")
    return entry


async def delete_entry(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID, entry_id: uuid.UUID
) -> None:
    entry = await get_entry(db, org_id=org_id, owner_user_id=owner_user_id, entry_id=entry_id)
    await repo.delete_folder_item(db, org_id=org_id, item_type="character", item_id=entry_id)
    await repo.soft_delete_character_entry(db, entry)
    await db.commit()
