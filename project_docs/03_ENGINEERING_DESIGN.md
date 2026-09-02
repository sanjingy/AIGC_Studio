# 工程设计

> 最近核对：2026-09-02。口径依据 `DECISIONS_2026-09-02.md` 与 ADR-030 ~ 033。

## 1. 总体架构

```text
Browser / Next.js（/freeflow 单壳，ADR-030）
  ├─ REST：命令、查询、审核、取消、重试
  └─ SSE：项目任务、Agent、资产、余额事件
            │
            ▼
FastAPI 模块化单体
  ├─ auth / project / content / asset
  ├─ task / realtime / billing
  ├─ agent / gateway / consistency / skill
  └─ core：配置、DB、Redis、错误、日志、鉴权
            │
       PostgreSQL（真相）
       Redis（队列/事件）
       MinIO（文件）
            │
            ▼
Arq Worker
  ├─ Image Generation Job（已实现）
  └─ 待实现：Video / TTS / 每镜 MP4 合成 Job
            │
            ▼
Provider Adapters（文本、图像已接；视频、TTS 待接）
```

> 图上没有画 Agent Job：**文本 Agent 目前不经过 Worker**，`advance` 在 API 进程里同步执行。
> 见 §5。

## 2. 模块化单体

每个业务模块标准结构：

```text
modules/<name>/
  __init__.py    # 只导出公开 service 能力，不导入 router（会撞循环导入）
  models.py      # 模块私有 ORM
  repository.py  # 模块私有持久化
  schemas.py     # 输入输出契约
  service.py     # 唯一跨模块入口
  router.py      # HTTP 边界
```

跨模块禁止直接 import 对方 `models` 和 `repository`，由 Ruff banned-api 检查。这样可以保持事务和领域规则集中，也为未来真正拆服务保留清晰边界。

## 3. 数据所有权

| 数据 | 权威模块 | 说明 |
|---|---|---|
| 用户、组织、Refresh Token | auth | 其他模块只使用 `user_id/org_id` |
| 项目状态和项目配置 | project | 不保存任务运行真相；五个阶段的产出正文存 `current_state_json`（不实体化，决策记录 §3.4） |
| 内容 Patch 与修订历史 | content | Agent 输出原件仍由 agent 记录。**该模块目前是未提交的工作树代码** |
| 文件与元数据 | asset | 对象内容在 S3/MinIO。**`assets` 只存文件与血缘，不加"变体组 / 当前版"字段**（§11.1 裁决 1） |
| 任务执行状态 | task | 全系统唯一权威 |
| Agent 推理过程与审核 | agent | 不替代 task 状态 |
| Credits 与价格 | billing | 流水不可变 |
| 一致性档案和条件 | consistency | 通过 asset_id 引用文件 |
| 首帧图 / 配音 / 段视频的候选与当前版 | consistency / media（各自建表） | ADR-033 + §11.1 裁决 1：**各业务模块自建候选表**，"当前版"的语义按镜/段/配音各不相同 |
| 组织级模型默认 | gateway（独立表 `org_model_defaults`） | §11.2 裁决 6：不给 `organizations` 加 JSONB 列，表所有权归 Gateway，且需要 `updated_by` 溯源 |
| 用户上传 Skill | skill | 运行接线按决策记录 §9 冻结 |

## 4. 租户和权限

- 注册创建个人 `organization`，用户作为 owner。
- 用户侧资源查询必须带 `org_id`。
- 跨租户访问返回 404，避免泄露资源存在性。
- **`users.role` 目前没有任何地方按它判权限**，注册时人人 `owner`。不能凭它宣称有角色体系。
  管理员判定走配置项白名单（§11.2 裁决 8），不等模块 13。
- 邮箱验证的门开在"不能发起花 Credits 的生成"，判定点在 `task.service.create_task` 与
  `agent` 的 `advance` 两处（§11.2 裁决 1）。
- 对象存储 Key 必须包含租户隔离前缀，预签名 URL 只能针对已授权对象。

## 5. 任务状态机

```text
queued → running → succeeded
   │         ├──→ failed
   │         └──→ cancelled
   └────────────→ cancelled

failed --retry--> queued（attempt + 1）
```

工程约束：

1. Web 请求只创建任务，不执行耗时生成。
2. `tasks.status` 是唯一状态权威（ADR-008）。
3. 每个有副作用的任务必须有稳定幂等键。
4. Worker 重试必须带 attempt，账务幂等键也必须带 attempt。
5. Worker 崩溃后可从 PostgreSQL 恢复，不依赖进程内存。

### 5.1 当前偏离：文本链路不走 tasks（P0 缺口）

`POST /projects/{id}/advance` **不建任务、不预扣、不结算、也不幂等**：
`orchestrator.advance` 在 API 进程里同步调 `runner.run_agent`，
`apps/api/modules/agent/` 整个包对 `billing` 零引用，`agent_runs.cost` 恒为 0。

后果有三个，都是钱和状态上的：

- `task_cost_cap`、日消费上限这些熔断**对主链路的文本生成完全无效**。
- 用户重复点击"继续"会重复调模型并覆盖产出。
- `advance` 期间没有 task 事件，前端只能干等 HTTP 响应。

**决定的形态见决策记录 §11.1 裁决 5、6**：保持同步、走 reserve/settle 直连
（照抄 `asset/character.py` 已有的第三条路），写 `agent_runs.cost`，**不包成 `tasks`**；
幂等键由服务端取 `(project_id, stage, 当前产出版本)`，同阶段互斥。理由是包成 tasks 要把
advance 改异步、前端全改，而它不挡逐镜 MP4；直连能立刻堵住钱漏。排 Wave 1。

在这条改完之前，上面第 1 条"Web 请求只创建任务"对文本链路是**目标不是现状**，
文档不要写成已经成立。

## 6. 账务事务

```text
estimate
  → reserve（锁账户行，余额转 reserved）
  → execute
      ├─ success → settle（扣实际成本，退还差额）
      └─ failure → release（释放全部预扣）
```

- 金额使用 BIGINT Credits，禁止浮点。
- 交易流水只 INSERT，不允许覆盖历史。
- 定价来自 `model_pricing`，系数和熔断来自 `pricing_rules`，代码里零价格常量。
- 充值、支付回调、预扣和退款都必须有业务幂等键。
- 预扣与结算的 `attempt` 编号必须一致（`begin_execution` 会自增 attempt，差一位会导致
  任务成功了但钱没扣，且不报任何错）。

### 6.1 模型解析必须在建任务时完成并写进 `input_json`

这是本轮新增的硬约束，因为 **ADR-024 硬约束 1（换模型必重算 Credits 预估）当前被违反**：

- `billing/pricing.py::_shape()` 取 `payload["model_id"]`，取不到就退回 `_DEFAULT_MODEL`；
- 而出图任务的 payload 在 `consistency/render.py` 里构造，**从来不写 `model_id`**；
- Gateway 直到 Worker 执行时才按 `projects.model_preference` 解析模型。

结果是：项目偏好选了贵的那个，**预扣按便宜的那个算**。今天两者差价小所以看不出来，
接入视频后是数量级差异。

因此：

1. **模型解析提前到建任务时**，解析结果（`model_id`、`provider_id`、
   命中的是哪一层默认）写进 `tasks.input_json`。
2. 预扣按写进去的那个 `model_id` 估算。
3. Worker 执行时以 `input_json` 里的模型为准，不再自己解析。
4. failover 换了模型时，**实际使用的模型必须回写到任务结果并在前端显著标注**
   "实际使用 X，你选的 Y 失败"（ADR-031 第 7 条、§11.2 裁决 4）。
5. 副产品：ADR-031"代价"里要求的"每个生成任务记下解析出的模型和它来自哪一层"天然成立。

排 Wave 1（决策记录 §11.3 第 2 条）。

### 6.2 手动发放 Credits

首批用户由负责人手动加 Credits。`POST /credits/topup` **当前没有任何权限判定**，
任何登录用户都能给自己加余额——这不是"如果误开放会成为漏洞"，它已经是开放的。
收口方式（§11.2 裁决 8/9/10，Wave 0）：

- 管理员判定走**配置项白名单**（管理员用户 id 列表），不依赖 `users.role`。
- 入账类型从 `TOPUP` 改为 `ADMIN_GRANT`——没有支付凭证的入账记成 `TOPUP`，
  将来真接支付时对账会把它算成收入。
- 端点改名 `POST /credits/admin-grant`，旧端点删除。
- `credit_transactions` 加 `operator_id` 列：谁发的钱必须查得出来。

## 7. 实时与 Outbox

数据库状态变更和 `outbox_events` 在同一事务提交。维护 Job 将事件投递到 Redis Stream；前端通过一次性 Ticket 建立项目级 SSE，使用事件序号和 `Last-Event-ID` 重放。

前端处理规则：

- 事件携带最终状态，不发送"递增 1"类不可幂等指令。
- SSE 断线后自动重连。
- 超出重放窗口时返回 `sync.required`，前端全量刷新项目。
- 一个项目页面只建立一条主 SSE，而不是每个镜头一条连接。

## 8. 资产上传和血缘

```text
Web 申请 upload ticket
  → 浏览器直传 MinIO/S3
  → Web 通知 complete
  → API 校验对象、MIME、大小和配额
  → 资产转为 ready
```

生成资产必须记录：项目、任务、Provider、模型、Resolved Prompt、输入参考资产、费用和生成时间。
上游临时下载地址必须立即转存自有对象存储（DashScope 图片链接只有 24 小时有效期）。

**候选与"当前版"不放在 `assets` 上**（§11.1 裁决 1）：`assets` 是通用文件表，
"当前版"的语义按镜 / 段 / 配音各不相同，塞进去会让它长出三套业务字段。
各业务模块自建候选表，通过 `asset_id` 引用文件，并**记录下游消费时用的是哪一个候选**
（ADR-033 第 3 条，这是"过期"标记的地基）。

## 9. Provider、Agent 与 Skill 分层

```text
业务任务：生成什么
  → Capability：需要什么能力
  → Gateway：选择哪个 Provider/Model（三层默认解析）
  → Adapter：如何调用供应商
  → Task：如何审计、计费和恢复
```

- Agent 负责理解、规划和生成结构化内容，**不选模型**（ADR-002）、**不写风格词**。
- Skill 描述可复用生产流程。运行时接线按决策记录 §9 冻结。
- Provider 负责模型协议适配。
- Runtime 负责执行位置。自建 GPU / ComfyUI **不做**（决策记录 §1）。

三者不允许相互替代。

### 9.1 能力维度是四个

`text_generation` / `image_generation` / `video_generation` / `tts`（ADR-031 第 2 条）。
现状：`gateway/catalog.py::SPECS` 里**只有前两个**。
另有两处散落的能力字符串与这四个对不上：`gateway/router.py::_ORDER` 是
`text_generation / image_generation / text_to_speech / image_to_video`，
`credentials.CAPABILITY_LABELS` 里还多出 `text_to_video` / `image_editing` /
`vision_understanding` / `speech_to_text`。这些**全部没有 adapter，不是能力**，
收敛时一并清掉（只改常量，上线前跑一次
`SELECT DISTINCT capability FROM provider_credentials` 确认库里无旧值，§11.2 裁决 5）。
"图生还是文生"是模型的输入形状，不是新能力。

### 9.2 供应商是固定目录，单段上限在 adapter 声明

- 供应商来源是**代码内固定目录**，每家 adapter 声明能力、模型列表、定价键名。
  用户"添加供应商"= 给目录里的某家填 Key（BYOK，ADR-025 折扣）。
- **文本能力额外允许一个 OpenAI 兼容自定义端点；视频和 TTS 不允许**——
  各家请求形状差异太大，做了就是假入口（ADR-031 第 5 条）。
- **视频模型的"单段最大时长"写在 adapter 的能力声明里**，不放数据库、不放配置：
  它是**协议事实**不是价格。价格必须可热更新（`model_pricing`），能力不需要——
  加一个模型本来就要写 adapter 代码。
  分镜阶段要按 ADR-032 第 4 条读到这个数来定每镜时长与段数，所以它必须挂在
  **目录**上而不是调用结果上。
- `ModelSpec` 重构（`ProviderSpec.models` 从 `tuple[tuple[str, int], ...]` 换成结构体）
  是 **Wave 2 第一件事，排在视频 adapter 之前**（§11.2 裁决 7）。

### 9.3 三层默认与"用了谁必须说"

解析顺序固定：**生成前临时选择 > 项目覆盖（`projects.model_preference`）> 组织默认**
（ADR-031 第 3 条）。临时选择只对本次生效，不回写任何默认，且必须是**界面上的按钮**
而不是让用户在对话里说。

`_resolve` 的"偏好只重排候选顺序、选中模型失败仍自动 failover"行为**保留**；
代价是 failover 发生时必须显著标注实际使用的模型（§6.1 第 4 条）。

## 10. 错误和可观测性

- 对外错误统一为稳定 `error_code + message + detail + trace_id`。
- 未捕获异常对用户隐藏框架和上游细节，日志保留堆栈。
- Agent 失败不把内部 Prompt 或 Provider 原始错误暴露给用户。
- API 请求带 `X-Trace-Id`，结构化日志记录路径、状态和耗时。
- Key、Token、密码、请求中的密钥字段必须脱敏；密钥不进 `input_json`。
- `billing/service.audit()`（对账函数）已经存在，但**没有调度也没有告警出口**——
  账不平没人会知道。补调度是运维项，不挡出片。
- 下一阶段补 OpenTelemetry、Prometheus 指标、Sentry/错误聚合和 Provider 成本告警。

## 11. 测试策略

```text
Unit：纯函数、错误分类、Prompt 组合、路由决策、Patch 路径安全
Integration：PostgreSQL/Redis/MinIO、事务、租户和账务
Contract：Provider Mock、API Schema、事件信封
Eval：Agent 结构化输出的 schema 样例（硬门禁）
E2E：注册 → 创建项目 → 剧本确认 → 分镜确认 → 出图 → 逐镜 MP4 下载
```

任何新功能按以下顺序交付：

```text
Domain → API Contract → Service → Worker → Adapter → UI → Tests → Docs
```

外部 Provider 一律先有 Mock，`ENV=test` 时强制走 Mock。
测试里改环境变量用赋值不用 `os.environ.setdefault()`——compose 已注入时它不生效，
这个坑让整个测试套件打过一次真实上游。

## 12. 部署设计

MVP 保持六类进程：Web、API、Worker、PostgreSQL、Redis、对象存储。
生产环境必须增加反向代理/TLS、数据库备份、对象生命周期、秘密管理、Migration 发布步骤和 Worker 优雅停止。

### 12.1 镜像里要装 ffmpeg

`infra/docker/api.Dockerfile` 目前只装了 `build-essential` 和 `curl`，
**没有 ffmpeg 也没有 ffprobe**。逐镜 MP4 合成（段拼接 + 配音混音）和读取音视频真实时长
都依赖它们，接入前必须先加进镜像。

注意 compose 里 **api 和 worker 共用 `image: aigc-studio-backend`**（同一个 Dockerfile）：
加依赖两个会一起重建。这正是当初共用镜像的原因——分开建会出现"api 好好的 worker 崩了"。

### 12.2 渲染进程的边界

- 渲染器只接受**结构化 Render Spec**，自己拼命令行；禁止 Agent 生成 shell 命令
  （ADR-032 第 6 条）。
- Render Spec 的所有输入必须是**授权的 Asset ID**，不是路径也不是 URL；
  编码器、分辨率、帧率等一律走后端枚举，**没有透传参数字段**。
- 大文件不经过 API 进程内存，Worker 从对象存储流式下载到隔离临时目录，用完即清。
- 渲染失败只保留结构化阶段与短日志摘要，不把完整命令行回给用户。
- Render Spec 里预留一个**空的 overlay 槽位**（水印注入点），本轮不实现——
  AIGC 标识事后补要重渲染全部存量内容（§11.2 裁决 12）。

### 12.3 队列

视频、TTS、渲染任务接入后要分独立队列。当前 `worker/main.py` 是单队列、
`max_jobs = 8`、`job_timeout = 900`，一个长视频任务会占住一个槽位让文本请求排队。

暂不采用 Kubernetes；当单机 Compose 无法满足高可用、多个 Worker 队列需要独立扩缩容或出现专职平台团队时再评估。
