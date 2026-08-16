# AICG Studio 技术架构

## 1. 总体架构

```text
Browser
  |
  v
Next.js Web
  |
  v
API Gateway / FastAPI
  |
  +--> Auth / User
  +--> Project Service
  +--> Agent Engine (LangGraph)
  +--> Task Service
  +--> Asset Service
  +--> Skill Service
  +--> Billing Service
  +--> Runtime Service
  |
  +----------------------------+
                               |
                         Redis / Queue
                               |
                 +-------------+-------------+
                 |                           |
               Workers                 Scheduler
                 |                           |
          +------+-------+          +--------+--------+
          |              |          |                 |
      AI Gateway     Runtime GW   Cloud Adapter   Node Registry
          |              |          |
    +-----+-----+    +---+---+      +--+--+
    |     |     |    |       |      |     |
  LLM   Image Video ComfyUI SSH   AutoDL Other
          |
     External APIs

PostgreSQL <---- All services
Object Storage <---- Assets
Observability ---- Logs/Metrics/Tracing
```

## 2. 服务边界

### API Service
外部 REST API、鉴权、项目管理、用户请求。

### Agent Service
LangGraph 图、状态持久化、Agent/Tool 调度。

### Task Service
异步 Job 创建、状态、重试、优先级、取消。

### AI Gateway
统一 LLM/Image/Video/Audio Provider。

### Runtime Gateway
统一本地 ComfyUI、远程 ComfyUI、SSH Node、vLLM、Ollama 等执行节点。

### Billing Service
Credits 钱包、流水、预扣、扣款、退款。

### Asset Service
Object Storage 元数据、缩略图、版本、权限。

### Cloud Service
AutoDL 及其他云 GPU Provider。

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
- Celery/Arq/BullMQ 任选其一
- MVP 推荐 Redis + Celery/Arq

Database:

- PostgreSQL
- pgvector

Storage:

- MinIO（开发）
- S3/OSS/COS/R2（生产）

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
