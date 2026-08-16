# AutoDL 云 GPU 联动方案

> **状态：预留，方案待定，不排期。**
>
> 2026-08-16 评审意见：本方案重运维、低毛利、边界情况极多（见第 7 节，
> 共 10 类失败模式），且对 `19_UnitEconomics.md` 第 8 节的核心指标
> （每天 16 部成片）几乎没有贡献。
>
> 本文档作为**思路保留**，等 M3 拿到真实运营数据后重新评估：
> - 有多少用户真的卡在 GPU 上？
> - 用户自带 GPU（M4）能否覆盖这部分需求？
> - 如果做，是自营 GPU 池、代理转售，还是只做撮合？
>
> 在重新评估之前，只保留 `CloudProvider` Adapter **接口**（ADR-004），
> 不实现具体逻辑，`09_Database.md` 第 11 节的表也不落地。

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

AutoDL 官方 Pro API 支持通过开发者 Token 鉴权，并提供创建、状态查询、开机、关机和释放实例等接口；创建实例需要 GPU 规格、镜像、CUDA 下限等参数。

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

AutoDL 官方文档说明实例环境与数据在关机后保留，但连续关机 15 天实例可能被释放，因此平台侧仍需维护自己的状态与用户资产备份。

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
