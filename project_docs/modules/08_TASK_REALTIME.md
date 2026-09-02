# 08 任务、队列与实时进度

> 状态：**部分实现**（状态机 / 幂等 / Outbox / SSE 已通；队列不分类，取消不落到 Worker，前端任务页读错表）
> 优先级：**P0**（视频、配音、合成三类新任务全部要走这条链路）
> 负责人：待定
> 最近核对：2026-09-02
> 权威顺序：[DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §2 / §3 > ADR-008 / 010 / 013 / 018 / 019 > 当前代码

---

## 1. 模块目标与边界

给所有"比一次 HTTP 请求长"的动作提供统一的异步执行生命周期，
并把状态可靠地推给前端。

**拥有**：`tasks`（执行状态的**唯一权威**，ADR-008）、`outbox_events`、
状态机与合法跃迁、幂等键、SSE 通道。

**不拥有**：业务产出的内容（在 `current_state_json` 与 `assets` 里）、
Credits 的金额规则（模块 09 定，本模块只在生命周期节点调用它）、
Agent 的推理过程（`agent_runs`，模块 04）。

**Redis 只是队列与事件传输，不是真相源。** Redis 整个丢掉，
重启后从 `tasks` 与 `outbox_events` 仍能恢复出正确状态。

---

## 2. 用户与使用场景

1. 点"生成"，看到它排队 → 运行中 → 完成，中途有进度。
2. 失败了看到**能看懂的原因**和一个重试按钮，且重试不会重复扣费。
3. 点取消，钱退回来。
4. 关掉页面再回来，进度是对的（断线重放）。
5. 在一个地方看到这个项目所有正在跑的东西——**不管它是文本、出图、
   配音还是视频**。这一条今天不成立（§4.1）。

---

## 3. 当前真实能力

状态词按 [DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §0。

| 能力 | 状态 | 代码 / 测试证据 |
|---|---|---|
| 任务创建 / 列表 / 详情 / 取消 / 重试 | 已实现 | `task/router.py` 五条端点 |
| 状态机与合法跃迁集中一处 | 已实现 | `task/models.py::ALLOWED_TRANSITIONS`；`service._check_transition` |
| 幂等键（全局唯一，带 attempt 编号） | 已实现 | `tasks.idempotency_key`；`billing/service.py::_key` |
| 重试新建 attempt，重新估算并预扣 | 已实现 | `service.retry_task`；`tests/integration/test_billing_ledger.py::test_retry_uses_separate_reservation` |
| 按错误目录决定退不退钱（`Disposition`） | 已实现 | `core/errors.py`（30 条错误码，带 `retryable` / `failover` / `disposition` / `counts_as_waste`）；`service.finish_execution` |
| 不可重试的错误不给重试 | 已实现 | `service.retry_task` 读 `spec.retryable` |
| Transactional Outbox（状态变更与事件同事务） | 已实现 | `task/models.py::OutboxEvent`（独立序列保证顺序）；ADR-019 |
| 中继把 Outbox 投到 Redis Stream | 已实现 | `realtime/relay.py::run_forever`，随 Worker 启动（API 会水平扩容，多副本中继没必要） |
| 项目级 SSE + 断线重放 | 已实现 | `realtime/router.py`、`stream.py`（`STREAM_MAXLEN=1000`、TTL 24 h、`REPLAY_LIMIT=500`） |
| 游标过期时触发全量同步 | 已实现 | `stream.cursor_is_fresh` |
| SSE 用一次性票据换连接（60 秒、只能用一次） | 已实现 | `POST /projects/{id}/events/ticket`——EventSource 不能带自定义头，但长期 JWT 不能进 query（会进 access log） |
| Arq Worker 执行任务，参数只传 task_id | 已实现 | `worker/main.py`、`worker/jobs/execute.py` |
| 进度上报（0–100 + stage） | 已实现 | `service.report_progress`；`tasks.progress` 是真列 |
| 超时未完成上传的清理 cron | 已实现 | `worker/jobs/maintenance.py` |
| 跨租户任务隔离 | 已实现 | `tests/integration/test_tenant_isolation.py` |
| 任务类型 `video.generate` / `audio.tts` / `timeline.render` | 预留 | 在 `TASK_TYPES` 里，`worker/jobs/execute.py::_dispatch` **没有分支**，`pricing._shape` 也没有——按 §0 不计入功能 |
| **队列按任务类型隔离** | 未实现 | `worker/main.py` 只有一个队列，`max_jobs=8`、`job_timeout=900` 全局一份（代码注释写着"S6 会按任务类型分队列细化"，没做） |
| **取消能中止正在跑的任务** | 未实现 | `service.cancel_task` 只改数据库并释放预扣；Worker 在 `execute_task` 开头查一次状态之后**再不回头看**，上游调用照跑到底 |
| 死信队列、堆积告警、Worker 心跳 | 未实现 | — |
| freeflow 任务页读 `tasks` | 未实现 | `components/freeflow/project/task-center.tsx` 读的是 `projects.runs()`（`agent_runs`），另有一段写死的 `MOCK_TASKS` |
| 旧壳任务页 | 已实现但要删 | `app/(app)/tasks/page.tsx` 读真实 `tasks` + SSE，但带 `mock.echo` / `mock.fail` 调试按钮；ADR-030 要删这一页 |

---

## 4. 功能需求

### 4.1 P0（挡住"逐镜 MP4"）

- **FR-TASK-001**：所有超过普通 HTTP 时延的生成动作必须任务化。（已实现）
- **FR-TASK-002**：状态机合法跃迁由服务层统一控制。（已实现）
- **FR-TASK-003**：重试创建新 attempt，保留旧错误与成本；
  预扣与结算用同一个 attempt 编号。（已实现）
- **FR-TASK-004**：**`video.generate` / `audio.tts` / `timeline.render`
  三类任务的执行分支**（`_dispatch`）与计费形状
  （[09_BILLING_PAYMENT.md](./09_BILLING_PAYMENT.md) FR-BIL-004）。
- **FR-TASK-005**：**队列隔离**。方案与理由见 §6，**建议三条队列**。
- **FR-TASK-006**：**取消要落到 Worker**。视频任务跑 5–10 分钟，
  现在取消只在数据库上生效、钱退了、上游照跑——那笔上游费用平台自己吃。
  最小做法见 §5.2。
- **FR-TASK-007**：**freeflow 任务页改读 `tasks`**。理由见 §7。
- **FR-TASK-008**：进度必须是**单调且可解释**的阶段，不虚构精确百分比。
  视频任务的进度来自"第几段 / 共几段"，不是编一个数。
- **FR-TASK-009**：事件至少一次投递，客户端幂等处理；
  断线重连可重放，超出窗口触发全量同步。（已实现，视频任务的长连接要复核）

### 4.2 P1

- **FR-TASK-020**：死信队列 + 人工重放。
- **FR-TASK-021**：队列指标（等待时长、运行时长、失败率、重试率、成本）
  与堆积告警，见 [14_PLATFORM_OPERATIONS.md](./14_PLATFORM_OPERATIONS.md)。
- **FR-TASK-022**：Worker 优雅停机——停止拉新任务、等当前任务收尾或重新排队。
- **FR-TASK-023**：失联任务恢复（`running` 超过 N 分钟且 Worker 没心跳）。
- **FR-TASK-024**：任务归档与成本日汇总。

### 4.3 P2

- **FR-TASK-030**：任务优先级的产品化（`tasks.priority` 是真列，
  但没有任何地方按它排序之外的使用）。
- **FR-TASK-031**：跨项目的全局任务中心。
  ADR-030 删掉旧壳的 `/tasks` 之后，任务视图先只做项目内的。

---

## 5. 核心流程与状态机

### 5.1 状态机（已实现）

```text
queued → running → succeeded
                 → failed      （retryable 才允许回 queued）
queued/running → cancelled

ALLOWED_TRANSITIONS 里 succeeded 与 cancelled 的后继集合是空的——
终态就是终态，不存在"取消后又成功了"。
```

计费在两端：`create_task` 里 estimate + reserve，
`finish_execution` 里按 `Disposition` settle 或 release。
`finish_execution` 发现任务已经不是 `running`（执行途中被取消）时
**直接返回**，不把它改回 succeeded——用户看到的"已取消"必须是最终状态。

### 5.2 取消的真实语义（当前的坑）

现在的 `cancel_task`：

```text
标记 cancelled → 发事件 → 释放 attempt 与 attempt+1 两笔预扣
```

两笔都释放，是因为"已开跑的用当前 attempt，还没开跑的预扣记在 attempt+1 上"，
两个都试一遍才能保证预扣一定被释放。这一段是对的。

**缺的是第二半**：Worker 不知道。`execute_task` 只在开头调一次
`begin_execution`（不是 `queued` 就返回 `skipped`），之后一路跑到底。
出图任务几秒钟，看不出问题；视频任务 5–10 分钟，用户取消后：

- 数据库说"已取消"、Credits 已退；
- 上游还在生成，那笔钱平台照付；
- 生成完了 `finish_execution` 发现不是 `running`，把结果**丢掉**。

**最小修法**（P0，随视频接入一起做）：把任务拆成段以后，
每段开始前查一次 `tasks.status`；发现已取消就停在段边界，
不必要求 Provider 支持中止。这不需要新的取消协议，
只需要长任务在自己的自然断点上回头看一眼。

### 5.3 事件投递（已实现）

```text
业务事务：改 tasks.status + 写 outbox_events（同一事务）
    ↓
中继（随 Worker 跑）：读未发布的 outbox → XADD 到 Redis Stream → 标记 published
    ↓
SSE：客户端带 Last-Event-ID 连上来，先补历史再阻塞读
```

序号用**独立数据库序列**而不是 `created_at`（同一毫秒内顺序不确定）
也不是 UUID 主键（不单调）。Stream 条目 id 直接当 SSE 的 `id`（ADR-018）。

---

## 6. 队列隔离（FR-TASK-005）

> 旧文档（`12_MVP_Roadmap.md` 与本模块上一版）提的是"独立队列：
> video、audio、render"。**判断：仍然需要，但不按五类拆，按三条拆。**

### 6.1 为什么还需要

不是为了"看起来整齐"，是三个具体的失败：

1. **长任务饿死短任务。** `max_jobs=8` 是全局的。26 个镜头 × N 段的视频
   批量提交后，8 个槽位全被 5–10 分钟的视频占住，一个 3 秒的文本改写
   要排在它们后面。用户改一句台词等十分钟。
2. **超时值只能有一个。** `job_timeout=900` 对视频是合理的，
   对文本是灾难：一次卡住的文本调用会占住一个槽位 15 分钟才被判死。
3. **资源维度不同。** 视频和 TTS 是**网络等待 + 上游配额**；
   `timeline.render` 是本机 **CPU**（ffmpeg）。把它们放同一个池里，
   一批合成能把机器 CPU 打满，连带拖慢所有网络任务的事件循环。

### 6.2 建议的三条队列（不是五条）

| 队列 | 装什么 | 并发 | 超时 | 理由 |
|---|---|---|---|---|
| `default` | 文本（Agent 步骤）、`image.generate`、`audio.tts`、`mock.*` | 8 | 180 s | 都是几秒到一分钟的网络任务，共用一个池没有问题；TTS 虽然是新类型，但耗时量级与出图相同 |
| `video` | `video.generate` | 2–4 | 1800 s | 长、贵、受上游并发限流约束。并发上限的真实约束是 Provider 的配额，不是我们的机器 |
| `render` | `timeline.render` | = CPU 核数 | 1800 s | 唯一的 CPU 密集任务，与网络任务抢的不是同一种资源 |

**为什么不按 text / image / video / audio / render 五条拆**：
五条队列意味着五份 Worker 部署、五套指标、五个容量参数要调，
而我们要解决的问题只有"长短混跑"和"网络 / CPU 混跑"两个。
真正需要独立限流的是**上游并发**，那一层在 Gateway 的熔断与限流上，
不该用队列去模拟。等到某个能力真的出现独立的容量问题，再从
`default` 里拆出来——那时候拆的理由是可观测数据，不是猜测。

### 6.3 实现要点

Arq 的队列是**队列名 + 独立 Worker 进程**：`WorkerSettings.queue_name`
分开，`create_task` 入队时按 `task_type` 选队列名。
`docker-compose.yml` 与生产部署要相应地多两个 Worker 服务
（共用同一个 `aigc-studio-backend` 镜像，只是启动参数不同——
镜像分家会出现"api 好好的 worker 崩了"）。

**中继只能跑在其中一个 Worker 上**（现在挂在 `on_startup`）。
多个 Worker 各跑一份中继会重复投递同一批事件。这条必须在拆队列时一起处理，
否则拆完当天就会看到事件翻倍。

---

## 7. freeflow 任务页为什么必须改读 `tasks`（FR-TASK-007）

现在 `components/freeflow/project/task-center.tsx` 读的是
`projects.runs()` 返回的 `AgentRun`，外加一段写死的 `MOCK_TASKS`。
四条理由，任意一条都足够：

1. **`tasks.status` 是执行状态的唯一权威（ADR-008）。**
   `agent_runs` 记的是"某个 Agent 跑了一次"，它与任务不是一张表，
   `task_id` 与 `run_id` 也对不上（该文件第 126 行自己写了这一点）。
   拿 `agent_runs` 当任务列表，等于在界面上维护第二份执行真相。
2. **出图、视频、配音、合成根本不产生 `agent_runs`。**
   它们是 `tasks` 行。用 Run 列表当任务中心，**逐镜 MP4 的每一步都看不见**——
   而那正是 M2 唯一要变绿的链路。
3. **`agent_runs` 没有 SSE 通道。** 项目 SSE 推的是 `tasks` 的快照。
   读 Run 就只能轮询；改读 `tasks` 之后进度是推过来的。
4. **进度、成本、attempt、错误码、重试按钮全在 `tasks` 上。**
   `agent_runs` 没有 `progress` 列，所以现在的页面只能画不确定进度条
   并注明"进度未落库"——那句话对 `agent_runs` 是真的，对 `tasks` 不是。

**改法**：任务中心以 `tasks` 为主表；点开一条文本类任务时，
再按 `project_id` + 时间关联 `agent_runs` 展示 Agent 步骤详情。
**不得用 Run 列表代替 Task 列表**，反过来也不行——两者是"执行"与"推理"
两个层面，一份列表两个用途只会两头都不好用。

删旧壳时顺带丢掉 `mock.echo` / `mock.fail` 两个调试按钮
（`app/(app)/tasks/page.tsx:27`）：它们是 S4 打通链路用的，
不该出现在用户可见的页面上。

---

## 8. 数据模型与所有权

| 表 | 关键列 |
|---|---|
| `tasks` | `type`、`status`、`priority`、`progress`、`provider_id` / `model_id`、`input_json` / `output_json`、`estimated_cost` / `reserved_cost` / `actual_cost`（BIGINT）、`idempotency_key`（唯一）、`attempt` / `max_attempts`、`error_code` / `error_detail`、`counts_as_waste`、`started_at` / `finished_at` |
| `outbox_events` | `seq`（独立序列，单调）、`type`、`data_json`、`published_at` |

索引：`(org, project, created)`、`(status, priority, created)`（队列扫描）、`(created_at)`。

**`input_json` 要多记两件事**（ADR-031 代价第 2 条 +
[05_MODEL_GATEWAY.md](./05_MODEL_GATEWAY.md) §6.4）：
建任务时解析出的 `model_id`，以及它来自三层默认的哪一层。
`output_json` 记**实际**用了哪个模型与 failover 轨迹。
`tasks.model_id` 这一列已经存在，可以直接用来存"实际用的那个"。

事件信封稳定包含：id（Stream 条目 id）、seq、type、project_id、task_id、
timestamp、data。

---

## 9. API 与前端入口

```text
POST /api/v1/tasks
GET  /api/v1/tasks                       分页，按项目筛
GET  /api/v1/tasks/{id}
POST /api/v1/tasks/{id}/cancel
POST /api/v1/tasks/{id}/retry
POST /api/v1/projects/{id}/events/ticket
GET  /api/v1/projects/{id}/events        SSE
```

前端入口（ADR-030 之后唯一）：`/freeflow/projects/{id}/tasks`。
`app/(app)/tasks/page.tsx` 删除。

---

## 10. 技术、安全与可靠性

- **Job 参数只传 task_id**，Worker 自己从数据库读当前事实——
  队列消息里带大对象会在重试时用上过期数据。
- **arq 的 `WorkerCoroutine` 按参数名匹配，第一个参数必须叫 `ctx`。**
- **幂等键带 attempt 编号。** 不带的话重试会被上一次的预扣幂等挡掉，
  任务照跑但一分钱没扣——重试次数越多亏得越狠。
- **结算放在状态提交之后**：钱的操作要自己加行锁，塞进状态事务会把
  账户锁的持有时间拉长到整个收尾流程。
- **不得把"用户关闭页面"当成取消任务。**
- SSE 票据 60 秒一次性；长期 JWT 不进 URL。
- **测试 SSE 不要用 ASGITransport**：流式行为与真实 HTTP 有差异，
  只会得到关于测试工具的结论。拆成部件测 + 真实 HTTP 手工验证。
- **Worker 里的常驻中继会与测试并发跑**，断言要写成"最终一致"
  而不是"本次调用搬了几条"。

---

## 11. 模块依赖

- **依赖**：02（`project_id` 与预算）、09（estimate / reserve / settle / release）、
  05（Gateway 执行）、07（生成结果落资产）、Redis（队列与 Stream）。
- **被依赖**：04 Agent、06 出图、11 Web、**12 媒体**（三类新任务全走这里）。

---

## 12. 当前缺口与风险

1. **三类新任务只有枚举值**（`video.generate` / `audio.tts` / `timeline.render`），
   `_dispatch` 里没有分支。**不能因为枚举里有就说支持视频。**
2. **单队列 + 单超时**，视频接入后会立刻表现为"改一句台词等十分钟"（§6）。
3. **取消不落 Worker**，视频任务上的每一次取消都是平台在替上游买单（§5.2）。
4. **freeflow 任务页读错表**，逐镜 MP4 的绝大多数步骤在界面上不可见（§7）。
5. **中继与队列拆分冲突**：现在挂在 Worker 的 `on_startup`，
   拆队列后会有多份中继重复投递（§6.3）。
6. 没有死信、堆积告警、Worker 心跳；`running` 的任务如果 Worker 崩了，
   会永远停在 `running`。
7. `tasks.priority` 是真列但没有产品语义。

---

## 13. 迭代计划

1. **Wave 1**：freeflow 任务页改读 `tasks`（FR-TASK-007），删旧壳任务页与调试按钮。
2. **Wave 2 前**：队列拆三条 + 中继归位（§6）。
3. **Wave 2**：三类新任务的 `_dispatch` 分支、分段进度、段边界取消检查。
4. **P1**：死信、堆积告警、Worker 心跳、失联任务恢复。
5. **P2**：全局任务中心、优先级产品化。

---

## 14. 验收标准和测试

**已可验收**

- 重复投递不会重复生成或重复扣费；重试用独立预扣、同一 attempt 结算。
  （`tests/integration/test_task_billing_lifecycle.py`、`test_billing_ledger.py`）
- 失败任务按错误目录决定退不退钱；不可重试的错误不给重试。
- 取消后预扣一定被释放（两个 attempt 都试）。
- API / Worker 任一进程重启后状态可从 `tasks` + `outbox_events` 恢复。
- 租户只能看到和操作自己的任务。（`test_tenant_isolation.py`）
- SSE 断线重连后与数据库最终一致；游标过期触发全量同步。

**新增（P0 做完才算）**

- 一批 26 个镜头的视频任务排队时，同项目的一次文本改写**不排在它们后面**
  （队列隔离生效）。
- 取消一个正在跑的视频任务，Worker 在下一段开始前停下，
  上游不再产生新的调用；已发生的那一段费用照实结算而不是全额释放。
- freeflow 任务页能看到出图、配音、视频、合成四类任务，
  且状态与 `GET /tasks` 一致；页面上没有任何写死的示例任务。
- 视频任务的进度是"第 k 段 / 共 n 段"，重启后不倒退。
- `timeline.render` 跑满 CPU 时，同一台机器上的出图任务延迟没有明显变化。
