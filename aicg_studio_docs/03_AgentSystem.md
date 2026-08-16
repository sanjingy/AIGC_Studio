# Agent 系统设计

## 1. Agent 层级

```text
User
 |
v
Router Agent
 |
v
Director Agent
 |
+-------------------------------+
|   Specialist Agents           |
|                               |
| Story                         |
| Character                     |
| World/Scene                   |
| Script                        |
| Storyboard                    |
| Prompt                        |
| Image                         |
| Video                         |
| Audio                         |
| Editing                       |
| QA                            |
+-------------------------------+
 |
v
Tools / Skills / Providers / Runtimes
```

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
  "estimated_credit_range": [2000, 5000],
  "requires_clarification": false
}
```

Router 必须结构化输出，禁止让下游解析自然语言。

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
Story + Character + Scene + Storyboard + Image + Video + Audio + Edit + QA
```

知识解说 Skill：

```text
Story + Script + Research + Voice + Visual + Edit + QA
```

这样 Skill 真正成为“生产模板”，而不是 Prompt 集合。
