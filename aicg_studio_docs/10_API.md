# API 设计草案

API Prefix：

```text
/api/v1
```

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

```text
POST /assets/upload
GET /assets/{id}
GET /projects/{id}/assets
```

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

## Cloud GPU

```text
GET /gpu/products
POST /gpu/instances
GET /gpu/instances/{id}
POST /gpu/instances/{id}/stop
POST /gpu/instances/{id}/release
```

## Credits

```text
GET /credits/balance
GET /credits/transactions
POST /credits/estimate
POST /orders/topup
```

## Future Public API

预留：

```text
POST /v1/images/generations
POST /v1/videos/generations
POST /v1/chat/completions
```

内部 API 与公网 API 必须分离认证和限流策略。
