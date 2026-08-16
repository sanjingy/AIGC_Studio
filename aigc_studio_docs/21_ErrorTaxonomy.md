# 错误分类与重试策略

> `05_ModelRuntimeSpec.md` 第 6 节定义了 Provider failover 的优先级，
> 但没有定义**什么情况下触发 failover**。没有这份文档，failover 落不了地。

---

## 1. 错误码规范

```text
<domain>.<category>.<specific>

例：
provider.rate_limit.qps_exceeded
provider.content.rejected
runtime.comfyui.model_missing
billing.credit.insufficient
agent.output.schema_invalid
```

统一响应体：

```json
{
  "error": {
    "code": "provider.rate_limit.qps_exceeded",
    "message": "供应商限流，正在切换备用通道",
    "user_message": "生成排队中，请稍候",
    "retryable": true,
    "retry_after_ms": 2000,
    "trace_id": "01J..."
  }
}
```

> `message` 给开发看，`user_message` 给用户看。
> 绝不把上游原始报错直接透给用户——既暴露供应商信息，又难以理解。

---

## 2. 错误分类与处置矩阵

| 类别 | 示例 | 可重试 | 换 Provider | 退还预扣 | 计入废片率 |
|---|---|---|---|---|---|
| **瞬时故障** | 超时、连接重置、502 | ✅ 指数退避 | 3 次后切 | 最终失败才退 | ❌ |
| **限流** | QPS/并发超限 | ✅ 按 retry_after | 立即切 | 最终失败才退 | ❌ |
| **供应商故障** | 5xx、模型下线 | ✅ 1 次 | 立即切 | 最终失败才退 | ❌ |
| **余额不足（平台侧）** | 上游账户欠费 | ❌ | 立即切 + 告警 | 全额退 | ❌ |
| **参数错误** | 分辨率不支持、时长超限 | ❌ | ❌ | 全额退 | ❌ |
| **内容拒绝** | 上游安全策略拦截 | ⚠️ 改写后 1 次 | ⚠️ 谨慎 | 全额退 | ✅ |
| **结果质量不达标** | 一致性分数低于阈值 | ✅ 换 seed | ❌ | ❌ 继续扣 | ✅ |
| **用户主动重做** | 用户不满意 | ✅ | 用户选 | ❌ 继续扣 | ✅ |
| **用户余额不足** | Credits 不够 | ❌ | ❌ | 释放预扣 | ❌ |
| **成本熔断** | 超出 budget_cap | ❌ | ❌ | 释放预扣 | ❌ |
| **Agent 输出非法** | 结构化输出校验失败 | ✅ 2 次 | 换 LLM | — | ❌ |
| **系统 Bug** | 未捕获异常 | ❌ | ❌ | 全额退 | ❌ |

**两条关键区分：**

1. **"可重试"和"该不该收钱"是两回事。**
   系统原因的重试不能扣用户钱；质量不达标的重试要扣
   （因为上游确实产生了成本），但要在报价区间内、受 `budget_cap` 约束。

2. **"计入废片率"决定 `19_UnitEconomics.md` 的成本模型准不准。**
   只有真正消耗了上游算力却没产出可用结果的，才计入。

---

## 3. 重试策略

```python
# 瞬时故障 / 限流 / 供应商故障
base_delay = 1s
max_attempts = 3
delay = base_delay * (2 ** attempt) * jitter(0.5, 1.5)
# 1s → 2s → 4s，加随机抖动防止雪崩
```

```python
# 内容拒绝：不要盲目重试，先改写
attempt 1: 原 prompt
attempt 2: LLM 改写 prompt（去除可能触发的表述）后重试
attempt 3: 放弃，提示用户调整描述
```

```python
# 质量不达标：换 seed 而非换 prompt
attempt 1..N: seed = base_seed + attempt
max_attempts = min(3, remaining_budget / unit_cost)
# 预算不够就不重试，直接交付并标记待人工处理
```

---

## 4. Failover 触发条件

```text
Provider 进入 DEGRADED：
  滑动窗口 5 分钟内
    错误率 > 30%   或
    P95 延迟 > 基线 3 倍  或
    连续失败 5 次

DEGRADED 行为：
  权重降为 10%，仍发送探测流量

Provider 进入 DOWN：
  DEGRADED 状态下再连续失败 10 次
  或返回明确的不可用信号（账户欠费、模型下线）

DOWN 行为：
  权重 0，每 60 秒发一次健康探测
  连续 3 次探测成功 → 恢复到 DEGRADED → 5 分钟无异常 → 恢复 HEALTHY
```

**熔断状态必须持久化到 Redis 并全 Worker 共享**，
否则每个 Worker 各自熔断，等于没熔断。

---

## 5. 幂等

所有会产生成本的操作必须携带 `idempotency_key`：

```text
Idempotency-Key: <uuid>       # HTTP 头，客户端生成
```

服务端约定：

- key 存 Redis，TTL 24 小时，value 为首次响应
- 相同 key 重复请求，直接返回首次响应，不重新执行
- 提交给上游 Provider 时，透传或派生该 key（支持的 Provider）
- Worker 重试时**复用同一个 key**，这是防止重复扣费的关键

`08_BillingCredits.md` 第 6 节只说了"需要 idempotency_key"，
本节补齐了它在 API 层和 Worker 层的具体约定。

---

## 6. 用户可见的降级

失败不等于什么都没有。降级顺序：

```text
高质模型失败 → 自动降到标准模型，告知用户"已使用备用通道"
标准模型失败 → 降到经济模型，标记该镜头"质量待确认"
全部失败     → 该镜头保留静态首帧（已生成的图），标记为待重做
              整片仍可合成交付，不因单镜失败而整体失败
```

**原则：一部 60 镜的片子，不能因为第 43 镜失败就全盘皆输。**
时间线渲染必须支持"占位镜头"，允许带缺口交付后再补。

---

## 7. 可观测性要求

每个错误必须记录：

```text
trace_id, task_id, project_id, user_id
provider_id, model_id, runtime_id
error_code, attempt, total_attempts
upstream_status, upstream_body_digest   -- 摘要，不存全文
cost_incurred                            -- 失败也可能产生成本
duration_ms
```

按 `error_code` 聚合的日报是运营的核心输入：
它直接告诉你该换掉哪个 Provider、该修哪个 prompt。
