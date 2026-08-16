# API 设计草案

API Prefix：

```text
/api/v1
```

## 通用约定

### 幂等

所有会产生成本的 POST 请求必须携带：

```text
Idempotency-Key: <uuid>
```

服务端行为见 `21_ErrorTaxonomy.md` 第 5 节。

### 错误响应

统一错误体，含 `code` / `message`（给开发）/ `user_message`（给用户）/
`retryable` / `trace_id`，见 `21_ErrorTaxonomy.md` 第 1 节。

### 分页

统一游标分页：`?cursor=&limit=`，响应含 `next_cursor`。
不使用 offset 分页（大表性能差且结果不稳定）。

## Auth

```text
POST /auth/register
POST /auth/login
POST /auth/refresh
```

## Project

```text
POST /projects
GET /projects
GET /projects/{id}
PATCH /projects/{id}
DELETE /projects/{id}
```

## Creative Session

```text
POST /projects/{id}/creative-session
POST /projects/{id}/route
POST /projects/{id}/approve
POST /projects/{id}/revise
```

## Agents

```text
GET /agents
GET /agents/{id}
POST /projects/{id}/agent-runs
GET /agent-runs/{id}
```

## Skills

```text
GET /skills
POST /skills
POST /skills/upload
POST /skills/{id}/publish
POST /skills/{id}/validate
```

## Assets

> 2026-08-16 修订：原 `POST /assets/upload` 走 API 代理上传。
> 视频素材单文件可达数百 MB，代理上传会打死 API 进程。
> 改为**预签名直传**。

```text
POST /assets/upload-url        # 请求预签名 URL
                               # body: { filename, mime_type, size_bytes }
                               # resp: { asset_id, upload_url, fields, expires_at }
PUT  <upload_url>              # 客户端直传对象存储，不经过 API
POST /assets/{id}/complete     # 通知完成，触发校验/转码/审核
GET  /assets/{id}
GET  /assets/{id}/download-url # 预签名下载 URL，带 TTL
GET  /projects/{id}/assets
```

约束：

- 预签名 URL TTL ≤ 15 分钟
- `POST /assets/{id}/complete` 后才校验 checksum 与 size，
  不匹配则标记为无效
- 未 complete 的 asset 24 小时后由清理任务回收

## Tasks

```text
POST /tasks
GET /tasks/{id}
POST /tasks/{id}/cancel
POST /tasks/{id}/retry
```

## Runtime

```text
GET /runtimes
POST /runtimes/register
POST /runtimes/{id}/heartbeat
GET /runtimes/{id}/health
```

## Realtime（新增）

```text
POST /projects/{id}/events/ticket    # 换取一次性 ticket，TTL 60s
GET  /projects/{id}/events?ticket=   # SSE 主通道
GET  /agent-runs/{id}/stream         # Agent 流式输出
```

协议、事件类型、断线重放见 `20_Realtime.md`。
**不要把长期 JWT 放进 URL query**——会进 Nginx access log。

## Timeline（新增，M2）

```text
GET  /projects/{id}/timeline
PATCH /projects/{id}/timeline           # 调整片段时长/顺序/字幕
POST /projects/{id}/timeline/render     # 触发 ffmpeg 渲染
GET  /projects/{id}/timeline/preview    # 低码率预览
```

## Credits

```text
GET  /credits/balance
GET  /credits/transactions
POST /credits/estimate       # 调用 19 号文档第 6 节的估价公式
POST /orders/topup
```

## Cloud GPU（预留，不实现）

```text
GET  /gpu/products
POST /gpu/instances
GET  /gpu/instances/{id}
POST /gpu/instances/{id}/stop
POST /gpu/instances/{id}/release
```

见 `07_AutoDL.md`，方案待定。

## Future Public API（M6+，不预留路由）

原方案在此预留 OpenAI 兼容端点。**当前不预留**——
内部 API 还没稳定，预留公网契约只会变成负债。

将来实现时的硬约束：**公网 API 与内部 API 必须分离认证、
限流、配额与计费策略**，且不能复用内部 handler。
