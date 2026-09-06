# 04 Agent 编排与人工审核

> 状态：**部分实现**（文本链路编排已实现；媒体编排、计费接线、Skill 驱动未实现）
> 优先级：P0
> 负责人：待定
> 最近核对：2026-09-06（ADR-037 四道门落地）

## 1. 模块目标与边界

把"用户想要一部片子"变成一串可执行、可审计、可在关键点停下来等人的生产步骤：
决定下一步跑哪个 Agent、拼输入、调 Gateway、校验结构化输出、写产出、开门等确认。

边界：

- 本模块回答"**为什么跑这一步、跑出了什么**"；`tasks` 回答"**现在是否在执行**"（ADR-008）。
  两者不互相覆盖。
- 本模块**不选具体模型**（ADR-002）。模型由 Gateway 按三层偏好解析（ADR-031）。
- 本模块**不写风格词**（一致性模块的硬规则，见 06）。
- Agent 的产出正文归 03；本模块只负责生产它。

## 2. 角色与阶段

代码里真实存在的 Agent（`agents/builtin/`，10 份 YAML）：

| Agent id | 角色 | 在主链路上 |
|---|---|---|
| `router.default.v1` | Router：判路线、判信息是否足够 | 是 |
| `story.plot_index.v1` | 情节目录 | 是 |
| `story.screenplay.v1` | 剧本（集 → 场 → 节拍 → 台词） | 是 |
| `visual.character.v1` | 角色档案 | 是 |
| `visual.scene.v1` | 场景档案 | 是 |
| `visual.storyboard.v1` | 分镜表 | 是 |
| `story.default.v1` / `visual.default.v1` | 旧的两步式产出 | 否，留作 role 兜底 |
| `director.default.v1` | Director | 否，`_NEXT` 是硬编码的，Director 不参与决策 |
| `qa.default.v1` | QA | 否，无调用方 |

阶段图（`orchestrator._NEXT`，写成数据不是 if/else，"下一步是什么"只有一处答案）：

```text
routing → plot_index → await_plan → screenplay → await_setup →
characters → scenes → await_anchors → storyboard → await_storyboard → done
```

2026-09-05（ADR-037）由两道门改为**四道**，新增 `await_plan` 与 `await_anchors`。
两个新阶段是**插在既有阶段之间的新值**，没有任何阶段名作废，存量项目的 stage
一个都没变、`_NEXT[stage]` 全部仍然命中——所以这一次**不需要**往
`_LEGACY_STAGES` 里加东西（那张表仍然只翻译 2026-08-18 那次的 `story` / `visual`）。
要迁移的是**数据**，见 §6 的 `project_lock_variables`。

四道门各自确认什么：

| 阶段 | 门 | 确认什么 | 打回退到 |
|---|---|---|---|
| `await_plan` | `plan` | 情节目录全量 / 时代背景与人种 / 画风 / 改编模式 | `plot_index` |
| `await_setup` | `setup` | 剧本（行为与改动前一字不差） | `screenplay` |
| `await_anchors` | `anchors` | 全部场景的空间锚点，**一次性**确认 | `scenes` |
| `await_storyboard` | `storyboard` | 分镜表（行为与改动前一字不差） | `storyboard` |

门① 一次问四件事而不是拆成四道门：这四件事**在信息上是同时可决的**，
都只依赖原文与情节目录、不依赖彼此，拆开只增加点击不增加判断质量。
它必须开在 `screenplay` **之前**，因为改编模式决定剧本怎么写——
等剧本写完再问，用户改一下就要把整份剧本重写一遍，那笔钱已经花了。

## 3. 当前真实能力

### 3.1 已实现

| 能力 | 证据 |
|---|---|
| Agent 注册表：YAML spec 加载、按 id 取、热重载 | `agents/registry.py`；`POST /api/v1/agents/reload` |
| 声明式 spec + Pydantic `output_schema`，第三方只能是 YAML 不能是代码 | `agents/spec.py`、`agents/schemas.py`；`tests/unit/test_agent_spec_sandbox.py` |
| 阶段推进 `advance`：拼输入 → 跑 Agent → 校验 → 写 `current_state_json` → 推进阶段 | `orchestrator.advance` |
| 结构化输出校验失败自动重试（`spec.schema_retries`），每次尝试落 `agent_steps` | `runner.py` 的 `for attempt in range(spec.schema_retries + 1)` |
| 每次运行记录 `agent_runs`（agent_id、role、model_id、tokens_in/out、attempts、error_code）与 `agent_steps`（`resolved_prompt` 全文、`raw_output`、耗时） | `apps/api/modules/agent/models.py`、`repository.py` |
| **四道**阻塞审核门：`plan` / `setup` / `anchors` / `storyboard` | `orchestrator._GATE_OF`；`tests/integration/test_four_gates.py` |
| 门被打回时退回上一个生产阶段重做 | `orchestrator._REDO_FROM` |
| 门① 通过时在**同一个事务里**给锁定变量盖确认戳（时间 + 人 + 来源） | `resolve_gate` → `project_service.confirm_lock_variables` |
| 门③ 一次性展示全部场景，出卡的与走内联描述的分开列，并标出"卡是空的" | `agent/anchors.py::gate_payload`；`tests/unit/test_anchor_cards.py` |
| 项目级锁定变量（画风 / 时代背景与人种 / 改编模式）读写与租户隔离 | `project_lock_variables` 表；`tests/integration/test_lock_variables.py` |
| 改编模式真的注入剧本提示词（不是只存下来） | `_variables_for` 的 `adaptation_instruction`；`test_four_gates.py` |
| 时代背景判定优先级：门① 锁定 > 情节目录证据 > 题材关键词 > **要求判定的指令** | `orchestrator._era_of` / `detect_era` |
| 存量项目穷举每一种历史 stage 值仍能 `advance` | `tests/integration/test_stage_migration.py` |
| 审核决策记 `approvals`，不写进 `agent_runs`，也不改任务状态 | `models.Approval`、`orchestrator.resolve_gate` |
| 聊天式局部返工 `revise`：只重跑目标阶段，记录对话与版本号、变更字段 | `agent/revise.py`；`tests/integration/test_revise_chat.py` |
| 过期记账 `stale_roles`：改了某阶段就把下游标记为过期，存进 `current_state_json` | `orchestrator.mark_stale` / `mark_fresh` |
| Router 要求澄清时原地停住，不带着错路线往下跑 | `advance` 里的 `requires_clarification` 分支 |
| 旧阶段名迁移（`story` → `plot_index`、`visual` → `characters`） | `orchestrator._LEGACY_STAGES` |
| 角色 / 场景产出跑完自动同步进一致性档案 | `_sync_consistency` / `_sync_scene_consistency` |
| 每个 Agent 必须有 eval 覆盖，由 registry 枚举自动触发，缺了 CI 变红 | `tests/unit/test_agent_eval_required.py` |

### 3.2 部分实现

- **门的数量**：ADR-037 定的四道门已全部实现。产品文档另写过一道"成片"门，
  它仍然没有对应阶段，因为成片本身还不存在——不写它是诚实的，不是缺陷。
- **门③ 的判据用的是下界，不是实测值**。ADR-037 第 3 条的三条判据
  （≥3 个镜号 / ≥2 人同框 / 有明显位移）输入全长在分镜表上，而门③ 在分镜
  **之前**。解法是把输入换成剧本（详见 `agent/anchors.py` 顶部）：节拍数是
  镜号数的下界，误差只往"多出一张卡"的方向倒。残差由
  `reconcile_after_storyboard()` 在门④ 的摘要里补一行提示，**不加第五道门**。
- **eval**：硬地板是"结构化输出 schema 样例"（`tests/eval/eval_suite.py` 的 `SCHEMA_SAMPLES`），
  已经在 CI 上拦人。`22_AgentEval.md` §2.1 的 Router 黄金集、§2.3 的 LLM-as-judge 内容评分
  **没有实现**——`tests/eval/agent_evals/` 目录里只有一份 README。
- **QA**：`qa.default.v1` 有 spec、有 `QAReport` schema，但没有任何调用方。

### 3.3 未实现 / 与旧文档不符的事实

以下三条是本轮核对代码时发现的，旧文档写反了：

1. **`advance` 不建任务、不预扣、不结算。** `orchestrator.advance` 在 API 进程里
   **同步**调 `runner.run_agent`，全程没有 `task_service.create_task`，也没有任何
   `billing` 调用。旧文档 §5 画的 "estimate + create task + reserve credits → Worker
   invokes Gateway" 在文本链路上**不成立**——那条路径只在出图（`image.generate`）上成立。
   值得注意的是**机器已经有了**：`billing/pricing.py` 里的 `estimate_agent_run` 与
   `text_run_cost` 就是给文本 Agent 计费用的，`apps/api/modules/asset/character.py`
   （ADR-028 的参考描述生成角色）已经在用它们做 reserve/settle。缺的只是主编排这条路的接线。
2. **`agent_runs.cost` 永远是 0。** `repository.finish_run` 的参数里根本没有 `cost`，
   全仓库没有一处给它赋值。"每次 Agent 调用的成本可追溯"目前只到 token 数，不到金额。
3. **`advance` 不幂等。** 没有幂等键、没有"同阶段已在跑"的互斥，重复调用会重复跑 Agent
   并重复写产出。出图路径有 `tasks.idempotency_key`（`task/service.py`），文本路径没有。

### 3.4 预留 / 仅设计

| 项 | 状态 | 说明 |
|---|---|---|
| Director 参与决策 | 预留 | spec 在，`_NEXT` 硬编码，没有调用方 |
| Skill 运行时驱动阶段图 | 仅设计 | ADR-020、ADR-026；决策记录 §9 把它整体冻结 |
| 自动 / 半自动 / 全人工三档审查 | 仅设计 | ADR-023；代码里门是固定四道，不可配置 |
| 空间锚点卡的**可编辑层** | 未实现 | 门③ 现在只能整批通过或整批打回；单独改某一张卡的 `camera_axis` 要走 `revise` 重跑场景阶段 |
| Media / TTS / 合成的 Agent 链路 | 未实现 | 见模块 12 |

## 4. 功能需求

### P0（挡住"逐镜 MP4"）

- **FR-AGENT-001**：分镜阶段必须能拿到**当前项目的视频模型单段上限**，并据此产出每镜时长
  与段数（ADR-032 第 4 条）。这要改 `visual.storyboard.v1` 的模板变量与
  `agents/schemas.py:StoryboardShot`，并补对应 schema 样例（否则
  `test_agent_eval_required.py` 会拦下）。
- **FR-AGENT-002**：`advance` 幂等。同一项目同一阶段并发或重复推进，只产生一次生产。
  没有这一条，用户手滑双击就是一次重复的模型调用。
- **FR-AGENT-003**：文本链路接上计费。`asset/character.py` 已经给出可照抄的样板
  （`pricing.estimate_agent_run` → `billing.reserve` → 跑 Agent → `pricing.text_run_cost`
  → `billing.settle`）。要么把 `advance` 也走 `tasks` + reserve/settle（与出图对齐），
  要么至少把 `agent_runs.cost` 按 `model_pricing` 算出来写进去。当前状态下平台不知道
  一个项目的文本花了多少钱，`task_cost_cap` 这类熔断对文本也完全无效。
- **FR-AGENT-004**：Agent 失败不把内部 Prompt 或 Provider 原始错误暴露给用户，
  只给 `21_ErrorTaxonomy.md` 的错误码 + 用户话术（`apps/api/core/errors.py` 已有目录）。
- **FR-AGENT-005**：Gateway 发生 failover 时，实际使用的模型必须回写到运行记录并在前端显示
  （ADR-031 第 7 条）。`agent_runs.model_id` 这一列已经存在，前端未显示。

### P1

- **FR-AGENT-006**：Router 黄金集（`22_AgentEval.md` §2.1，50 条标注输入）进 CI，
  指标：路线准确率 ≥ 90%、追问召回率 ≥ 85%。
- **FR-AGENT-007**：内容质量评分（§2.3 的 LLM-as-judge rubric）以离线脚本形式落地，
  **不进 CI 单测**——它要真实 Provider、有成本、非确定性。
- **FR-AGENT-008**：QA Agent 接进链路，至少在分镜阶段做一次覆盖核验
  （`StoryboardNode` 的 `node_index` 就是为覆盖核验留的）。
- **FR-AGENT-009**：spec 版本化与历史回放：改了提示词后能回放旧版本的 `resolved_prompt`。
- **FR-AGENT-010**：统一 Context Builder，禁止把整个项目 JSON 无差别塞进模型
  （现在 `_input_for` 已按阶段裁剪，但没有统一的预算口径）。

### P2 / 不做

- **Skill 运行时驱动编排**：决策记录 §9 明确"整体冻结"。`_NEXT` 硬编码继续保留。
- **Director 做阶段决策**：同上，阶段图是数据不是模型的判断。
- **审查模式三档**（ADR-023）：门的数量是策略（ADR-021），但本轮不做可配置。
- **节点画布驱动编排**：决策记录 §2 明确搁置。

## 5. 核心流程与状态机

实际代码路径（不是设想）：

```text
POST /projects/{id}/advance
  → 取 project、读 current_state_json、算 current_stage
  → user_input 非空则先落 state["source"]（跑任何 Agent 之前）
  → 若当前是门：没有 pending 就建一条 approval（摘要由 _gate_summary 现算），
    返回 blocked=True
  → 否则 rewind_to_runnable（老项目缺上游产出时退回能跑的那一步）
  → 读 project_lock_variables（读不到就是 None，提示词退回按证据推）
  → runner.run_agent：拼 prompt → Gateway → 校验 → 失败重试
  → 写 state[stage] = output、mark_fresh、state["stage"] = _NEXT[stage]
  → plot_index 阶段同步时代背景判定；characters / scenes 阶段同步一致性档案
```

门的状态：`pending → approved / rejected / revision_requested`。
**审核只是决策记录，不是任务状态**，不进 `tasks.status`。

门① / 门③ 通过时额外盖一个确认戳（`confirmed_at` / `anchors_confirmed_at`），
**与阶段推进在同一个事务里提交**：分开提交会出现"阶段推进了但没记确认"
或者反过来的中间态，而这两种状态在界面上都解释不清。

`_gate_summary` 是 async 的，因为门① 的画风目录在库里而不在代码里。
两道既有门的摘要**只放数字和标题**，两道新门的摘要要放正文：门① 上画风目录
与改编模式的可选项前端没有别处能读到，门③ 的卡片内容就是那道门的正文。

## 6. 数据模型

| 表 | 内容 |
|---|---|
| `agent_runs` | 一次 Agent 调用：agent_id、role、status、input/output JSON、model_id、tokens、attempts、error_code、finished_at。`cost` 列存在但恒为 0（见 §3.3） |
| `agent_steps` | 单次模型调用：`resolved_prompt` 全文、`raw_output`、error、耗时 |
| `conversation_messages` | 聊天修订的输入与结果版本号；**不参与任何状态判断** |
| `approvals` | 门、payload、决策、评论、操作者 |
| `project_lock_variables` | 项目级锁定变量：`style_key` / `era` / `region` / `ethnicity` / `era_evidence` / `adaptation_mode` / `origin` / `confirmed_at` / `confirmed_by` / `anchors_confirmed_at`。一个项目一行（唯一索引） |
| `style_catalog` | 可选画风目录，**全局表不带 org_id**（同 `model_pricing`）。每条三套描述词，见模块 06 |

不在 Agent 进程内存里维护跨请求的图状态。阶段真相在 `projects.current_state_json`。

**锁定变量为什么不放进 `current_state_json`**：两者生命周期不同。
`current_state_json` 是"跑到哪了 + 每一步的产出"，会被打回、重跑、字段级编辑
整份换掉；锁定变量是"这个项目是什么"，一旦确认就要在所有重跑之间存活。
塞进同一份 JSONB 里，一次 `changes_requested` 重跑就可能把用户确认过的画风
连带冲掉，而且"确认过没有"这件事没有地方记时间和人。

**存量项目怎么处理（ADR-037 第 6 条）**：已经越过门① 位置的项目
**不得被退回去重新确认**。迁移 `e4b7c9d21f38` 给它们补一行
`origin='migrated'`、`confirmed_at` 留空的锁定变量；画风取目录缺省项，
时代背景**故意留空**（留空在界面上显示成"未判定"，填一个"现代中国"
会显示成一个看起来已经想好了的答案，那正是 ADR-037 第 2 条禁止的）。
迁移之后、部署之前建的项目会漏网，运行时在 `confirm_anchors` 里补上，
同样标 `migrated`。判据是 `legacy_unconfirmed`，界面据此标注"历史项目，未经确认"。

## 7. API 与事件

```text
GET  /api/v1/agents
POST /api/v1/agents/reload
POST /api/v1/projects/{id}/advance
GET  /api/v1/projects/{id}/approvals
POST /api/v1/projects/{id}/approvals/{approval_id}
POST /api/v1/projects/{id}/revise
GET  /api/v1/projects/{id}/conversation
GET  /api/v1/projects/{id}/agent-runs

GET  /api/v1/projects/{id}/lock-variables      当前值 + 可选项（画风目录、改编模式）
PUT  /api/v1/projects/{id}/lock-variables      只改传了的字段，没传的不动
```

（前八个端点与 `apps/api/modules/agent/router.py` 一一对应；
锁定变量那两条在 `apps/api/modules/project/router.py` 上，因为它们是项目属性
而不是编排动作。）

锁定变量接口的三条规矩：

- **PUT 不是 POST**：这是"这个项目的锁定变量就是它"，重复调用结果相同。
- **字段可省略**，含义是"这次不动它"；要求整份传回会让两个标签页同开时互相覆盖。
- **画风一旦冻结就拒绝再改**（409）。`ensure_style` 已存在就不覆盖，
  改 key 不会有任何效果——返 200 等于告诉用户"换好了"而全片仍是旧画风。
  时代背景与改编模式在冻结之后仍可改，它们只影响还没跑的阶段。

事件走项目 SSE（ADR-013 / ADR-019 的 Outbox）。注意：**文本阶段目前没有 task**，
所以 `advance` 期间没有 task 事件，前端只能等 HTTP 响应——这是 §3.3 第 1 条的直接后果。

## 8. 技术选择与工程设计

- 结构化输出优先：自由文本不当业务数据。schema 用 `extra="forbid"`，模型幻想出的字段立刻报错。
- spec 与执行引擎分离；第三方 Agent 只能是 YAML（不可信输入不进程序）。
- `_SPEC_OF` **钉死到具体 agent id**，不按 role 取默认——同一 role 下有多个 Agent，
  `default_for` 只能取一个。
- DeepSeek 的 `response_format: json_object` 要求提示词里出现 "json"，由 runner 统一注入，
  第三方 Agent 作者不需要知道。
- 改阶段枚举必须同时迁移存量数据（`_LEGACY_STAGES` 就是上一次的教训：不翻译会在
  `_NEXT[stage]` 上 KeyError 变 500，表现为"点继续没反应"）。ADR-037 这一次
  没有作废任何阶段名，所以要迁移的是数据不是阶段名——判据钉在
  `tests/unit/test_four_gates_migration.py` 上，迁移里"哪些 stage 还没到门①"
  必须与运行时的 `_NEXT` 推出来的集合一致。
- 受控词表（`HEIGHT_BANDS` / `BODY_TYPES` / `POSTURES`）写成 `Literal` 而不是入库：
  它们是 Agent 的**输出契约**，换一个词就要同步改提示词、改 schema 样例、重跑 eval。
  真正需要热更新的画风目录走 `style_catalog` 表，那个才是数据。

## 9. 模块依赖

- 05 Gateway：模型解析、failover、熔断、BYOK。
- 03 内容：产出正文的宿主，`mark_role_edited` 的调用方。
- 06 一致性：接收 `characters` / `scenes` 的同步。
- 08 任务 / 09 计费：**当前只在出图路径上接了**，文本路径未接（FR-AGENT-003）。
- 12 媒体：M2 要新增视频 / TTS / 合成的编排，见该模块。

## 10. 当前缺口与风险

| 缺口 | 影响 | 优先级 |
|---|---|---|
| 分镜阶段拿不到视频段上限 | ADR-032 第 4 条无法落地，分镜时长与实际生成对不上 | P0 |
| `advance` 不幂等 | 重复点击 = 重复调模型 = 重复花钱，且产出被覆盖 | P0 |
| 文本链路无计费 | 平台不知道文本花了多少钱，熔断（ADR-014 的 `task_cost_cap`）对文本无效 | P0 |
| `agent_runs.cost` 恒 0 | 单次运行成本无法溯源 | P0 |
| failover 后的实际模型不显示 | ADR-031 第 7 条的"用了谁必须说"未落地 | P0 |
| Router 黄金集缺失 | 提示词退化不会让测试变红——正是 `22_AgentEval.md` 要防的事 | P1 |
| QA 无调用方 | 分镜覆盖核验靠人眼 | P1 |
| 门数量固定四道 | 门是策略不是常量（ADR-021），但改它不挡出片 | P2 |
| 门③ 只能整批通过或打回 | 想单独改一张锚点卡要走 `revise` 重跑整个场景阶段 | P2 |
| 门③ 的判据是剧本推的下界 | 可能多出几张不必要的卡；漏判由门④ 的 `anchor_gaps` 兜底 | P2 |

## 11. 迭代计划

1. `advance` 幂等 + 文本链路计费（两件事一起做，都要碰同一段代码）。
2. 分镜阶段接入视频段上限：模板变量 → schema 字段 → schema 样例。
3. failover 实际模型回写与前端显示。
4. M2 媒体编排：视频 / TTS / 合成三类任务的推进与门（详见模块 12），
   **确定性工具调用，不让 Agent 拼 ffmpeg 命令**。
5. Router 黄金集与内容质量离线评分。

## 12. 验收标准与测试

- 同一项目同一阶段重复 `advance`（含并发），只产生一次 Agent 运行、一次扣费、一份产出。
- 任一 `agent_runs` 行都能查到：resolved_prompt 全文、原始输出、模型 id、token 数、
  尝试次数、耗时；接上计费后还能查到金额。
- 门被拒绝后阶段退回 `_REDO_FROM` 指定的阶段，且**不覆盖**已确认的上游版本。
- 四道门每一道的开门 / 通过 / 打回 / 打回后重跑都有集成测试
  （`tests/integration/test_four_gates.py`）。
- 库里存着的**任何**历史 stage 值都还能 `advance`，且读回来是现行枚举里的值
  （`tests/integration/test_stage_migration.py`，判据从 `Stage` 与
  `_LEGACY_STAGES` 自动枚举，不是手写清单）。
- 改过的 Agent 提示词有 eval 覆盖：受控词表、"不得默认套用本国"禁令、
  变量占位符从提示词里掉出去时 CI 变红（`tests/eval/test_prompt_contracts.py`）。
- 新增一个 Agent 而不补 eval 样例时，`tests/unit/test_agent_eval_required.py` 变红。
- Provider 报错时用户看到的是错误目录里的话术，不是原始异常。
- Gateway failover 后，前端显示的模型是**实际使用的那个**，不是用户选的那个。
- 结构化输出 schema 通过率 100%（允许 `schema_retries` 内的重试）。
