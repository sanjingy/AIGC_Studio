# 06 一致性与图像生成

> 状态：**部分实现**（L0 提示词锁定 + 出图链路已实现；L1 参考图条件化、质量评分、候选版本未实现）
> 优先级：P0
> 负责人：待定
> 最近核对：2026-09-05（FR-CONS-011 无 Key 回退落地）

## 1. 模块目标与边界

让同一个项目里的角色、场景、画风在几十个镜头之间保持一致，并且这份一致性**可追溯、可复现、可度量**。
一致性做不好产品直接不可用——用户在第 3 个镜头看到主角变脸就会关掉页面（见 ADR-012）。

边界：

- 本模块拥有：风格档案、角色档案、场景档案、镜头条件化记录、质量分数。
- 图片文件与通用元数据归资产模块（07）；执行状态归 `tasks`（ADR-008）；费用归计费（09）；
  模型选择归 Gateway（05）。
- **提示词由本模块确定性合成，Agent 不许自己写风格词**——这是画风漂移的头号来源。

## 2. 用户与使用场景

- 跑完角色阶段后，为每个角色出一张基准立绘；不满意就重出，或者从资产库选一张、从本地传一张。
- 场景同理，出一张基准参考图。
- 分镜确认后，逐镜出首帧图；提示词自动带上该镜的角色、场景、项目风格。
- 出的图不对，换个模型再出一张，两张并排比，挑一张当"当前版"（**这一条还没实现**，见 §4）。

## 3. 当前真实能力

### 3.1 已实现

| 能力 | 证据 |
|---|---|
| 风格档案：base_model、正/负风格词、色调、线条、渲染方式、项目级基准 seed | `models.StyleProfile`；`service.ensure_style` |
| 角色档案：结构化外貌 JSON、基准立绘列、版本号 | `models.CharacterProfile` |
| 场景档案：结构化空间 JSON、基准参考图列、版本号 | `models.SceneProfile` |
| 角色 / 场景档案从 Agent 产出自动同步 | `service.sync_from_characters_output` / `sync_from_scenes_output`，由 `orchestrator` 在对应阶段调用 |
| 确定性提示词合成，结构固定为 **角色 → 场景 → 画面 → 风格** | `compose.compose_shot` |
| 场景一致性锚点真的进提示词：摄影主轴 `camera_axis` + 固定参照物 `fixed_references` | `compose.describe_scene`、`agents/schemas.py:SceneSheet` |
| Agent 写的风格词被剥掉 | `compose.strip_style_words`；`tests/unit/test_consistency_compose.py` |
| 项目级基准 seed + 镜号偏移，同项目重跑同一镜得同一张图 | `compose.compose_shot` 的 `seed=style.seed_base + shot_index` |
| 三类出图入口：角色 / 场景 / 镜头，各自建 `image.generate` 任务，走完整 Task + Billing + Gateway + Asset + SSE 链路 | `consistency/render.py`、`worker/jobs/generation.py`；`tests/integration/test_image_generation.py` |
| 镜头条件化落库：`resolved_prompt` 全文、negative、seed、引用的角色/场景 id、attempt | `models.ShotConditioning`、`service.record_conditioning` |
| 基准图三条来路：AI 生成（POST）、从资产库选、本地上传（后两条走 PUT，入参只有 `asset_id`） | `render.assign_character_portrait` / `assign_scene_reference`；`tests/integration/test_base_image_assign.py` |
| **PUT 这条路一分钱不花**：不建任务、不预扣、不结算 | `test_base_image_assign.py` 每个用例都在断言反面（无 Task、无 Ledger、余额不动） |
| 出图列表合并两个来源（`tasks` 里的生成记录 + 档案上钉的基准图），带 `source` 区分 | `render.list_renders` / `_assigned_base_images` |
| 上游 URL 立即转存自有对象存储（DashScope 链接只有 24 小时有效期） | `worker/jobs/generation.py` |
| 上游若改写提示词（`actual_prompt`）必须记下来 | 同上，写进 asset metadata |
| 缺角色档案时出图报可读错误而不是 500 | `errors.py` 的 `consistency.profile.missing` |
| **无 Key 时出图回退到 Mock**（FR-CONS-011）：`ENV=test` 一律 Mock、有 Key（平台或 BYOK）走真实 Gateway、都没有才 Mock | `gateway/mock_image.py`；`gateway/service._resolve`；`tests/unit/test_mock_image_provider.py` |
| Mock 走的是**同一条**路径（Gateway 返 URL → `_download` → `register_generated`），因此 worker 那段第一次有了自动化覆盖 | `tests/integration/test_image_generation_worker.py` |

出图接口（与 `apps/api/modules/consistency/router.py` 逐条核对，前缀 `/api/v1`）：

```text
GET  /api/v1/projects/{id}/images
POST /api/v1/projects/{id}/images/characters/{ref}     生成一张新候选（扣 Credits）
POST /api/v1/projects/{id}/images/scenes/{ref}         同上
POST /api/v1/projects/{id}/images/shots/{shot_index}   同上
PUT  /api/v1/projects/{id}/images/characters/{ref}     把已有资产钉成基准图（幂等，不花钱）
PUT  /api/v1/projects/{id}/images/scenes/{ref}         同上
```

> POST 与 PUT 用不同动词是刻意的：POST 是"再生成一张"（每次扣费、结果不同），
> PUT 是"基准图就是它"（幂等）。用同一个动词会让"点两下多扣一次钱"和"点两下没事"混在一起。

### 3.2 部分实现

- **一致性档次实际只有 L0。** `17_ConsistencyEngine.md` §2 把 L1 参考图条件化写成 MVP 主力，
  但 `adapters/providers/base.py:ImageRequest` 只有 `prompt` / `negative_prompt` / `size` / `n` / `seed`，
  **没有 `init_image` 也没有多参考图字段**。当前的一致性完全来自结构化外貌字段 + 系统注入风格词 + 固定 seed，
  即 L0。验证切片（§8.1）10 镜全部 ≥ 0.65 也是在这个前提下测出来的——这是好消息，
  但文档里"已做到 L1"的说法不成立。
- **基准图回写不对称。** `base_portrait_asset_id` / `base_reference_asset_id` 只有 PUT 这条路会写
  （`render.py:430` / `render.py:471`）。**AI 生成成功后不回写**，`reference_embedding` 同理始终为空。
  后果：AI 生成的那条路只体现在 `tasks` 上，"当前基准图是哪张"对它不成立。
- **相似度度量**：`metrics.embed_image_url` / `cosine` / `judge_similarity` 存在且能跑，
  但只有 `scripts/validation_slice.py`（会花真钱的验证脚本）在用。

### 3.3 预留（有列 / 有函数，没有调用方，不计入功能）

| 项 | 证据 |
|---|---|
| `shot_quality_scores` 落库 | `service.score_shot` 存在，**全仓库无调用方**（`worker/jobs/generation.py` 不调它） |
| `consistency_tier`（L0/L1/L2/L3） | 两张档案表都有这一列，默认 `"L1"`，**写死不改、也从没被读过** |
| `turnaround_asset_ids`（三视图） | 列存在，无任何代码写它 |
| `reference_embedding` | 列存在，只有 `score_shot` 会写，而 `score_shot` 没有调用方 |
| 风格冻结 | `service.lock_style` 存在，**无调用方**；`style_profiles.locked_at` 实际永远是 NULL |

### 3.4 未实现

- 候选版本与 `is_current`（ADR-033）。当前 `GET /images` 靠"取第一条就是当前版"的时间倒序约定，
  ADR-033 第 2 条已经把这个约定作废，但代码还没改。
- 自动重试与质量门槛。
- L2 角色 LoRA（依赖 GPU Runtime，M4）。
- L3 人脸修复后处理。
- 表情集、服装集。
- 视频时序一致性（见模块 12）。

## 4. 功能需求

### P0（挡住"逐镜 MP4"）

- **FR-CONS-001**：候选版本落地（ADR-033）。每次生成新增一个候选，恰好一个 `is_current`；
  切换当前版不删旧候选。`GET /projects/{id}/images` 每条带 `is_current`。
  **存储方案（`assets` 加变体组字段 vs 另建表）由 Lead 定**，07 与 12 必须对齐同一张表（ADR-033 第 5 条）。
- **FR-CONS-002**：AI 生成成功后回写基准图。出图完成回调里补一次
  `base_portrait_asset_id` / `base_reference_asset_id` 的写入，让两条路对称。
  没有这一条，"当前基准图"这个概念对 AI 生成的图不成立，而视频要拿它当首帧。
- **FR-CONS-003**：镜头首帧图必须能被下游明确引用——视频阶段消费的是"该镜当前版的首帧图"，
  并**记下消费的是哪一版**（ADR-033 第 3 条）。
- **FR-CONS-004**：生成前的模型选择按钮（ADR-031 第 4 条）：出图入口旁边能选本次用哪个图像模型，
  只对本次生效、不回写默认，且换模型必须重算 Credits 预估（ADR-024 硬约束 1）。
- **FR-CONS-005**：`resolved_prompt` 继续必存（已实现，列为需求是因为它不能被"优化"掉）。
- **FR-CONS-011**：图像能力的 Mock Provider。**已实现（2026-09-05）**，见
  `apps/api/modules/gateway/mock_image.py`。三条规则与 `llm.py` 一致：`ENV=test` 一律 Mock、
  有 Key 走真实 Gateway、没 Key 用 Mock；规则 1 压过 BYOK（测试环境里存了自有 Key 也不许打真上游），
  规则 3 让位于 BYOK（平台没 Key、用户自带 Key，该用他的）。
  占位图 1024×1024 PNG，同提示词稳定同图、不同提示词明显不同，写进对象存储后签一个预签名 URL 返回，
  所以 `_download` → `register_generated` 一步不少地被走了一遍——worker 那段因此第一次有了自动化覆盖
  （`tests/integration/test_image_generation_worker.py`）。
  **与原需求的两处偏差**：① 图上标的是提示词指纹（sha256 前 16 位）+ seed，不是"镜号 / 角色名"——
  Provider 层拿到的是 `ImageRequest`，里面只有 prompt / negative / size / n / seed，镜号与角色 ref
  留在任务 payload 里，为了让 Mock 能读到而往这个共享契约上加字段，是拿真实链路的接口去迁就假实现；
  且 Pillow 自带字体没有中文字形，中文标注只会画成方框。指纹同样能把图对回日志里的那一次调用。
  ② Credits 照常扣，见下方"计费"一段。
  依据：2026-09-04 Lead 手动验收，配 Key 前 10 镜出图全部 `provider.unavailable`，
  配 Key 后同一条路径 10/10 成功——说明缺的只是无 Key 时的回退，不是链路本身有问题。

  > **计费**：Mock 跑完照样按普通出图预扣与结算，`billing` 里没有任何 `if mock` 分支。
  > 预扣发生在建任务时（`pricing._shape` 按请求的模型算，此时 Gateway 还没解析），
  > 结算取 `task.reserved_cost`——真实路径上 failover 换了模型也是同一个行为。
  > 要让 Mock 免费就得在计费链路上开一条只有无 Key 环境才走的分支，那比"无 Key 环境里 Credits 照扣"
  > 危险得多。无 Key 环境本来就不产生真实上游费用，扣的只是平台内部的记账额度。

### P1

- **FR-CONS-006**：最小质量评分落地——把 `score_shot` 真正接进出图完成回调，
  至少写角色相似度一项。阈值走配置/数据库，不写常量。
- **FR-CONS-007**：受控自动重试。次数、成本、质量门槛三重上限，
  失败走 `quality.below_threshold` 错误码（目录里已有）。
- **FR-CONS-008**：按镜别分别建基准（三视图 / 表情集 / 景别变体），
  让比对发生在同类镜别之间——§8.1 第 2 条结论已经证明单一全局阈值是错的。
- **FR-CONS-009**：风格冻结真的生效：一旦有镜头产出就调 `lock_style`，
  之后改风格必须新建版本并告知"已生成的镜头需要重新生成"。
- **FR-CONS-010**：`consistency_tier` 要么按 §2 的降级链真正参与策略选择，要么删掉这一列。
  一个永远是 `"L1"` 又从没被读过的字段，只会让下一个人以为分层已经做了。

### P2 / 不做

- **L1 参考图条件化**：要给 `ImageRequest` 加 `init_image` / 多参考图，会同时动 Provider 抽象、
  Gateway、计费、BYOK 四处。决策记录 §3.2 把"图片编辑（图生图 + 多参考图）"判为 **M2 之后不取**。
  当前 L0 的实测数据（10 镜全部 ≥ 0.65，与"不同角色 0.30"拉开一个数量级）说明这个推迟是可承受的。
- **L2 角色 LoRA**：依赖自建 GPU，而决策记录 §1 明确"不做自建 GPU/ComfyUI"。M4 之后再谈。
- **L3 人脸修复后处理**。
- 表情集、服装集。

## 5. 核心流程

```text
Agent 角色 / 场景产出
  → sync_from_*_output（写档案）
  → 基准图：AI 生成（POST，扣费） 或 资产库选 / 本地传（PUT，不扣费）
  → 用户确认
  → 分镜确认后逐镜出图：
       compose_shot（角色 → 场景 → 画面 → 风格）
       → estimate + reserve
       → image.generate 任务
       → Gateway 出图 → 下载 → 转存对象存储
       → record_conditioning（resolved_prompt 落库）
       → settle
  → [P1] 质量评分 → 通过 / 重试 / 人工挑选
```

## 6. 数据模型与所有权

| 表 | 归属 |
|---|---|
| `style_profiles` | 本模块。项目级，版本化，`locked_at` 目前恒 NULL（见 §3.3） |
| `character_profiles` | 本模块。`ref` 是跨版本引用的稳定 id |
| `scene_profiles` | 本模块。同上 |
| `shot_conditioning` | 本模块。一镜一次生成一行，带 attempt |
| `shot_quality_scores` | 本模块。表在，无写入方 |

图片文件与元数据在 `assets`；任务在 `tasks`；费用在 `ledger_entries`。
pgvector 的用途是**角色 / 场景基准图的 embedding 与相似度检索**，不是 RAG。

## 7. 技术选择与工程设计

- Prompt Composer 是确定性服务，不让 Agent 临时重写项目风格词。风格词放在提示词**最后**：
  多数扩散模型对靠后的 token 权重更敏感，即便 Agent 的画面描述漏了风格词也压不过系统设定。
- 场景是**可选**参数。分镜没写 `scene_ref`、写了但档案没有、项目还没跑到场景阶段时，
  优雅降级成"角色 → 画面 → 风格"。做成硬要求会让存量项目突然出不了图。
- POST / PUT 语义与计费严格分开（见 §3.1）。
- 换基准图**不递增 `version`**（那是"角色设定改了"用的），**不删旧资产**（用户可能想换回去）。
- 上传复用资产模块现成的三段式直传，MIME 白名单、大小上限、容量配额都在那条链路上，
  本模块不另写一套阈值。
- 阈值、废片率、重试次数一律走 `pricing_rules` / 配置，不写代码常量（ADR-014）。

## 8. 模块依赖

- 04 Agent：角色 / 场景产出的来源。
- 05 Gateway：出图模型解析、failover、熔断、BYOK。
- 07 资产：图片文件与候选存储（ADR-033 第 5 条要求两边对齐同一张表）。
- 08 任务 / 09 计费：出图任务与预扣结算。
- 12 媒体：消费镜头当前版首帧图作为图生视频的输入。

## 9. 当前缺口与风险

| 缺口 | 影响 | 优先级 |
|---|---|---|
| 无候选版本 / `is_current` | ADR-033 的地基缺失；"生成三张挑一张"做不了；视频不知道该拿哪张当首帧 | P0 |
| AI 生成不回写基准图 | 两条路不对称，`reference_embedding` 恒空，相似度度量在生产链路上无数据 | P0 |
| 无生成前模型选择 | ADR-031 第 4 条未落地 | P0 |
| `score_shot` 无调用方 | 废片率永远测不出来，`image_retry_factor = 250` 一直是拍值，**不可用于对外报价** | P1 |
| `lock_style` 无调用方 | 风格可以在出图中途被改掉，已生成镜头静默不一致 | P1 |
| `consistency_tier` 恒为 `"L1"` 且从未被读 | 让人误以为分层策略已经实现 | P1 |
| 图像链路无 Mock 回退 | 没有 Provider Key 的环境（测试机、新同事本机、CI）出图必失败，`CLAUDE.md` 里"没有 Key 也能跑通全链路"这句对图像不成立 | P1 |
| 无 L1 参考图 | 换装 / 侧脸 / 远景 / 多人同框仍是最容易崩的场景（§8.1 数据） | P2（决策记录已定不做） |
| 视频时序一致性 | 廉价感的主要来源，M2 才会遇到 | 见模块 12 |

## 10. 迭代计划

1. 候选版本 + `is_current`（与 07、12 一起定表）。
2. AI 生成回写基准图 + `reference_embedding`。
3. 出图入口的模型选择按钮 + Credits 重估。
4. 接 `score_shot` 到出图回调，拿到第一批真实相似度数据。
5. 人工逐张判可用性，回填真实废片率到 `pricing_rules`（`validation/` 里有 10 张图）。
6. 风格冻结生效；`consistency_tier` 要么用起来要么删掉。
7. M2：把当前版首帧图交给视频阶段，并记录消费版本。

> **2026-09-04 实测记录（Lead 手动验收，香港测试机）**：配置 BYOK 的 DashScope Key 之后，
> 「注册 → 新建项目 → 推进 → 剧本门 → 角色出图 → 分镜门 → 批量出图 10 镜」全链路跑通，
> 10 张分镜首帧图 **10/10 成功、零失败**，每张实扣 7 Credits，落在批量弹窗给出的
> 60–105 估算区间内。worker 日志 `key_source=org` 确认走的是租户自己的 Key
> （ADR-024 的 `org_id` 传递修复有效）。这是出图链路第一次在非本机环境上端到端验证。

## 11. 验收标准与测试

- 任一镜头能追溯：角色版本、场景版本、风格版本、`resolved_prompt` 全文、seed、模型 id、任务 id、费用。
- 指派已有图片（PUT）绝不产生任何生成费用：无 `tasks` 行、无 Ledger 流水、余额不动。
- 重新生成不覆盖历史；用户可以切回旧候选（依赖 FR-CONS-001）。
- 每个角色 / 场景在任一时刻**恰好一个** `is_current`，切换是原子操作。
- 视频阶段取到的首帧图与界面上显示的"当前版"是同一张，且视频记录里写下了它的候选 id。
- 质量失败不会无限重试，也不会突破 `task_cost_cap`。
- 跨租户访问角色 / 场景 / 出图记录一律 404。
- Agent 产出里混入风格词时，`strip_style_words` 把它剥掉（已有单测，回归保留）。
