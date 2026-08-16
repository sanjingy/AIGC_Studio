# AutoDL 云 GPU 联动方案

## 1. 商业模式

用户在 AICG Studio 选择：

```text
RTX 4090-48G
租赁 1 小时
```

平台：

1. 收取用户 Credits 或租赁费用
2. 使用平台已绑定的 AutoDL 账号
3. 调用 AutoDL API 创建新实例
4. 等待实例 Running
5. 自动初始化 AICG Node Agent / ComfyUI
6. 将实例注册为 Runtime Node
7. 用户开始使用
8. 计时
9. 到期后自动停止/释放
10. 写入 GPU Usage 与财务流水

AutoDL 官方 Pro API 支持通过开发者 Token 鉴权，并提供创建、状态查询、开机、关机和释放实例等接口；创建实例需要 GPU 规格、镜像、CUDA 下限等参数。citeturn905503search0

## 2. 强烈建议：平台不把 AutoDL 账号权限暴露给终端用户

数据库：

```text
autodl_account
  encrypted_token
  status
  quota
  last_sync_at
```

用户只看到：

```text
AICG GPU Pool
```

## 3. Instance 生命周期

```text
REQUESTED
 ↓
CREATING
 ↓
BOOTING
 ↓
INSTALLING
 ↓
ONLINE
 ↓
ALLOCATED
 ↓
RUNNING
 ↓
DRAINING
 ↓
STOPPING
 ↓
RELEASED
```

## 4. 镜像策略

预制镜像：

```text
AICG-ComfyUI-Base
AICG-Wan
AICG-Flux
AICG-LTX
AICG-AllInOne
```

优先使用平台维护的私有镜像，减少用户每次启动时重新安装依赖的时间。

AutoDL 官方文档说明实例环境与数据在关机后保留，但连续关机 15 天实例可能被释放，因此平台侧仍需维护自己的状态与用户资产备份。citeturn905503search1

## 5. 学生优惠商业模型

内部成本：

```text
provider_cost
```

用户售价：

```text
customer_price
```

利润：

```text
customer_price - provider_cost
```

不要把价格写死在 AutoDL Adapter 中，应由 Pricing Rule 管理。

## 6. 计费策略

按分钟/秒计费，建议内部最小计费单位统一为秒。

用户界面可以展示：

```text
¥X / 小时
```

财务内部：

```text
usage_seconds × internal_rate
```

## 7. MVP 风险

必须处理：

- AutoDL 创建失败
- GPU 无货
- 镜像失效
- 启动超时
- Node Agent 注册失败
- ComfyUI 启动失败
- 用户断线
- 用户超时不释放
- AutoDL 账户余额不足
- 平台实际 GPU 成本高于用户售价
