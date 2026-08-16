# Agent 系统设计

> 2026-08-16 修订：Specialist Agent 由 11 个收敛为 4 个（ADR-012 相关），
> 并修正执行顺序为**音频优先**（ADR-011）。
>
> 原因：11 个 Agent 意味着 11 套提示词、11 套输出 schema、11 处退化风险，
> 而它们中的大多数本质上在做同一件事（把上文结构化地变成下文）。
> 拆分应该按"输出物形态"而非"职能名称"。

## 1. Agent 层级

```text
User
 │
 ▼
Router Agent            分类与路由
 │
 ▼
Director Agent          编排与调度
 │
 ├─ Story Agent         故事结构 / 剧本 / 台词
 ├─ Visual Agent        角色 / 场景 / 分镜 / 镜头提示词
 ├─ Media Agent         TTS / 图像 / 视频 生成调度
 └─ QA Agent            质量校验（M1 为规则校验，非 LLM）
 │
 ▼
Tools / Skills / Providers / Runtimes
 │
 ▼
Consistency Engine  +  Timeline Assembler
```

### 收敛映射

| 原 Agent | 现归属 |
|---|---|
| Story / Script | Story Agent |
| Character / World-Scene / Storyboard / Prompt | Visual Agent |
| Image / Video / Audio | Media Agent |
| Editing | 不是 Agent —— 是 `timeline/` 确定性代码 |
| QA | QA Agent（M1 为规则校验） |

**Editing 不该是 Agent**：时间线装配是确定性计算
（按音频时长排布片段），用 LLM 做是把可靠的事情变得不可靠。

**QA 在 M1 不用 LLM**：一致性分数、时长偏差、字幕对齐都是可计算指标
（见 `17_ConsistencyEngine.md` 第 6 节），规则判断更快更准更便宜。

## 2. Router Agent

### 输入

```json
{
  "user_prompt": "把这篇小说做成悬疑漫剧",
  "attachments": [],
  "user_preferences": {},
  "available_skills": []
}
```

### 输出

```json
{
  "route": "NOVEL_TO_ANIME",
  "confidence": 0.96,
  "recommended_skill_ids": ["skill_novel_anime_default"],
  "recommended_director": "director_anime",
  "estimated_duration_seconds": 300,
  "estimated_credit_range": [23000, 40000],
  "estimated_tier": "standard",
  "requires_clarification": false
}
```

Router 必须结构化输出，禁止让下游解析自然语言。

> **注意 credit 区间的量级**：原文档此处写 `[2000, 5000]`，
> 低估了 6–15 倍。区间必须由 `19_UnitEconomics.md` 第 6 节的估价公式计算，
> **不允许由 LLM 自行估算**——Router 只输出生产参数（镜头数、时长、档位），
> 金额由确定性代码算出后回填。

## 3. Director Agent

Director 使用项目状态作为长期记忆：

```text
project_state
  ├── route
  ├── skill
  ├── story
  ├── characters
  ├── scenes
  ├── script
  ├── storyboard
  ├── assets
  ├── tasks
  ├── approvals
  └── production_policy
```

## 4. Agent 状态机

```text
INIT
 ↓
ROUTING
 ↓
PLAN_READY
 ↓
WAIT_USER
 ↓
EXECUTING
 ↓
REVIEW
 ├── APPROVED → NEXT
 ├── EDIT → REPLAN
 ├── REGENERATE → RETRY
 └── REJECT → ROLLBACK
 ↓
COMPLETED
```

> **状态持久化约定（ADR-008）**：上述状态机的当前位置由
> `projects.current_state_json` + `tasks.status` + `approvals` 共同决定，
> **不由 LangGraph checkpoint 决定**。
> Director 每次被唤醒时从数据库重新计算"下一步是什么"，
> 不依赖内存中的图状态。这样进程重启、Worker 崩溃、用户隔天回来续做，
> 行为完全一致。

## 4.1 执行顺序：音频优先（ADR-011）

```text
剧本定稿
   ↓
台词/旁白拆分到镜头
   ↓
★ TTS 合成 → 得到每句【真实音频时长】
   ↓
反推每个镜头的目标时长
   ↓
关键帧生成
   ↓
图生视频（按目标时长切段）
   ↓
时间线装配 → ffmpeg 渲染
```

**Media Agent 内部必须先跑 TTS，再跑图像与视频。**
先出视频再配音会必然音画错位：视频片段时长固定（5s/10s），
台词时长不固定，事后拉伸变形、裁剪丢内容。

## 5. Agent Tool

每个 Agent 只能通过 Tool 做实际操作，例如：

```text
create_character
update_character
generate_image
queue_comfy_workflow
submit_video_job
create_task
search_skill
resolve_model
request_approval
create_asset
publish_version
```

Agent 不应该直接操作数据库。

## 6. Agent 可靠性

必须有：

- max_steps
- timeout
- retry
- idempotency_key
- structured output validation
- tool permission
- budget limit
- human approval
- audit log

## 7. Director 选择 Skill

优先级：

```text
用户明确指定 Skill
    >
项目指定 Skill
    >
Router 推荐 Skill
    >
Director 默认 Skill
```

## 8. Director 选择 Agent

Director 根据 Skill 声明动态生成 agent graph。

示例：

```text
小说漫剧 Skill
=
Story → Visual → [Consistency 冻结] → Media(TTS→图→视频) → [Timeline] → QA
```

知识解说 Skill：

```text
Story → Visual(简化) → Media(TTS→图) → [Timeline] → QA
```

方括号内的是**确定性模块，不是 Agent**。

这样 Skill 真正成为“生产模板”，而不是 Prompt 集合。

## 9. Agent 质量回归

Agent 的提示词改动不会让单元测试变红，但会让质量悄悄退化。
**每个 Agent 必须在 `evals/` 下有对应的黄金测试集**，
详见 `22_AgentEval.md`。没有回归集的 Agent 不允许上线。
