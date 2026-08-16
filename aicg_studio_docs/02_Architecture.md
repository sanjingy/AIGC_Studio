# AICG Studio 技术架构

> 2026-08-16 修订：由 7 微服务改为**模块化单体**（ADR-009），
> 队列定为 Arq（ADR-010），补充 SSE 实时通道（ADR-013）。
> 领域边界不变，只是不再用进程边界去表达它。

## 1. 总体架构

```text
Browser
  │  REST（操作）        SSE（状态推送）
  ▼                      ▼
Next.js Web
  │
  ▼
FastAPI 单体进程（apps/api）
  │
  ├── modules/auth          鉴权、用户
  ├── modules/project       项目、版本
  ├── modules/asset         对象存储元数据、预签名直传
  ├── modules/task          异步 Job 生命周期 ★状态唯一权威
  ├── modules/agent         Agent 编排（LangGraph 仅做单步推理）
  ├── modules/skill         Skill 注册与校验
  ├── modules/billing       Credits Ledger
  ├── modules/runtime       Runtime 注册与调度
  ├── modules/realtime      SSE 事件通道
  ├── modules/moderation    合规审核（M1 空实现）
  └── modules/cloud         云 GPU（预留）
  │
  ▼
Redis  ── 队列 / 缓存 / 事件流 / 熔断状态
  │
  ▼
Arq Worker（worker/）
  │
  ├── AI Gateway ────── 商业 API（LLM / 图 / 视频 / 音频）
  ├── Runtime Gateway ─ ComfyUI / SSH Node / vLLM / Ollama    [M4]
  ├── Consistency ───── 一致性引擎（条件化 / 度量 / LoRA）      ★
  ├── Timeline ──────── 音频优先装配 + ffmpeg 渲染             ★
  └── Cloud Adapter ─── AutoDL 等                              [预留]

PostgreSQL + pgvector  ←── 全部持久化（pgvector 存角色人脸 embedding）
Object Storage (MinIO/OSS)  ←── Assets
Observability ── Logs / Metrics / Tracing
```

## 2. 模块边界

进程只有 2 个，但**领域边界必须严格**，靠 lint 强制
（见 `11_ProjectStructure.md`）。模块职责：

### auth / project / asset
鉴权、项目管理、资产元数据。资产上传走**预签名直传**，不经过 API 进程。

### task ★
异步 Job 创建、状态、重试、优先级、取消。
**`tasks.status` 是全系统执行状态的唯一权威**（ADR-008）。

### agent
Agent 编排与 Tool 调度。LangGraph 仅用于单步推理，**不持有跨请求状态**。

### realtime
SSE 事件流，Transactional Outbox 保证不丢事件。见 `20_Realtime.md`。

### billing
Credits 钱包、流水、预扣、扣款、退款、成本熔断。

### moderation
生成前后的内容审核钩子。M1 为空实现，但**接口位置必须先占住**
（见 `18_Compliance.md`）。

### runtime / cloud
执行节点注册与调度；云 GPU Provider。M4 及以后。

### 独立包（不在 modules 下）

- `consistency/` — 一致性引擎，核心资产
- `timeline/` — 时间线模型与 ffmpeg 渲染

## 3. 推荐技术栈

Frontend:

- Next.js
- TypeScript
- Tailwind CSS
- shadcn/ui
- Zustand
- TanStack Query

Backend:

- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic

Agent:

- LangGraph
- LangChain（按需）
- Pydantic Structured Output

Queue:

- Redis
- **Arq**（已定，见 ADR-010。不再是"任选其一"）

Realtime:

- SSE（主通道，见 ADR-013 与 `20_Realtime.md`）
- WebSocket（仅用于 Node Agent 长连接）

Media:

- ffmpeg（时间线渲染）

Database:

- PostgreSQL 16+
- **pgvector — 用途明确为存储角色人脸 embedding**，
  用于一致性检索与校验，不用于 RAG（见 `17_ConsistencyEngine.md` 第 7 节）

Storage:

- MinIO（开发）
- S3/OSS/COS/R2（生产）
- **大文件走预签名直传，不经过 API 进程**

Deployment:

- Docker Compose（MVP）
- 后期 Kubernetes

## 4. 解耦原则

业务层不能 import 某个模型 SDK 后到处调用。

错误：

```python
seedance.generate(...)
```

正确：

```python
provider = model_registry.resolve(capability="text_to_video")
result = await provider.generate(request)
```

## 5. 三个统一接口

### Capability
“需要什么能力”。

### Provider
“哪家服务提供这个能力”。

### Runtime
“在哪里执行”。

这三者必须分开。

## 6. 状态权威（ADR-008）

系统中曾存在四套并行状态（LangGraph checkpoint、`tasks`、`agent_runs`、队列），
必然导致互相矛盾。现统一为：

```text
tasks.status      → 执行状态的唯一权威，前端只信它
agent_runs/steps  → 只记录推理过程，供审计与回放，不参与状态判断
approvals         → 只记录人工决策
LangGraph         → 无状态单步推理
```

**推论**：任何进程重启后，系统状态可完全从 PostgreSQL 恢复，
不依赖任何内存或图状态。

## 7. 成本参数化（ADR-014）

**代码中不允许出现任何价格常量。**

```text
model_pricing   表    上游单价（带 effective_from，支持历史追溯）
pricing_rules   表    废片率、overhead、熔断阈值，热更新
```

理由见 `19_UnitEconomics.md`：上游调价是常态
（DeepSeek 2026-08-17 高峰输出价上涨 350%），
硬编码价格会让毛利在一夜之间转负。
