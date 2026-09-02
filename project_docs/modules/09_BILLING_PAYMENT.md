# 09 Credits、充值与支付

> 状态：**部分实现**（账本与消费生命周期已实现；充值只有一个不设防的开发端点，支付未实现）
> 优先级：**P1**（账本部分是 P0 且已完成；支付本身**不做**，见 §4.4）
> 负责人：待定
> 最近核对：2026-09-02
> 权威顺序：[DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §6 / §1 > 当前代码 > ADR-005 / 014 / 025 > `19_UnitEconomics.md`（历史文档）

---

## 1. 模块目标与边界

保证每一次花钱的动作都**可估算、可预扣、可结算、可释放、可对账**。
本模块拥有 Credits 账户与不可变流水、价格与规则表、BYOK 凭证。

**明确不拥有**：真实支付。决策记录 §1 把支付列进"明确不做（M2 内）"，
§6 写明**首批用户由负责人手动加 Credits**。所以本文档里没有 Payment 子模块，
只有一节说明手动充值怎么安全地做（§5.3）。

Credits 汇率：`1 Credit = ¥0.01`。**汇率一旦公布不可更改**——
调价通过调整"每个能力消耗多少 Credits"实现，不动汇率
（`19_UnitEconomics.md` §4.1）。

---

## 2. 用户与使用场景

1. 生成前看到**估算区间**，知道这一步大概花多少。
2. 生成中失败，钱要退回来，而且看得见是"释放"了。
3. 随时能看余额、看每一笔花在哪个项目 / 哪个任务上。
4. 自带 Key 时价格明显更便宜（ADR-025），且能确认折扣真的生效了。
5. **额度用完了**——首批用户找负责人加，不是自己充值（决策记录 §6）。

---

## 3. 当前真实能力

状态词按 [DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §0。

| 能力 | 状态 | 代码 / 测试证据 |
|---|---|---|
| Credits 账户（`balance` / `reserved`），按 org 一份 | 已实现 | `billing/models.py::CreditAccount` |
| 不可变流水（`TOPUP` / `ADMIN_GRANT` / `RESERVE` / `CONSUME` / `RELEASE`） | 已实现 | `billing/models.py::CreditTransaction` |
| 预扣 → 结算 / 释放，行锁 + 全局唯一幂等键 | 已实现 | `billing/service.py::reserve` / `settle` / `release`；`tests/integration/test_billing_ledger.py`（20 例） |
| 幂等键带 attempt 编号（重试独立预扣） | 已实现 | `billing/service.py::_key`；`test_retry_uses_separate_reservation`、`test_reserve_and_settle_use_the_same_attempt` |
| 并发不超支（`SELECT FOR UPDATE`） | 已实现 | `test_concurrent_reserves_cannot_overspend` |
| 金额一律 `BIGINT` 最小单位，无浮点 | 已实现 | `tests/unit/test_pricing_no_constants.py::test_no_float_arithmetic_on_money` |
| 三道成本熔断：单任务 / 项目预算 / 租户日上限 | 已实现 | `billing/service.py::_check_caps`；`test_task_cost_cap_blocks_runaway`、`test_project_budget_cap_blocks` |
| 注册体验额度（`welcome_grant=500`，走 `ADMIN_GRANT` 且 `is_bonus=true`，幂等） | 已实现 | `billing/service.py::grant_welcome_credits` |
| 价格与系数全部走表，代码里没有价格常量 | 已实现 | `model_pricing` / `pricing_rules`；`tests/unit/test_pricing_no_constants.py::test_no_hardcoded_prices` |
| 估算：单价 × 份数 × 废片系数，返回 [×0.8, ×1.4] 区间 | 已实现 | `billing/pricing.py::estimate`；`POST /credits/estimate` |
| BYOK 折扣分支（`byok_unit_credits`，不叠 `overhead_rate`） | 已实现 | `billing/pricing.py::estimate`；ADR-025 |
| BYOK 凭证：AES-GCM 加密、按能力一把、只回尾号 | 已实现 | `billing/credentials.py`；`tests/integration/test_byok_credentials.py` |
| 对账函数（流水总和 == 账户总额） | 已实现 | `billing/service.py::audit`；`test_ledger_always_balances` |
| 余额 / 流水 / 估算接口 | 已实现 | `GET /credits/balance`、`/credits/transactions`、`POST /credits/estimate` |
| 前端余额展示 | 部分实现 | freeflow 顶栏能看余额；**没有消费明细页** |
| `POST /credits/topup` | 部分实现 | 能加余额、幂等、本金与赠送分账；**但任何登录用户都能调用**，见 §7.1 |
| 视频 / TTS 的计费形状 | 预留 | `pricing._CAPABILITY_OF` 里有 `image_to_video` / `text_to_speech` 两个键，**没有对应的 `_shape` 分支**，落到 `log.warning("pricing.unknown_task_type")` 兜底 |
| 支付订单、回调、退款、对账 | 未实现 | 无表、无接口、无 Provider |
| 账单 / 发票 | 未实现 | — |
| 管理员调账与审计 | 未实现 | 见模块 13 |

---

## 4. 功能需求

### 4.1 P0（挡住"逐镜 MP4"）

- **FR-BIL-001**：每次生成 estimate → reserve → execute → settle/release，
  幂等、行锁、不超支。（已实现）
- **FR-BIL-002**：金额一律 `BIGINT` 最小单位，禁止浮点。（已实现）
- **FR-BIL-003**：价格、汇率、废片率不写成代码常量。（已实现）
- **FR-BIL-004**：**视频与 TTS 的计费形状**。`pricing._shape()` 要给
  `video.generate` 和 `audio.tts` 各一个分支：视频按 `units = 段数`
  且折 `video_retry_factor`，TTS 按字符数或秒数计费。
  没有这一条，Wave 2 的任务会全部落到 `unknown_task_type` 兜底价上。
- **FR-BIL-005**：**换模型必须重算 Credits 预估**（ADR-024 硬约束 1）。
  当前被违反，根因和修法在
  [05_MODEL_GATEWAY.md](./05_MODEL_GATEWAY.md) §11.1，
  修点在建任务时把解析出的 `model_id` 写进 `input_json`。
- **FR-BIL-006**：**`POST /credits/topup` 必须收口**（§7.1）。
  它今天是一个任何登录用户都能自助加余额的接口。
- **FR-BIL-007**：视频接入前把 `task_cost_cap` 调到 **6000 Credits（¥60，决策记录 §12 第 3 条）**，走 `pricing_rules` 迁移种子；第一批视频成本数据出来后重估。
  一个 12 秒镜头切 3 段、每段重试一次就可能顶到上限，
  用户会看到"单任务成本超过上限"而不知所措。走 `pricing_rules` 改值，不改代码。

### 4.2 P1

- **FR-BIL-020**：消费明细页——每笔流水关联项目、任务、产出资产，
  失败的显示"已释放"。现在有 `GET /credits/transactions` 但没有界面。
- **FR-BIL-021**：BYOK 折扣在界面上可感知（选模型时显示的 Credits 就该是折后的，
  见 [05_MODEL_GATEWAY.md](./05_MODEL_GATEWAY.md) §7 的 `key_source`）。
- **FR-BIL-022**：自动对账告警——`audit()` 已有，缺一个定时任务和告警出口。
- **FR-BIL-023**：真实废片率回填（§9.1）。

### 4.3 P2

- **FR-BIL-030**：`daily_cost_rollup` 成本 / 售价 / 毛利聚合表。
- **FR-BIL-031**：发票。
- **FR-BIL-032**：免费额度的分层设计（`19_UnitEconomics.md` §5）。

### 4.4 不做（M2 内）

决策记录 §1 与 §6 明确排除。下面这些**只能出现在这一节**，
不得写成 P0 / P1：

| 项 | 说明 |
|---|---|
| **真实支付**（订单、收银台、支付回调、幂等入账） | 首批用户由**负责人手动 `topup`**（§5.3）。不做支付，就没有 `payment_orders` / `payment_attempts` 表 |
| **退款** | 没有支付就没有退款。手动发放的额度按 `ADMIN_GRANT` + `is_bonus`，本来就不该退 |
| **充值档位与赠送比例** | `19_UnitEconomics.md` §4.3 那三档是定价设计，不是本轮要实现的功能 |
| **运营对账看板 / 毛利报表** | 见模块 13，同样不做 |

做支付前必须先有模块 13 的管理员身份与审计——顺序不能反
（一个没有审计的调账入口比没有调账更危险）。

---

## 5. 核心流程与状态机

### 5.1 AI 消费（已实现）

```text
estimate → reserve → execute → settle / release
```

- `estimate`：`billing/pricing.py::estimate`，单价来自 `model_pricing`，
  系数来自 `pricing_rules`，返回点值；对外给 [×0.8, ×1.4] 区间。
- `reserve`：`balance -= amount`，`reserved += amount`，写一条 `RESERVE`。
  预扣按区间上限扣——**先扣多了能退，扣少了要平台垫**。
- `settle`：按实际成本扣，差额退回，封顶在预扣额（`test_settle_caps_at_reserved_amount`）。
- `release`：全额退回（任务失败 / 取消）。
- 每一步幂等键 = `{action}:{task_id}:{attempt}`。
  **预扣与结算的 attempt 编号必须一致**——`begin_execution` 会自增 attempt，
  差一位会导致"任务成功了但钱没扣"，且不报任何错。

### 5.2 熔断顺序（已实现）

```text
单任务上限 task_cost_cap        （挡参数写错烧钱）
  → 项目预算 budget_cap × multiplier
  → 租户日上限 org_daily_cap
  → 余额是否够
```

顺序有讲究：先挡明显异常的参数错误，最后才看余额，
这样用户看到的错误最贴近真实原因。

### 5.3 手动充值（首批用户的唯一充值路径）

决策记录 §6：**首批用户由负责人手动加 Credits，`POST /billing/topup` 已可用**。
落到实现上，这条路径必须满足三件事，否则它就是一个财务漏洞：

1. **调用者必须是平台管理员**，不能是任意登录用户（今天不是，见 §7.1）。
2. **走 `ADMIN_GRANT` 而不是 `TOPUP`**，并标 `is_bonus=true`。
   `TOPUP` 的语义是"用户付过钱"，没有支付凭证的入账不能记成 `TOPUP`——
   将来真接支付时，对账会把这些行当成收入。
3. **必带 `note`（给谁、为什么）与操作者 id。** 现在 `note` 有列，
   操作者没有地方记（`credit_transactions` 上没有 `operator_id`）。
   补一列，或在 `note` 里写死格式——前者更好，但如果只做手动充值，
   `note` 里带上够用，不值得为它单开审计表（审计表是模块 13 的事）。

---

## 6. 数据模型与所有权

### 6.1 已有

| 表 | 关键列 | 说明 |
|---|---|---|
| `credit_accounts` | `org_id`、`resource_type`、`balance`、`reserved` | 每 org 一份，改余额必先 `SELECT FOR UPDATE` |
| `credit_transactions` | `type`、`amount`、`reserved_delta`、`balance_after`、`reserved_after`、`is_bonus`、`ref_type`/`ref_id`、`idempotency_key`（**全局唯一**）、`note` | 不可变；`balance_after` 让对账可逐行复算 |
| `pricing_rules` | `key`、`value`（BIGINT）、`note` | `task_cost_cap` / `org_daily_cap` / `project_budget_multiplier` / `image_retry_factor` / `video_retry_factor` / `overhead_rate` / `byok_unit_credits` / `welcome_grant` |
| `model_pricing` | `model_id`、`unit`、`provider_cost`、`credit_price`、生效期 | 按调用时间匹配生效版本（ADR-014） |
| `provider_credentials` | `org_id`、`capability`、`provider_id`、`key_encrypted`、`created_by` | BYOK（ADR-025 / 027）；放在 billing 是因为它存在的理由是计费折扣 |

`billing/service.py::DEFAULT_RULES` 里那份数字是**缺配时的兜底**，
不是报价依据。对外报价一律以 `pricing_rules` 表为准。

### 6.2 需要新增

- `credit_transactions.operator_id`（可空）——手动发放时记谁发的（§5.3 第 3 条）。

**不新增**：`payment_orders`、`payment_attempts`、`refunds`、`invoices`。
支付不做（§4.4），建了表也只是空壳。
`daily_cost_rollup` 是 P2，等有真实数据再说。

---

## 7. API、事件与前端入口

### 7.1 已有

```text
GET  /api/v1/credits/balance        余额（balance / reserved / total）
GET  /api/v1/credits/transactions   流水，分页
POST /api/v1/credits/estimate       估算，返回点值 + [×0.8, ×1.4] 区间
POST /api/v1/credits/topup          ⚠ 见下

GET    /api/v1/provider-credentials              BYOK 配置状态（只回尾号）
PUT    /api/v1/provider-credentials/{capability}
DELETE /api/v1/provider-credentials/{capability}
POST   /api/v1/provider-credentials/{capability}/test   免费探针
```

**`POST /credits/topup` 当前是一个不设防的接口**（`billing/router.py:64`）：
依赖只有 `CurrentUser`，任何登录用户都可以给自己的 org 加最多
1,000,000 Credits 本金 + 1,000,000 赠送，且可以反复调
（只要换 `Idempotency-Key`）。它的 docstring 写着"S5 只做记账，
用于把计费链路跑通"，但**代码上没有任何东西阻止生产用户调用它**。

修法（FR-BIL-006，做支付之前就得做）：

1. 加一个平台管理员判定（不是 `users.role`——那一列是 org 内角色，
   注册时人人都是 `owner`）。最小做法是配置项里的一个管理员用户 id 白名单，
   正式的身份体系见模块 13。
2. 生产环境（`settings.is_production`）下非管理员一律 404。
3. 入账类型改成 `ADMIN_GRANT` + `is_bonus=true`（§5.3 第 2 条）。
4. 加一条测试：普通用户调用返回 404，且余额不动。

### 7.2 前端入口

- 已有：freeflow 顶栏余额。
- P1：消费明细页（`/freeflow/billing`），按任务分组，显示预扣 / 实扣 / 释放，
  关联项目与产出资产。
- **不做**：充值页、收银台、订单页、账单页（§4.4）。
  界面上不要放一个"充值"按钮然后弹"敬请期待"——那是假入口。
  额度用完时的提示直接写"请联系管理员开通额度"。

---

## 8. 技术选择和工程设计

- **金额一律 `BIGINT` 最小单位，禁止浮点。** 浮点存钱是财务事故经典来源。
  废片系数用整数乘除（`× factor // 100`），不引入 `Decimal`。
- **改余额必先 `SELECT FOR UPDATE`，每笔带幂等键。** 幂等键全局唯一。
  测试里不能写死幂等键，否则跨轮次残留会 409。
- **流水不可变。** 冲正靠追加反向流水，不改历史行。
- **价格按调用时间匹配生效版本**（ADR-014）。DeepSeek 于 2026-08-17
  高峰输出价上涨 350%——把价格写进代码的实现会在那一夜毛利转负，
  且要改代码发版才能救。
- **表里查不到价时按 `task_cost_cap // 10` 兜底并告警**：
  宁可高估拦下来，也不要低估放行——低估会让预扣不够，差额由平台承担。
- **BYOK 分支只替换单价，不改份数**（`_Shape` 把两者拆开的原因）。

---

## 9. 安全、性能和可靠性

- `POST /credits/topup` 的收口是本模块最高优先级的安全项（§7.1）。
- BYOK 明文只在三个瞬间存在于内存：入库前加密、调用/探测时发给上游、
  读列表时算尾号。不返回前端、不进日志、不进 `input_json`。
- 每个用户侧查询都带 `org_id`；跨租户 404。余额天然隔离
  （`test_balances_are_isolated_across_orgs`）。
- `audit()` 每天跑一次并告警：流水总和 ≠ 账户总额就是账务 bug，
  应该立刻发现而不是等用户投诉。
- 赠送额度与本金分账记录（`is_bonus`），因为将来真接支付时**退款只退本金**。

### 9.1 风险（两条必须保留）

1. **盈亏平衡 = 每天 16 部成片。** `19_UnitEconomics.md` §8：
   固定成本 ¥65,000/月 ÷ 单片毛利 ¥138 ≈ 471 部/月 ≈ 每天 16 部。
   **"部"的定义随 ADR-032 已经变了**——第一条成片是"逐镜 MP4 的集合"
   而不是一条整片，每部的镜数与段数要重估，**本轮不重估**，
   等第一批真实数据。在那之前，"每天 16 部"仍是排优先级的唯一尺子：
   一个功能能不能帮我们更快到每天 16 部。
2. **真实废片率仍未测出。** `pricing_rules.image_retry_factor = 250`
   （一张可用图平均要生成 2.5 次）是**拍值**。验证切片跑出的 3.33 无效
   ——它把构图差异计成了废片。需要人工逐张判可用性，10 张图在 `validation/`。
   **这个数字不可用于对外报价**；它每降 0.1，单片成本降 ¥2 左右
   （`19_UnitEconomics.md` §2.1）。`video_retry_factor = 150` 同样是拍值，
   而且视频是成本大头，误差的绝对值更大。

### 9.2 其他风险

- 视频 / TTS 没有 `_shape` 分支，接入时会静默走兜底价（`unknown_task_type`
  只 warning 不报错）。FR-BIL-004 就是堵这个。
- 预估用的模型与实际跑的模型可能不同（FR-BIL-005）。
- 没有消费明细页，用户看不懂钱花在哪，只能看到余额在掉。
- `DEFAULT_RULES` 兜底值与表里的值可能长期不一致，没有任何检查。

---

## 10. 模块依赖

- **依赖**：01（`org_id`）、08（任务生命周期驱动 reserve/settle/release）、
  05（实际使用的模型决定单价）。
- **被依赖**：所有花钱的动作——04 Agent、06 出图、12 媒体（视频 / TTS / 合成）、
  05 Gateway（BYOK 凭证与折扣判定）。

跨模块只调 `service` 层。

---

## 11. 迭代计划

1. **立刻**：`POST /credits/topup` 收口（FR-BIL-006）。它不依赖任何别的东西。
2. **Wave 2 前**：视频 / TTS 的计费形状（FR-BIL-004）+ 模型解析提前
   （FR-BIL-005）+ `task_cost_cap` 复核（FR-BIL-007）。
3. **Wave 2 后**：消费明细页、BYOK 折扣的界面可感知。
4. **有真实数据后**：回填 `image_retry_factor` / `video_retry_factor`，
   重估"一部"的成本，重算盈亏平衡。
5. **不排期**：支付、退款、账单、发票、毛利看板。

---

## 12. 验收标准和测试

**已可验收（现在就该绿）**

- 任一任务的预扣、实际消费、释放可完整对账，流水总和恒等于账户总额。
  （`tests/integration/test_billing_ledger.py::test_ledger_always_balances`）
- 并发消费不会出现负余额或丢更新。（`test_concurrent_reserves_cannot_overspend`）
- 重试使用独立预扣，且预扣与结算用同一个 attempt。
  （`test_retry_uses_separate_reservation`、
  `test_task_billing_lifecycle.py::test_reserve_and_settle_use_the_same_attempt`）
- 三道熔断都能拦住。（`test_task_cost_cap_blocks_runaway`、`test_project_budget_cap_blocks`）
- 代码里没有价格常量、没有浮点算钱。（`tests/unit/test_pricing_no_constants.py`）
- 体验额度只发一次；赠送与本金分账。
- 跨租户余额隔离。

**新增（P0 做完才算）**

- 普通登录用户 `POST /credits/topup` 返回 404，余额不动；
  管理员调用入账类型是 `ADMIN_GRANT` 且 `is_bonus=true`。
- `video.generate` 与 `audio.tts` 的估算走各自的 `_shape` 分支，
  日志里**不出现** `pricing.unknown_task_type`。
- 同一个镜头选不同模型，`POST /credits/estimate` 给出的数字不同，
  且与 `tasks` 上实际结算的模型一致（与
  [05_MODEL_GATEWAY.md](./05_MODEL_GATEWAY.md) §15 的同一条用例）。
- 一个 12 秒镜头切 3 段的完整生成，总花费不触发 `task_cost_cap`，
  或触发时错误信息告诉用户该怎么办。
- BYOK 配置后，同一次估算的数字明显低于平台档，且与实际用的 Key 一致
  （`test_byok_gateway_wiring.py::test_billing_discount_and_actual_key_agree`）。
