# 现有功能总清单

> 最近核对：2026-09-02。口径依据 `DECISIONS_2026-09-02.md`，代码事实来自
> `orca/tasks/W1.report.md`（模块 03/04/06/12）与 `W2.report.md`（模块 01/05/09/13）。
> 与本表冲突的旧文档一律以本表为准；与决策记录冲突的以决策记录为准。

## 1. 状态定义

与决策记录 §0 统一，只有这五个词。**页面占位、类型声明、路线图都不算实现。**

| 状态 | 判断标准 |
|---|---|
| 已实现 | 有可运行代码、迁移、测试，且有用户入口 |
| 部分实现 | 后端或前端只有一半，或有入口但不闭环 |
| 预留 | 代码里有类型 / 枚举 / 占位，**没有**实现，**不计入功能** |
| 仅设计 | 只有文档 |
| 未实现 | 什么都没有 |

## 2. 用户与账号

| 功能 | 状态 | 说明 |
|---|---|---|
| 邮箱注册 | 已实现 | 注册时创建个人组织和 owner 用户，发放 500 Credits 体验额度 |
| 邮箱密码登录 | 已实现 | Argon2 哈希，失败时做时序均衡 |
| Access/Refresh Cookie | 已实现 | httpOnly Cookie；响应体不返回令牌。Access 1800 秒 / Refresh 14 天 |
| Refresh Token 轮换 | 已实现 | 支持令牌族、重放检测和整族撤销 |
| 登出 | 已实现 | 撤销当前 Refresh Token 族 |
| 当前用户查询 | 已实现 | `GET /api/v1/auth/me` |
| 用户偏好 | 预留 | `user_preferences` 表存在，没有 API 也没有 UI |
| 邮箱验证 | 未实现 | 决策记录 §6 定为要补。门开在"不能发起花 Credits 的生成"（§11.2 裁决 1），判定点在 `create_task` 与 `advance` |
| 找回 / 重置密码 | 未实现 | 决策记录 §6 定为要补。仓库里**没有任何发信 / SMTP / 邮件服务代码** |
| 接口限流 | 未实现 | 全仓零限流代码。`password/forgot` 是公开发信接口，与找回密码同批做（§11.2 裁决 2） |
| 平台管理员身份 | 未实现 | `users.role` 是 org 内角色，注册时人人 `owner`，**没有任何地方按它判权限** |
| 团队成员与邀请 | 预留 | 有 `organizations` 与 `users.role` 两个占位，无协作流程。决策记录 §6 明确不做 |
| MFA / 第三方登录 / 手机号 | 未实现 | 不进入当前 MVP |

## 3. 项目和工作台

| 功能 | 状态 | 说明 |
|---|---|---|
| 创建、列表、详情、编辑、软删除 | 已实现 | 项目按个人组织隔离 |
| 项目模型偏好 | 已实现 | 可按能力设置模型优先级并保留失败回退 |
| 项目预算字段 | 部分实现 | 数据与计费熔断支持，产品配置入口不完整 |
| 项目大厅 | 已实现 | 显示真实项目、运行和待审核项 |
| 项目生产概览 | 已实现 | 汇总故事、角色、场景、分镜、出图和任务状态 |
| 旧四栏工作台 `(app)` | 已实现 | 真实接口已接。**ADR-030 已定删除**，只留 `/login`，`/dashboard` 重定向到 `/freeflow` |
| Freeflow 项目工作台 | 部分实现 | **ADR-030 已定由它替换 `(app)`**；替换的唯一前置是能独立跑完"新建项目 → 剧本确认 → 分镜确认 → 出图"不跳回旧页。**闭环缺口见模块 11** |
| 项目模板 | 未实现 | 空壳页。决策记录 §2：`templates` 摘出导航、删路由 |

## 4. 内容生产

| 功能 | 状态 | 说明 |
|---|---|---|
| Router 路线判断 | 已实现 | YAML Agent + 结构化输出 |
| 情节目录 | 已实现 | `story.plot_index.v1`，产出存 `current_state_json` |
| 剧本 | 已实现 | `story.screenplay.v1`，集 → 场 → 节拍 → 台词 |
| 角色档案 | 已实现 | `visual.character.v1`，并同步一致性 Profile |
| 场景档案 | 已实现 | `visual.scene.v1`，并同步 Scene Profile |
| 分镜表 | 已实现 | `visual.storyboard.v1`，9 列；支持镜头级出图入口 |
| 分镜的每镜时长 / 段数 | 未实现 | `StoryboardShot` 明确不含这两项，而 ADR-032 第 4 条要求分镜阶段就定。§11.1 裁决 3：作为可选字段落在 `StoryboardShot` 上，由确定性代码填 |
| 字段级修改 / 变更历史 / 批次撤销 | 部分实现 | 后端 + 迁移 + 三份测试**在未提交工作树**（`apps/api/modules/content/`）；**前端零接线**——全仓前端代码里 `outputs/` 与 `revisions` 出现 0 次 |
| 聊天式局部返工 | 已实现 | `POST /revise` + `conversation_messages`。**但它不写 `content_revisions`**，变更历史只有人工编辑那一半 |
| 上游改动的过期标记 | 部分实现 | `current_state_json.stale_roles` 只覆盖五个文本阶段，不覆盖图、视频、配音 |
| 分集大纲 | 未实现 | 决策记录 §3.2 定为"取"，但 `_NEXT` 里没有这一阶段 |
| 创意项目入口（一句话 + 参考内容） | 未实现 | `advance` 只认 `state["source"]` 一段文本，没有参考内容字段 |
| 内容快照与生产锁 | 未实现 | 被 MP4 引用的分镜版本目前可被原地改掉 |
| 角色 / 场景实体化 | 未实现 | 决策记录 §3.4 明确放 M2 之后，正文继续存 `current_state_json` |
| 多人协同编辑 | 未实现 | 不在当前 MVP |

## 5. Agent 与审核

| 功能 | 状态 | 说明 |
|---|---|---|
| Agent 注册表和热重载 | 已实现 | 10 份内置 YAML spec，第三方只能是 YAML |
| 项目 Advance | 已实现 | 推进下一生产阶段。**但它在 API 进程里同步跑 Agent，不建任务、不预扣、不结算、也不幂等**——`apps/api/modules/agent/` 对 `billing` 零引用 |
| Advance 的计费 | 未实现 | §11.1 裁决 5：保持同步、走 reserve/settle 直连（照抄 `asset/character.py`）、写 `agent_runs.cost`。Wave 1 |
| Advance 的幂等 | 未实现 | §11.1 裁决 6：服务端取 `(project_id, stage, 当前产出版本)`，同阶段互斥 |
| 运行记录和步骤审计 | 已实现 | `agent_runs` / `agent_steps`，`resolved_prompt` 存全文。**`agent_runs.cost` 恒为 0**，`finish_run` 根本没有这个参数 |
| 对话记录与返工指令 | 已实现 | conversation + revise |
| 人工审核门 | 已实现 | **两道**（`await_setup` / `await_storyboard`），不是文档一直写的三道——第三道"成片"没有对应阶段 |
| 自动 QA | 预留 | `qa.default.v1` 有 spec、有 `QAReport` schema，**零调用方** |
| Director 参与决策 | 预留 | `director.default.v1` 有 spec，零调用方；阶段图硬编码在 `orchestrator._NEXT` |
| Agent Eval 门禁 | 部分实现 | 硬地板是结构化输出 schema 样例，缺样例会让 CI 变红；`22_AgentEval.md` §2.1 的 Router 黄金集和 §2.3 的内容评分**没有实现**（`tests/eval/agent_evals/` 只有一份 README） |
| 可视化 Agent 助手 | 部分实现 | 旧工作台有真实对话；freeflow 悬浮助手仍有原型成分 |

## 6. 图像与一致性

| 功能 | 状态 | 说明 |
|---|---|---|
| 风格 Profile | 已实现 | 项目级风格词、基础模型、色调、基准 seed |
| 风格冻结 | 预留 | `service.lock_style` **零调用方**，`style_profiles.locked_at` 永远是 NULL——风格可在出图中途被改掉 |
| 角色 Profile | 已实现 | 结构化外貌 + 基准立绘列 + 版本号 |
| 场景 Profile | 已实现 | 结构化空间 + 基准参考图列；一致性锚点 `camera_axis` / `fixed_references` 真的进提示词 |
| 角色出图 | 已实现 | 任务化、计费、资产落库、SSE 进度 |
| 场景出图 | 已实现 | 同上 |
| 分镜出图 | 已实现 | 角色 / 场景 / 风格条件确定性合成 Prompt，`resolved_prompt` 落库 |
| 从资产库选择基准图 | 已实现 | `PUT`，不建任务、不扣费（测试逐条断言反面） |
| 本地上传并设为基准图 | 已实现 | 复用预签名直传链路 |
| AI 生成后自动设为基准图 | 未实现 | 生成成功路径不回写 `base_portrait_asset_id` / `base_reference_asset_id`，只有 PUT 会写 |
| 候选版本与"当前版" | 未实现 | ADR-033 已作废"取第一条就是当前版"的约定，代码还没改。§11.1 裁决 1：**各业务模块自建候选表**，`assets` 只存文件与血缘 |
| 自动质量分与废片重试 | 预留 | `shot_quality_scores` 表和 `service.score_shot` 都在，**`score_shot` 零调用方**——所以 `reference_embedding` 恒空、真实废片率永远测不出 |
| L1 参考图条件化 | 未实现 | `ImageRequest` 只有 prompt / negative / size / n / seed，**没有任何参考图字段**。当前一致性实际是 L0；§8.1 那组"10 镜全部 ≥ 0.65"就是 L0 下测出来的 |
| 一致性档次 `consistency_tier` | 预留 | 两张档案表都有这一列，恒为 `"L1"`，从没被写过也从没被读过。§11.1 裁决 9：Wave 2 动档案表时一并删 |
| 三视图 / 表情集 / 服装集 | 预留 | `turnaround_asset_ids` 列存在，无任何代码写它 |
| LoRA 训练 | 未实现 | 一致性 L2，依赖自建 GPU；决策记录 §1 明确不做自建 GPU |

## 7. 资产库

| 功能 | 状态 | 说明 |
|---|---|---|
| 预签名直传 | 已实现 | 文件不经过 API 进程 |
| 上传完成确认 | 已实现 | 校验对象、大小、MIME 和配额 |
| 资产列表 / 详情 / 下载 / 删除 | 已实现 | 按组织与项目过滤 |
| 存储用量统计 | 已实现 | 支持配额和百分比 |
| 文件夹 | 已实现 | 创建、改名、删除、放入 / 移出资源 |
| 独立角色档案 | 已实现 | 可从描述生成并计费 |
| 跨类型统一搜索 | 部分实现 | Library 聚合存在，搜索、标签和血缘能力有限 |
| 资产版本 / 派生关系 | 仅设计 | 当前尚未形成通用 asset_versions |

## 8. 任务与实时通道

| 功能 | 状态 | 说明 |
|---|---|---|
| 任务创建、查询、取消、重试 | 已实现 | 状态权威在 PostgreSQL `tasks` |
| Arq Worker | 已实现 | Redis 队列。当前只处理 `mock.*` 与 `image.generate` |
| 幂等键与尝试次数 | 已实现 | 支持安全重试。**注意：只覆盖走 `tasks` 的路径，`advance` 不在其中** |
| Transactional Outbox | 已实现 | 任务状态与事件在同一事务写入 |
| 项目级 SSE | 已实现 | 一次性 Ticket，支持断线重放 |
| 旧版全局任务页 | 已实现 | 使用真实 Task/SSE 接口，页面仍提供 mock.echo/mock.fail 调试入口 |
| Freeflow 项目任务页 | 部分实现 | 当前主要展示 `agent_runs`，不是统一 `tasks` 权威列表 |
| WebSocket Node Agent | 未实现 | M4 能力 |

## 9. 模型网关和 BYOK

| 功能 | 状态 | 说明 |
|---|---|---|
| 能力到 Provider 路由 | 部分实现 | `catalog.SPECS` 里**只有 `text_generation` 和 `image_generation` 两个能力**；ADR-031 要求的四个能力里视频和 TTS 还不存在 |
| Provider 失败回退 | 已实现 | 候选模型依次降级 + 熔断 |
| 项目级模型偏好 | 已实现 | 只重排，不过滤后备模型 |
| 换模型重算 Credits 预估 | 未实现 | **ADR-024 硬约束 1 当前被违反**：出图任务的 payload 从不写 `model_id`，`pricing._shape()` 取不到就用默认模型算价，而 Gateway 在 Worker 里才按偏好解析。修法是把模型解析提前到建任务时并写进 `input_json`（§11.3 第 2 条，Wave 1） |
| 平台 Provider | 部分实现 | DeepSeek（文本）、DashScope（图像）、Mock 兜底；视频与 TTS 没有 adapter |
| 用户 BYOK | 已实现 | 按能力保存 AES-GCM 加密密钥，按 ADR-025 折扣，按 org 独立熔断 |
| Key 测试 | 已实现 | 使用免费探针，不发起生成消费 |
| 模型目录只读接口 | 已实现 | `GET /model-catalog` |
| 模型页面 | 部分实现 | `/freeflow/models` 用真实目录数据，但**全站没有任何链接指向它**，只能手敲 URL |
| 组织级模型默认 | 未实现 | ADR-031 第 3 条的第一层。§11.2 裁决 6：独立表 `org_model_defaults`，不给 `organizations` 加 JSONB 列 |
| 生成前临时选模型（界面按钮） | 未实现 | ADR-031 第 4 条。§11.2 裁决 4：仍然 failover，但结果里显著标注"实际使用 X，你选的 Y 失败" |
| 文本能力的 OpenAI 兼容自定义端点 | 未实现 | ADR-031 第 5 条的唯一例外。视频和 TTS **不允许**自定义端点 |
| 视频 / TTS Provider | 未实现 | 仅有任务类型预留，见 §11 |
| ComfyUI / 本地 GPU | 未实现 | 决策记录 §1 明确不做自建 GPU |

## 10. Credits、充值和支付

| 功能 | 状态 | 说明 |
|---|---|---|
| Credits 余额和流水 | 已实现 | 不可变流水，金额使用 BIGINT 最小单位 |
| 注册赠送 | 已实现 | 500 Credits，单独标记 bonus |
| 成本估算 | 部分实现 | 单价来自 `model_pricing`、系数来自 `pricing_rules`，零价格常量；但**视频 / TTS 没有计价形状**（`_shape()` 无对应分支，会按 `units=1` 兜底），且出图的预扣按默认模型算 |
| 预扣、结算、释放 | 已实现 | 行锁、幂等键带 attempt、四层熔断 |
| 开发充值接口 | 已实现 | **但没有任何权限判定**：`POST /credits/topup` 的依赖只有 `CurrentUser`，任何登录用户可自助加余额。§11.2 裁决 8/9：改配置项管理员白名单、入账类型改 `ADMIN_GRANT`、端点改名 `POST /credits/admin-grant`、旧端点删除。§11.3 第 1 条列为 Wave 0 |
| 发放操作者溯源 | 未实现 | §11.2 裁决 10：`credit_transactions` 加 `operator_id` 列 |
| 每日对账 | 部分实现 | `billing/service.audit()` 已经存在，但**没有定时任务、没有告警出口**——账不平没人会知道 |
| 真实支付下单 / 回调 | 未实现 | 决策记录 §1、§6 明确不做。首批用户由负责人手动加 Credits |
| 发票、退款、对账单、订阅套餐 | 未实现 | M3 之后 |

## 11. Skill、视频与运营

| 功能 | 状态 | 说明 |
|---|---|---|
| 内置 Skill 声明 | 已实现 | `novel_to_anime.yaml` |
| 用户 Skill 上传与校验 | 已实现 | 非法 YAML 保留为 `invalid` 供用户修正；不落 `skills/custom/`，按 org 隔离 |
| Skill 运行时接线 | 未实现 | 决策记录 §9 **整体冻结**。UI 必须诚实标注（ADR-026 的验收标准） |
| 工作流画布（Canvas） | 预留 | 决策记录 §2 **搁置**：路由从导航隐藏、代码保留、不接后端。节点图存储与执行引擎均未设计 |
| 图生视频 | 未实现 | `video.generate` 只是 `TASK_TYPES` 里的一个字符串，`_dispatch` 对它直接抛 `no handler`；`adapters/providers/` 里没有视频 adapter |
| TTS 配音 | 未实现 | 同上（`audio.tts`）。音频优先（ADR-011）继续有效，但没有任何实现 |
| 视频模型的单段最大时长声明 | 未实现 | ADR-031 第 5 条 / ADR-032 第 3 条要求在 adapter 里声明。§11.2 裁决 7：`ModelSpec` 重构是 Wave 2 第一件事，排在视频 adapter 之前 |
| 镜内切段 | 未实现 | ADR-032 第 3 条。§11.1 裁决 2：**方案 A，沿用上一段末帧**；B 作为模型不支持时的降级；C 首尾帧模式等 Seedance 接入后按模型能力开关 |
| 每镜 MP4 合成（段拼接 + 配音混音） | 未实现 | `timeline.render` 同样只是预留字符串；**镜像里没有 ffmpeg** |
| 逐镜下载 / 批量下载 | 未实现 | ADR-032 的验收物。§11.1 裁决 8：批量下载由服务端打 zip，走一个任务，完成后给预签名 URL |
| 整集时间线 / 字幕 / BGM / 转场 / 剪映导出 | 未实现 | 决策记录 §1 **明确不做（M2 内）**。ADR-032 把终点收敛为逐镜 MP4 |
| 管理后台 | 未实现 | 决策记录 §1 明确不做 |
| 合规审核与 AIGC 标识 | 预留 | `assets.moderation_status`、`users.realname_status` 两列存在但**从未被任何代码写过**，前端保持不显示。决策记录 §1 明确不做；§11.2 裁决 12：模块 12 的 Render Spec 里预留一个空的 overlay 槽位，不实现 |

## 12. 挡住"逐镜 MP4"的清单

从上表里抽出来的 P0，按决策记录 §9"只把①产品闭环变绿"筛过一遍：

| # | 事项 | 所在模块 |
|---|---|---|
| 1 | `POST /credits/topup` 收口（Wave 0） | 09 |
| 2 | 模型解析提前到建任务时并写进 `input_json`（Wave 1） | 05 / 09 |
| 3 | `advance` 接 reserve/settle + 幂等键（Wave 1） | 04 |
| 4 | `content` 模块合入主干 + 前端接线 | 03 |
| 5 | 分镜承载每镜时长与段数 | 03 / 04 / 12 |
| 6 | 候选版本与 `is_current`（各模块自建候选表） | 06 / 12 |
| 7 | AI 生成后回写基准图 | 06 |
| 8 | 视频 / TTS adapter 与四个能力维度 | 05 |
| 9 | 镜像装 ffmpeg，接段拼接 + 混音 | 12 |
| 10 | 视频 / TTS 的计价形状与 `reserve/settle` | 09 / 12 |
| 11 | freeflow 跑完"新建 → 剧本确认 → 分镜确认 → 出图"不跳回旧页 | 11 |
