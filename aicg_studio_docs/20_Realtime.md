# 实时通道设计

> `16_FirstMilestone.md` 要求"页面状态实时变化、Agent 状态可视化"，
> 但 `10_API.md` 全是 REST，没有任何实时通道。本文档补齐。

---

## 1. 为什么必须是一等公民

需要推送到前端的事件流：

- Agent 逐步推理输出（流式文本）
- 任务队列位置与进度百分比
- ComfyUI 的 `executing` / `progress` / `executed` 事件
- 视频生成的长时间等待（30 秒到 10 分钟）
- 审核门开启通知
- Credits 余额变动
- 节点上下线

轮询扛不住：一个用户同时跑 60 个镜头任务，1 秒轮询 = 每用户 60 QPS。

---

## 2. 技术选型：SSE 为主，WebSocket 为辅

| | SSE | WebSocket |
|---|---|---|
| 方向 | 服务端 → 客户端 | 双向 |
| 断线重连 | 浏览器原生支持 + `Last-Event-ID` | 需自己实现 |
| 经过 Nginx / CDN | 简单 | 需额外配置 |
| 实现复杂度 | 低 | 中 |

**决策：默认 SSE。** 本系统 95% 的实时需求是单向下行，
客户端的操作（审核、取消、重试）走普通 REST 即可，不需要双向通道。

WebSocket 只在两个场景使用：
1. Node Agent ↔ 平台的长连接（任务下发 + 心跳）
2. 未来的多人协同编辑（不在 MVP 范围）

---

## 3. 端点设计

```text
GET /api/v1/projects/{id}/events        # 项目级事件流（主通道）
GET /api/v1/tasks/{id}/events           # 单任务事件流（可选，用于详情页）
GET /api/v1/agent-runs/{id}/stream      # Agent 流式输出
```

前端默认只订阅**项目级主通道**，避免一个页面开 60 条连接。

---

## 4. 事件协议

统一信封：

```json
{
  "id": "evt_01J...",
  "seq": 1287,
  "type": "task.progress",
  "project_id": "proj_...",
  "ts": "2026-08-16T10:23:45.123Z",
  "data": { }
}
```

事件类型清单：

```text
agent.step.started
agent.step.delta          # 流式 token
agent.step.finished
agent.run.failed

task.created
task.queued               # data: { queue_position }
task.started
task.progress             # data: { percent, stage, eta_seconds }
task.succeeded
task.failed               # data: { error_code, retryable }
task.cancelled

approval.requested        # data: { gate, payload_ref }
approval.resolved

asset.created

credit.reserved
credit.settled
credit.released
credit.insufficient       # 需要立刻打断用户

node.status_changed
```

---

## 5. 顺序与可靠性

**问题**：SSE 断线重连期间的事件会丢。视频任务跑 10 分钟，
用户切个标签页回来，进度条卡在 30% 但任务其实已经完成了。

**方案：单调递增 seq + 事件重放。**

```text
1. 每个 project 维护一个单调递增的 seq（Redis INCR）
2. 事件写入 Redis Stream，保留最近 1000 条 / 24 小时
3. 客户端重连时带 Last-Event-ID: <seq>
4. 服务端从该 seq 之后重放
5. seq 缺口过大（超出保留窗口）→ 下发 sync.required
   客户端改为全量拉取项目状态
```

**客户端必须保证幂等**：同一事件收到两次，UI 状态不变。
所有事件携带最终状态而非增量指令（推 `status: succeeded`，
不推 `请把状态改成成功`）。

---

## 6. 与 Task Service 的关系

事件由 Worker 在状态变更时**与数据库写入同一事务边界内**投递，
避免"数据库已更新但事件没发"或"事件发了但事务回滚"。

实现方式（二选一，MVP 推荐前者）：

```text
A. Transactional Outbox
   业务事务内写 outbox 表 → 独立轮询进程读 outbox → 投递到 Redis Stream
   优点：不丢事件；缺点：多一次延迟（可控制在 100ms 内）

B. 直接投递 + 补偿
   事务提交后立即投递，失败进重试队列
   优点：简单；缺点：极端情况会丢
```

---

## 7. 连接管理

- 每用户最大并发 SSE 连接：**3**（超出拒绝，防止标签页泄漏）
- 心跳：每 15 秒下发 `: keepalive` 注释行，防止中间层断开
- 服务端最长连接时长：30 分钟，到期主动关闭让客户端重连
- Nginx 必须配置 `proxy_buffering off;` 否则 SSE 会被缓冲住

---

## 8. 鉴权

SSE 无法自定义请求头（`EventSource` 限制）。方案：

```text
1. 客户端先 POST /api/v1/projects/{id}/events/ticket
   → 返回一次性 ticket（TTL 60s，绑定 user_id + project_id）
2. GET /api/v1/projects/{id}/events?ticket=xxx
```

**不要把长期 JWT 放进 URL query**——会进 Nginx access log。
