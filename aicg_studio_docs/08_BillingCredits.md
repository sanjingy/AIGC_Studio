# Credits / Token 计费系统

## 1. 用户侧货币

统一名称：Credits。

示例：

```text
100 Credits
1000 Credits
10000 Credits
```

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
