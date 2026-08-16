# Credits / Token 计费系统

> 本文档定义**账务机制**（怎么记账）。
> **定价与成本模型**（收多少钱、成本多少）见 `19_UnitEconomics.md`。
> 两者不要混在一起。

## 1. 用户侧货币

统一名称：Credits。

**汇率（一经公布不可更改）：**

```text
1 Credit = ¥0.01
100 Credits = ¥1
```

调价通过调整"每个能力消耗多少 Credits"实现，**不要动汇率**。

参考量级（标准档 5 分钟漫剧）：成本约 15000 Credits，售价约 28800 Credits。

## 2. Ledger 模型

绝对禁止只保存：

```text
users.balance
```

必须有不可变流水：

```text
credit_accounts
credit_transactions
```

每次扣费：

```text
reserve
→ execute
→ settle
```

失败：

```text
release reserve
```

## 3. 交易类型

```text
TOPUP
RESERVE
CONSUME
RELEASE
REFUND
ADJUST
GPU_CHARGE
ADMIN_GRANT
```

## 4. Cost / Price

模型成本与用户售价必须分离。

例如：

```text
provider_cost = 1.00
platform_price = 1.50
```

平台毛利 = 0.50。

## 5. 预扣

视频类任务启动前必须估算：

```text
estimated_cost
```

先 reserve Credits。

例如账户只有 50 Credits：

```text
任务估算 80 Credits
→ 不允许开始
```

## 6. 幂等

所有扣费都需要：

```text
idempotency_key
```

禁止因 worker 重试造成重复扣费。

## 7. GPU 租赁

GPU 租赁不一定扣 Credits，可以单独：

```text
GPU Balance
```

MVP 建议仍统一 Credits，但内部 ledger 增加 resource_type：

```text
AI_CREDITS
GPU_SECONDS
```

## 8. 赠送额度分账

充值赠送必须与本金**分开记账**（`credit_transactions.is_bonus`）：

```text
充值 ¥298 → TOPUP 29800（本金） + ADMIN_GRANT 2200（赠送）
```

消费时**优先扣赠送额度**，退款时**只退本金部分**。
不分账会导致退款金额算错，是财务事故的常见来源。

## 9. 并发安全

扣费必须 `SELECT ... FOR UPDATE` 锁账户行，或乐观锁 + 版本号重试。
**绝不允许读-改-写无锁。**

## 10. 成本熔断

余额充足不等于应该继续花钱。必须实现项目级 / 任务级 / 用户日级 / 平台级
四层熔断，见 `19_UnitEconomics.md` 第 7 节。

其中 `task_cost_cap` 来自真实事故经验：一个"生成 5 秒"写成"生成 5 分钟"
的参数错误，配合自动重试，可以在半小时内烧掉四位数。
