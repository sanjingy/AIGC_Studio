"""成品提示词与生成记录的对外形状。

`run_id` / `id` 一律用字符串而不是 UUID 类型：这两个形状是前端与后端一起
钉死的契约（`project_docs/plans/2026-09-11_abc_workbench_and_prompt_alignment.md`
§7 的 D1→D2 依赖），前端拿到的就是字符串，不需要再区分 UUID 与别的 id。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PromptIn(BaseModel):
    """准备一份提示词时能带的东西。**只有创作要求，没有风格词。**

    风格词由系统按项目锁定的画风注入并校验是否被原样保留（ADR-036），
    前端传不了也不该传。这里能传的只有"这次我还想要什么"——
    比如"让他侧身一点"。
    """

    instruction: str = Field(default="", max_length=2000)


class PromptOut(BaseModel):
    """一份准备好的提示词。"""

    run_id: str
    #: character / scene / shot_image / shot_video
    kind: str
    #: 角色或场景的 ref；镜头是镜号的十进制字符串
    subject_key: str
    #: 完整最终提示词。**存档全文**，不是由当前档案重新拼出来的
    prompt: str
    negative_prompt: str
    #: 依据的上下文摘要。用它判断这份词是不是还对得上现在的内容
    basis_digest: str
    #: 上游内容已经变过了。旧词旧图都留着，不静默重算也不静默沿用
    stale: bool
    #: 按哪一版模板与校验规则生成的
    rule_version: str
    agent_id: str
    #: 模型没回报时为 null，不填一个猜的
    model_id: str | None
    created_at: datetime


class GenerationRecordSummary(BaseModel):
    """生成记录列表的一行。**不含任何全文**——全文只在详情里取。"""

    id: str
    #: agent（一次推理）/ image（一次出图）
    record_type: str
    subject_kind: str | None
    subject_key: str | None
    title: str
    status: str
    agent_id: str | None
    model_id: str | None
    #: 出图的来源：api（平台 Provider）/ local（用户自己电脑上的 Codex）。
    #: 文本生成没有落这一列，为 null——不猜，界面显示"未记录"
    source: str | None
    created_at: datetime
    finished_at: datetime | None
    error_code: str | None
    asset_ids: list[str] = Field(default_factory=list)


class GenerationRecordStep(BaseModel):
    """一次尝试。系统提示词与堆栈不在这里——那是平台内部变量。"""

    index: int
    kind: str
    duration_ms: int
    error: str | None
    raw_output: str | None


class GenerationRecordDetail(GenerationRecordSummary):
    """一条生成记录的全文。"""

    user_input: str | None
    prompt: str | None
    negative_prompt: str | None
    #: 上游改写后的提示词。**没回报就是 null**，不拿请求词冒充回传
    actual_prompt: str | None
    rule_version: str | None
    basis_digest: str | None
    #: 这条记录证明不了自己是完整的（历史截断，或者不是新链路产出的）。
    #: 补不回来的东西就如实说不完整，不重新拼一段冒充当时的词
    incomplete: bool
    steps: list[GenerationRecordStep] = Field(default_factory=list)
    output: dict[str, object] | None
