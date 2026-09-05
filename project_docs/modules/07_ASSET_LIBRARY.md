# 07 资产库

> 状态：**部分实现**（上传 / 下载 / 配额 / 分类 / 档案聚合已通；候选版本、引用关系、媒体元数据没有）
> 优先级：**P0**（逐镜 MP4 的每一张图、每一段视频、每一条配音都要落这里）
> 负责人：待定
> 最近核对：2026-09-05
>
> ⚠️ **2026-09-05：候选版本设计已被 ADR-035 改为"一张 `artifact_versions`"**，
> 不再是"各业务模块自建候选表"。过期判定也从"下游记录消费了哪一版"改为
> **内容摘要比对**（basis digest），不需要任何下游回写。本文档相关段落尚未重写。
> 权威顺序：[DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §5 > ADR-033 / 028 / 022 > 当前代码

---

## 1. 模块目标与边界

统一管理用户上传与系统生成的二进制产物：图片、视频、音频、文本，
以及将来的 LoRA / Workflow 文件。提供安全上传、下载、配额、分类、检索。

**拥有**：`assets` 的对象元数据、`asset_folders` 与分类关联、
存储配额、对象存储的 Key 生成规则。

**不拥有**：角色 / 镜头的业务状态（那在 `current_state_json` 与
`consistency` 的档案表里）、任务状态（模块 08）。
资产只知道"这是一张图、它由哪个任务产生"，不知道"它是第 7 镜的当前版"——
**后者正是决策记录 §5 要新加的东西**，落点见 §5。

---

## 2. 用户与使用场景

1. 传一份小说原文 / 一张参考图，用它起一个项目。
2. 在角色 / 场景的基准图位置**从资产库挑一张**或**从本地传一张**（S14 已有）。
3. 在"我的素材"里按类型、文件夹、项目筛，看容量还剩多少。
4. **同一镜生成三张图，挑一张当当前版，另外两张留着**（决策记录 §5，未实现）。
5. 下载单镜 MP4，或批量下载全部镜头（模块 12 的动作，落点是这里的对象）。

---

## 3. 当前真实能力

状态词按 [DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §0。

| 能力 | 状态 | 代码 / 测试证据 |
|---|---|---|
| 三段式直传（建 pending 记录 → 预签名 PUT → complete 校验） | 已实现 | `asset/router.py` 的 `upload-url` / `{id}/complete`；`tests/integration/test_asset_upload.py` |
| MIME 白名单（16 个类型）+ 文件名清洗 | 已实现 | `asset/mime.py::ALLOWED_MIME` |
| complete 时 HEAD 对象、核对大小、声明值与实测值不一致则拒 | 已实现 | `asset/service.py` |
| 每用户存储配额（默认 1 GiB） | 已实现 | `asset/service.py::ensure_quota`；`pricing_rules.user_storage_quota_bytes = 1_073_741_824`（迁移 `a1f3c07b52d4`） |
| 分页列表、详情、软删除、用量 | 已实现 | `GET /assets`、`/assets/usage`、`DELETE /assets/{id}` |
| 短期预签名下载 URL | 已实现 | `GET /assets/{id}/download-url`；`s3_presign_ttl_seconds = 900` |
| 资产库聚合视图（二进制资产 + 角色/场景档案 + 独立角色档案 + 文件夹 + 用量） | 已实现 | `GET /assets/library`；`asset/library.py` |
| 文件夹：建 / 改名 / 删 / 放入移出（ADR-028） | 已实现 | `/assets/folders*`、`PUT /assets/folder-items`；`tests/integration/test_asset_folders.py` |
| 独立角色档案（手工建 / 一段描述调 LLM 生成，ADR-028） | 已实现 | `POST /assets/characters`；`tests/integration/test_reference_character.py` |
| 平台生成的图落资产表并带来源元数据（`model_id` / `prompt` / `actual_prompt`） | 已实现 | `asset/service.py::register_generated`；`worker/jobs/generation.py` |
| 生成图可被钉成角色 / 场景基准图（S14） | 已实现 | `PUT /projects/{id}/images/characters/{ref}`、`.../scenes/{ref}`；`tests/integration/test_base_image_assign.py` |
| 超时未完成上传的清理 | 已实现 | `worker/jobs/maintenance.py::purge_abandoned_uploads`，每小时一次 cron |
| 跨租户隔离（列表、详情、下载全带 `org_id`） | 已实现 | `tests/integration/test_tenant_isolation.py` |
| **候选版本 / 当前版**（ADR-033） | 未实现 | 全仓无 `is_current`；`GET /projects/{id}/images` 仍是"取第一条就是当前版" |
| 资产引用关系（谁在用这张图） | 未实现 | 无 `asset_references` 表；删除时没有引用保护 |
| 派生 / 版本关系（这张图是从哪张改出来的） | 未实现 | — |
| 媒体元数据（时长、编码、缩略图、波形） | 预留 | `assets.duration_ms` / `width` / `height` 三列存在，**没有任何代码写过**（`register_generated` 也不写） |
| `assets.moderation_status` / `watermarked` | 预留 | 有列，从未被写过；界面**不要显示**（见 [13_ADMIN_COMPLIANCE.md](./13_ADMIN_COMPLIANCE.md)） |
| 标签、全文检索 | 未实现 | — |
| 前端上传入口 | 未实现 | `components/freeflow/asset-library-grid.tsx:209` 的上传按钮是 `disabled`，`lib/api.ts` 里没有上传接口 |
| `checksum` 去重提示 | 部分实现 | 有列有索引 `ix_assets_org_checksum`，但没有"这张图你已经传过"的产品行为 |

---

## 4. 功能需求

### 4.1 P0（挡住"逐镜 MP4"）

- **FR-ASSET-001**：大文件浏览器直传对象存储，服务端只发预签名 URL。（已实现）
- **FR-ASSET-002**：complete 前保持 `pending`，服务端确认对象存在且大小相符才 `ready`。（已实现）
- **FR-ASSET-003**：所有资产按 `org_id` 隔离，可选关联 `project_id`。（已实现）
- **FR-ASSET-004**：**候选版本与当前版**（决策记录 §5、ADR-033）。
  资产侧要回答两个问题：这一批候选是同一个"位置"（第 7 镜的首帧图）的吗，
  以及其中哪一个是当前版。方案见 §5，**由 Lead 定**。
- **FR-ASSET-005**：**视频与音频资产的落库**。视频文件比图大一到两个量级，
  `register_generated` 现在的做法是把整个字节读进内存再写库
  （`data: bytes`），视频必须改成流式转存，否则一条 12 秒的视频
  就能把 Worker 的内存打满。
- **FR-ASSET-006**：**音频资产必须带 `duration_ms`**。音频优先时间线
  （ADR-011）靠它回写镜时长；这一列已经存在，只是没人写。
- **FR-ASSET-007**：**前端上传接线**。资产库的上传按钮当前是禁用的，
  三段式链路后端全有，缺 `lib/api.ts` 的封装与页面接线
  （模块 11 的迁移清单第 4 条）。
- **FR-ASSET-008**：生成资产记录来源任务、模型、Prompt、上游改写后的
  实际 Prompt。（已实现，视频 / 音频接入时照做）

### 4.2 P1

- **FR-ASSET-020**：引用关系（`asset_references`）——查"这张图用在哪"，
  以及删除保护。候选版本落地后这条会变得更必要：
  下游要记录"消费的是哪一版"（ADR-033 第 3 条），那份记录本身就是一种引用。
- **FR-ASSET-021**：缩略图与波形（视频抽帧、音频包络），列表页才不会
  下载整个视频来画一个格子。
- **FR-ASSET-022**：`checksum` 去重提示（同一租户内），
  **不得跨租户提示**——那会泄露别人传过什么。
- **FR-ASSET-023**：标签与检索。
- **FR-ASSET-024**：对象生命周期与容量告警（配额 1 GiB 是硬上限，
  用户撞上之前应该先看到提示）。

### 4.3 P2

- **FR-ASSET-030**：LoRA / Workflow 类型资产（`ASSET_TYPES` 里已有枚举值，
  属"预留"）。
- **FR-ASSET-031**：跨项目资产复用的血缘图。
- **FR-ASSET-032**：候选的自动清理策略（ADR-033 代价一节明说不自动清理，
  定期清理是运维事项，见 [14_PLATFORM_OPERATIONS.md](./14_PLATFORM_OPERATIONS.md)）。

---

## 5. 候选版本对资产库的影响（决策记录 §5 / ADR-033）

> **这一节列方案，不做决定。** ADR-033 第 5 条把选型留给 Lead，
> 并要求**资产侧（本文档）与媒体侧（[12_MEDIA_TIMELINE_EXPORT.md](./12_MEDIA_TIMELINE_EXPORT.md)）
> 对齐同一张表**。两边都写"待 Lead 对齐"，不要各自实现。

### 5.1 要满足什么

1. 一个"位置"（第 7 镜的首帧图 / 第 7 镜第 2 段的视频 / 第 7 镜的配音）
   对应**一组候选 + 恰好一个当前版**。
2. 每次生成**新增**一个候选，切换当前版**不删**旧候选。
3. `GET /projects/{id}/images` 的"取第一条就是当前版"约定作废，
   改为每条带 `is_current`；视频、配音的列表接口同一形状。
4. 下游记录**消费时用的是哪一个候选 id**（ADR-033 第 3 条），
   用来算"过期"。
5. S14 的既有行为要能平滑接上：钉基准图不删旧资产、不递增 `version`
   （`consistency/render.py:415`、`:459`）——那已经是这条规则的雏形。

### 5.2 三个方案

| 方案 | 做法 | 好处 | 代价 |
|---|---|---|---|
| **A. `assets` 加两列** | `variant_group_id UUID`（同一位置的候选共享）+ `is_current BOOL`；配一个部分唯一索引 `WHERE is_current AND deleted_at IS NULL` 保证"恰好一个" | 改动最小；列表接口天然带上；不引入新表 | `assets` 是**跨项目的通用文件表**，把"第 7 镜的当前版"这种业务语义塞进去会让它变成业务表；组的"位置"是什么（镜号？段号？）没地方放，只能塞 `metadata_json` |
| **B. 新建 `asset_variant_groups` + `asset_variants`** | 组表记位置（project_id、subject_kind、subject_ref、shot_index、segment_index、capability）；成员表记 asset_id + is_current + 生成参数 | 语义干净；位置是显式列可以查；候选的生成参数（模型、seed、prompt）有地方放，正好满足 ADR-031"用了哪个模型"的展示需求 | 两张新表 + 迁移；所有出图 / 出视频 / 出音频的写路径都要改；`GET /projects/{id}/images` 要从"合并 tasks 与档案"改成"读组表" |
| **C. 位置表下挂 asset_id 列表** | 每类位置各建一张业务表（`shot_images` / `shot_segment_videos` / `shot_audios`），各自有 `current_asset_id` | 每张表的字段最贴合自己的位置语义 | 三张表三套几乎一样的逻辑；决策记录 §3.4 刚决定"不实体化"，这与那条精神相反 |

> **Lead 裁决（2026-09-02，决策记录 §11.1 第 1 条 / ADR-033 第 5 条）**：走 B 的方向，但**按业务各建候选表**（首帧图候选、配音候选、段视频候选），不建通用的 `asset_variants`；`assets` 只存文件与血缘，不加 `variant_group_id` / `is_current`。模块 12 的数据模型草案按此对齐。

**倾向（供 Lead 参考，不是决定）**：B。理由是 ADR-031 要求界面显示
"这一版是哪个模型出的"，而 A 只能把它塞进 `metadata_json`
（已经塞了 `model_id` / `prompt`），查询和展示都要靠 JSON 解包；
C 与"不实体化"的方向冲突。B 的代价集中在一次迁移和几处写路径，
且这几处写路径在 Wave 2 本来就要为视频 / 音频改一遍。

### 5.3 无论选哪个，这三条都成立

- `GET /projects/{id}/images` 现有的 `source`（`generated` / `assigned`）
  字段**保留**：钉的那条没有 `task_id`，重试按钮要靠它判断。
- 候选**不自动清理**，对象存储会涨。清理策略是运维事项，不是本模块的默认行为。
- 切换当前版**不递增** `consistency` 档案的 `version`——
  那一列的含义是"角色设定本身改了"，两件事不能混。

---

## 6. 上传与生成流程

### 6.1 用户上传（已实现）

```text
POST /assets/upload-url
  → 校验 MIME 白名单、清洗文件名、检查配额
  → 建 pending 记录 + 签一个 PUT URL（服务端生成 storage_key）
  → 浏览器直传对象存储
POST /assets/{id}/complete
  → HEAD 对象，核对大小与声明值
  → mark ready
```

失败或超时的 pending 记录由 `purge_abandoned_uploads` 每小时回收。

### 6.2 平台生成（已实现，视频 / 音频要改）

```text
Worker 拿到上游返回的临时 URL
  → 下载（DashScope 图片链接只有 24 小时有效期，必须转存）
  → asset_service.register_generated(data=bytes, metadata={model_id, prompt, actual_prompt})
  → 落 assets 表
```

**视频要改成流式**：现在的签名是 `data: bytes`，整份读进内存。
图片几百 KB 无所谓，视频不行（FR-ASSET-005）。

---

## 7. 数据模型与所有权

### 7.1 已有

| 表 | 关键列 |
|---|---|
| `assets` | `project_id`(可空)、`owner_user_id`、`type`、`status`、`filename`、`storage_key`(唯一)、`mime_type`、`declared_size_bytes` / `size_bytes`、`checksum`、`width` / `height` / `duration_ms`（**未填**）、`metadata_json`、`moderation_status` / `watermarked`（**预留**）、`retention_until` |
| `asset_folders` | 用户自建文件夹，**属于用户不属于项目**（资产库本身跨项目，配额按账号算） |
| folder items | `FOLDER_ITEM_TYPES = (asset, profile, character)`，三类 id 指向三张表，**故意不做外键**（跨模块加外键会把迁移绑死） |

索引四条：`(org, project, created)`、`(org, owner, created)`（配额与"我的资产库"）、
`(org, checksum)`、`(status, created)`（清理任务扫 pending）。

### 7.2 需要新增

- 候选版本的载体（§5，待 Lead 定）。
- `asset_references`（P1）。
- 媒体探测结果（P1，可以直接写进已有的 `width` / `height` / `duration_ms`）。

---

## 8. API 与前端入口

```text
POST   /api/v1/assets/upload-url
POST   /api/v1/assets/{id}/complete
GET    /api/v1/assets                      分页列表
GET    /api/v1/assets/usage                容量用量
GET    /api/v1/assets/library              聚合视图（资产 + 档案 + 文件夹 + 用量）
GET    /api/v1/assets/{id}
GET    /api/v1/assets/{id}/download-url
DELETE /api/v1/assets/{id}
GET    /api/v1/assets/folders              + POST / PATCH / DELETE
PUT    /api/v1/assets/folder-items
POST   /api/v1/assets/characters           独立角色档案（可用 LLM 生成）
GET    /api/v1/assets/characters[/{id}]    + DELETE
```

前端入口（ADR-030 之后）：
`/freeflow/assets`（我的素材）、`/freeflow/projects/{id}/assets`（项目内素材）。
两处共用 `components/freeflow/asset-library-grid.tsx`。
**上传按钮当前禁用**，接线是 P0（FR-ASSET-007）。

Asset Picker（`components/project/asset-picker.tsx`）只显示与当前动作
兼容的 `ready` 资产，被 `render-slot.tsx` 复用——freeflow 依赖这两个文件，
删旧壳时**不能删**（见 [11_WEB_WORKBENCH.md](./11_WEB_WORKBENCH.md) §4）。

---

## 9. 技术、安全与可靠性

- **Storage Key 由服务端生成**并含租户前缀，客户端不能指定任意路径。
  这是防止越权覆盖别人对象的唯一防线。
- **MIME 白名单不是黑名单**：黑名单永远漏，且漏的那个通常就是能执行的那个。
- **配额检查在每次上传前跑**，所以 `(org, owner, created)` 索引不能少——
  缺它时配额检查随资产表增长变成全表扫描。
- **配额是 BYOK 滥用的第二道防线**（ADR-025 第 2 条）：
  即使 Credits 侧几乎不花钱，存储侧的物理上限仍然卡着。
- 下载一律短期预签名（900 秒），Bucket 不公开。
- 删除是软删除；对象的物理清理与数据库记录分离，按保留期由后台执行。
- 开发用 MinIO，生产只依赖 S3 兼容接口（ADR-022：本地目录是镜像，
  对象存储才是唯一真相）。

---

## 10. 模块依赖

- **依赖**：01（`org_id` / `owner_user_id`）、02（`project_id`）、
  09（配额阈值走 `pricing_rules`）、08（生成资产由任务产生）。
- **被依赖**：06 一致性与出图、11 Web、**12 媒体**（视频 / 音频 / MP4 全部落这里）。
  §5 的候选版本方案必须与模块 12 对齐。

---

## 11. 当前缺口与风险

1. **候选版本没有落点**（§5）。在它定下来之前，Wave 2 的出图 / 出视频
   写路径不该动——改两遍的代价比等一次决定大。
2. **前端不能上传**。用户只能用系统生成的资产，"从本地传一张参考图"
   这条路在资产库页面上是断的（基准图那两个入口能传，是走 S14 的专用链路）。
3. **视频转存会撑爆内存**（FR-ASSET-005）。
4. **媒体元数据三列从未被写**，列表页无法显示时长，模块 12 也拿不到
   "这段视频实际多长"。
5. **没有引用保护**：被镜头引用的资产可以被无提示删除，下游只会在
   合成时报"资产不存在"。
6. `moderation_status` 恒为 `pending`（从未被写），**界面不要显示它**。

---

## 12. 迭代计划

1. **Wave 1**：前端上传接线（FR-ASSET-007）。
2. **Wave 2 前**：Lead 定候选版本方案（§5），与模块 12 对齐后一次性迁移。
3. **Wave 2**：视频 / 音频落库（流式转存 + `duration_ms` 回填）。
4. **P1**：引用关系与删除保护、缩略图与波形、去重提示。
5. **P2**：标签检索、生命周期与容量告警、LoRA / Workflow 类型。

---

## 13. 验收标准和测试

**已可验收**

- 上传无法越权覆盖其他租户对象；`storage_key` 由服务端生成。
- `pending` → `ready` 需服务端 HEAD 确认，大小不符被拒。
  （`tests/integration/test_asset_upload.py`）
- 超配额上传被拒，错误里带 used / requested / quota 三个数。
- 文件夹的增删改与放入移出有集成测试。（`test_asset_folders.py`）
- 独立角色档案可手工建、可由一段描述生成。（`test_reference_character.py`）
- 钉基准图不建任务、不预扣、不结算、余额不动。（`test_base_image_assign.py`）
- 跨租户资产一律 404。

**新增（P0 做完才算）**

- 同一镜生成三次得到三个候选，其中**恰好一个** `is_current`；
  切换当前版后旧候选仍在，且 `version` 没有递增。
- 一条 12 秒视频落库时 Worker 内存不随视频大小线性增长（流式转存）。
- 音频资产的 `duration_ms` 非空，且等于模块 12 拿到的那个值。
- 资产库页面能真正上传一张图，并在同一页看到它。
