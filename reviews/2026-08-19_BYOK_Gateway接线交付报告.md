# BYOK 接进 Gateway —— 交付报告

**日期**：2026-08-19
**任务**：让配了自有 Key 的用户**真的用他自己的 Key**调用上游（ADR-027）
**状态**：完成。全量门禁绿，真机（真实 HTTP + 真实上游）验证通过。

---

## 1. 修的是什么洞

上一轮做完了 BYOK 的**计费**联动：配了自有 Key 的能力按隐性成本档位收费，
跳过 `model_pricing` 的 provider_cost 加价。但 Gateway 的 `_build()` 仍然是
进程启动时读一次环境变量、建一张平台 Key 的路由表——**实际调用用的还是
平台的 Key**。

于是每一次 BYOK 生成都是：用户按折扣价付款（本次实测 7 Credits），
平台按全价掏钱买上游（同一次调用平台档报价 103 Credits）。
差额没有任何告警，账面上只体现为毛利莫名其妙地掉。

---

## 2. 改了哪些文件

**新增**

| 文件 | 作用 |
|---|---|
| `apps/api/modules/gateway/catalog.py` | 能力→Provider→适配器→模型的**唯一一份**目录，`service.py` 与 `probe.py` 共用 |
| `tests/unit/test_gateway_byok_routing.py` | 路由/熔断/failover 决策，11 例 |
| `tests/unit/test_provider_call_signal.py` | 调用信号本身，3 例 |
| `tests/integration/test_byok_gateway_wiring.py` | 真 HTTP 存 Key → 真加密落库 → 真解密 → 传到适配器，5 例 |

**修改**

| 文件 | 改动 |
|---|---|
| `apps/api/modules/gateway/service.py` | Key 解析从"启动时读环境变量"改成"每次调用按 `org_id + capability` 现查"；熔断作用域、BYOK failover 边界、错误重写 |
| `apps/api/modules/gateway/breaker.py` | 加 `scope()`：熔断作用域 id = 平台档一个 + 每个自带 Key 的 org 各一个 |
| `apps/api/modules/gateway/probe.py` | `PROVIDER_OF` / `PROVIDER_LABELS` / 验证器构造改从 catalog 取，不再自己维护一份映射 |
| `apps/api/modules/billing/credentials.py` | 加 `resolve_for_call()`（service 层，解密逻辑仍只有这一处） |
| `adapters/providers/base.py` | `KeySource` 枚举 + `signal_key_source()` |
| `adapters/providers/deepseek.py` / `dashscope.py` | 接 `key_source`，每次调用发信号 |
| `apps/api/core/errors.py` | 登记 `provider.byok.rejected` |
| `apps/api/modules/agent/llm.py` / `runner.py` | `LLMRequest` 带 `org_id`，透传到 Gateway |
| `worker/jobs/generation.py` | 出图调用带上 `org_id` |
| `aigc_studio_docs/15_ArchitectureDecisions.md` | 新增 ADR-027 |
| `aigc_studio_docs/21_ErrorTaxonomy.md` | §2 处置矩阵加一行、§4 熔断作用域、§7 可观测字段加 `key_source` |

**没动**：平台默认路径的行为。没配自有 Key 的用户，路由表、优先级、
failover、熔断全部与之前逐字一致（`_build()` 只是改成遍历 catalog，
模型与优先级原样搬过来）。

---

## 3. 熔断作用域怎么设计的

**结论：按账号分，不按 Provider 分。**
作用域 id 是 `cb:provider:<provider_id>` （平台档）与
`cb:provider:<provider_id>@org:<org_id>` （自带 Key）。

合成一把两个方向都错：

- 某个用户的 Key 被上游封了、欠费了、把配额打满了，连着失败 5 次就会把
  这家 Provider 整个熔断掉——**所有用平台 Key 的其他用户跟着降级**。
  一个人的账号问题不该有这种放大倍数。
- 反过来，平台自己的账号出问题时把自带 Key 的用户也拦住同样没道理：
  他用的是另一个账号，那个账号好好的。

熔断计的是"这个账号在这家上游还能不能用"，账号的粒度就是 org。
`test_org_key_failures_do_not_trip_platform_breaker` 与
`test_one_org_breaker_does_not_affect_another` 把这两条钉住了。

Redis 键数量随自带 Key 的 org 数增长，键本身带 TTL（失败窗口 4 倍），
不会无限堆积。

---

## 4. failover 在 BYOK 下怎么算

**结论：同一把 Key 可以换模型，不跨 Provider，尤其不回落平台 Key。**

- **不回落平台 Key**：这是本轮要堵的洞在失败路径上的镜像。计费此刻已经
  按 BYOK 折扣算过了，平台 Key 顶上去就是平台掏钱、用户按折扣价付款。
  而且它会把"你的 Key 坏了"这个**只有用户自己能修**的问题永久藏起来——
  他会一直用着一把已经失效的 Key，直到某天平台发现毛利不对。
- **同 Provider 换模型仍然允许**：同账号同价，钱还是用户自己付，
  `wan2.2-t2i-flash` 抽风换 `plus` 是合理的。
- **账号级失败连模型都不换**：401 / 欠费在 fast 上是这个答案，在 plus 上
  还是同一个答案，换过去只是白打一次上游。这条只对 BYOK 生效——平台档的
  下一条候选往往是另一家 Provider、另一个账号，换过去是有意义的。

错误码用新登记的 `provider.byok.rejected`，与
`provider.account.insufficient` 的区别只有一个，但这个区别决定了**谁去修**：
后者是平台在上游欠费，用户只能等（文案"服务暂时不可用，请稍后重试"）；
前者只有用户能改，文案直接把他指到设置页。上游的具体分类保留在
`detail.upstream_code`，**沿用"测试连接"那套分类，没有另造一份**。

透出前过一遍 `probe.redact()`：上游 401 常把收到的 Key 原样贴回来，
而这条 message 会被写进 `agent_runs.error_detail` / `tasks.error_detail`。

---

## 5. "确实用了自有 Key"是怎么验证的

信号打在**适配器**里（`provider.call` 日志，字段 `key_source=platform|org`），
也就是真正把 Key 放进 Authorization 头的那一层。**只记来源枚举**——
没有 Key、没有尾号、没有密文。

### 真机验证（ENV=local，真实 HTTP，真实上游，花了真钱）

走 `localhost:8000` 注册 → 存 Key → 建项目 → 提交 `image.generate` 任务，
由 worker 真实执行：

| # | 场景 | 结果 |
|---|---|---|
| 1 | 存一把**格式合法但无效**的自有 Key | 任务 `failed`，`error_code=provider.byok.rejected`，`actual_cost=0`。日志：`gateway.key_source key_source=org` → `provider.call key_source=org` → DashScope 回 **401 InvalidApiKey**。 |
| 2 | 换成**真实有效**的自有 Key | 任务 `succeeded`，出图落库，`actual_cost=7`（BYOK 档）。日志 `provider.call key_source=org`。 |
| 3 | 移除 Key 后再跑一次 | 任务 `succeeded`，`estimated_cost` 从 7 跳回 **103**（平台档），日志 `provider.call key_source=platform`。 |

第 1 条是关键证据：**上游拒绝了这把 Key**。如果调用仍然走平台 Key，
这次生成会成功——它成功不了，说明发出去的确实是用户那把。
第 2、3 条证明成功路径与移除后的回落。

真机共消耗 2 次真实出图（wan2.2-t2i-flash，约 ¥0.2）。

**泄露检查**：用真实 Key 的完整值与 12 字符片段 grep 了全部容器日志，
以及 `tasks.error_detail` 里的 `sk-` 匹配，均为 0 命中。

### 测试环境

`ENV=test` 一律不打真实上游（沿用既有约定），所以单测/集成测试里适配器是
假的，但**查库 → 解密 → 传参**这一整段是真的（集成用例走真实 HTTP 存 Key、
真实 AES-GCM 落库解密），断言点落在假适配器实际收到的 Key 值与来源标记上。

---

## 6. 门禁实际输出

```
docker compose exec api pytest       →  526 passed, 2 skipped in 93.03s
docker compose exec api mypy apps worker packages agents adapters skills
                                     →  Success: no issues found in 99 source files
docker compose exec api ruff check . →  本轮改动的文件全部通过
docker compose exec api ruff format --check .
                                     →  本轮改动的文件全部通过
```

`ruff check` / `format --check` 全仓当前仍有 5 处报错，全部落在
`tests/integration/test_asset_folders.py` 与
`tests/integration/test_reference_character.py` —— 这两个文件属于同期并行的
另外两个任务（未跟踪状态），不在本轮范围内，没有代改。

---

## 7. 偏离与遗留

1. **多了一个 `catalog.py`**，任务书没要求。理由：不抽的话"能力→Provider"
   会存在三份（`service._build()`、`probe.PROVIDER_OF`、新的"用户这把 Key
   该实例化哪个适配器"），迟早对不上，表现是"设置页能配、真调用时说没有
   这家 Provider"。抽出来之后 `probe.py` 那两个常量变成 catalog 的别名，
   对 `credentials.py` 和既有测试零影响。
2. **新增了一个错误码 `provider.byok.rejected`**。验收要求"错误信息能让
   用户分辨是我的 Key 有问题"，而 `user_message` 只能来自错误目录，
   复用旧码就只能给出"服务暂时不可用，请稍后重试"，与要求相反。
   上游的分类没有另造，仍在 `detail.upstream_code` 里。
3. **每次调用一次 DB 查询 + 一次解密，不做缓存**。缓存等于"删了 Key 还在
   用"，而删 Key 的人多半正是因为它泄露了。相对一次几秒到几十秒的生成，
   这点开销可以忽略。
4. **`ENV=test` 下 Gateway 仍然没有"强制 Mock"的兜底**。今天没有测试会真的
   调到出图路径，所以不构成新风险；但 BYOK 之后 Key 可以来自数据库，
   "测试环境没配平台 Key 所以打不出去"这条隐性护栏比以前弱了。
   建议后续单独一轮把 `get_provider()` / `get_prober()` 那种"安全默认写在
   生产代码里"的做法也补到 Gateway 的出图路径上。
5. **并发编辑撞车**：`apps/api/modules/agent/runner.py` 在本轮期间被另一个
   并行任务重构过（我只加了一行 `org_id=org_id`）。当前文件同时含有两边的
   改动、全量测试绿；但这条与任务书"不共享任何文件"的前提不符，值得留意。
