# 02 项目与工作区

> 状态：**部分实现**（后端 CRUD 与生产快照已通；工作台两套壳并存，freeflow 未独立闭环）
> 优先级：**P0**（项目是所有生产的聚合根，且 ADR-030 的替换以它为落点）
> 负责人：待定
> 最近核对：2026-09-02
> 权威顺序：[DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §2 / §3 > ADR-030 / 029 / 008 > 当前代码

---

## 1. 模块目标与边界

项目是一次内容生产的聚合根：标题、路线、状态、预算、模型偏好、Skill 选择，
以及**编排状态快照** `current_state_json`。

**拥有**：`projects` 表的全部列，`current_state_json` 这份生产快照
（ADR-008 里"执行状态只认 `tasks.status`"约束的是**任务**状态；
阶段与产物快照仍在这里）。

**不拥有**：任务执行状态（模块 08）、资产文件（模块 07）、
Agent 步骤记录（模块 04 的 `agent_runs`）、Credits（模块 09）。

决策记录 §3.4 定死一条：**角色 / 场景 / 分镜正文不实体化**，
继续存 `current_state_json`，字段级修改走 ADR-029 的 JSON Patch。
ReelBench 式"每个角色一行"放到 M2 之后。

---

## 2. 用户与使用场景

1. 从项目大厅新建一个项目，输入小说原文**或一句话创意**（决策记录 §3.1，
   创意入口是 Wave 1 的新增项）。
2. 回到大厅继续最近的项目，一眼看到"卡在哪、下一步做什么"。
3. 在项目设置里改标题、预算、模型偏好。
4. 删除不要的项目。

用户不需要知道 `route_type` 和 `status` 这两个枚举的存在——
它们是内部词汇，界面上只说"阶段"。

---

## 3. 当前真实能力

状态词按 [DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §0。

| 能力 | 状态 | 代码 / 测试证据 |
|---|---|---|
| 创建 / 分页列表 / 详情 / PATCH / 软删除 | 已实现 | `project/router.py` 六条端点；`project/service.py`；`repo.soft_delete` |
| `DELETE /projects/{id}` 前端已接 | 已实现 | `apps/web/lib/api.ts`（CLAUDE.md 记的"界面显示未接入"已在本分支修好） |
| 跨租户 404 | 已实现 | `tests/integration/test_tenant_isolation.py` |
| `current_state_json` 存编排快照（阶段 + 各角色产出） | 已实现 | `project/models.py`；`agent/orchestrator.py` 读写 |
| `stale_roles` 过期记账透出 | 已实现 | `project/models.py::Project.stale_roles`；`tests/integration/test_stale_roles.py` |
| `model_preference` 按能力覆盖模型 | 已实现 | `PATCH /projects/{id}/model-preference`；`tests/integration/test_model_preference.py` |
| `budget_cap_credits` / `spent_credits` 参与熔断 | 已实现 | `billing/service.py::_check_caps`；`consistency/render.py::_create` 传 `project_budget_cap` |
| 字段级修改 / 修订历史 / 批次撤销（ADR-029） | 部分实现 | 后端 `apps/api/modules/content/`（三条端点，已挂 `main.py:150`）**尚未提交、前端未接** |
| freeflow 项目大厅（真实项目 / 运行 / 审核数据） | 已实现 | `components/freeflow/project-lobby.tsx`、`home-project-grid.tsx` |
| freeflow 项目概览（汇总故事 / 角色 / 场景 / 分镜 / 图 / 任务） | 已实现 | `components/freeflow/project/project-overview.tsx` |
| 项目设置页（标题、预算、模型偏好） | 已实现 | `components/freeflow/project/project-settings.tsx` |
| `selected_skill_id` | 预留 | 有列、有 `PATCH` 不到的写入路径；**编排器不读它**（`orchestrator._SPEC_OF` 钉死 Agent id），ADR-026 明写运行时未接线 |
| `route_type` 七个枚举 | 部分实现 | 只有 `NOVEL_TO_ANIME` 走得通；其余六个是 `01_ProductSpec.md` 的路线清单，没有对应 Skill |
| 项目状态 `PROJECT_STATUSES` 六值 | 部分实现 | 列存在且有默认值 `draft`，但**没有任何代码按业务推进它**——阶段真相在 `current_state_json.stage` |
| 项目封面 / 最近资产缩略图 | 未实现 | 无 `cover_asset_id` 列 |
| 创意（一句话）入口 | 未实现 | `plot_index` 的输入只接小说原文 |
| 分集大纲 | 未实现 | 决策记录 §3.2 取，Wave 1 |
| 项目版本 / 快照 / 归档 | 未实现 | `archived` 只是枚举值 |

---

## 4. 功能需求

### 4.1 P0（挡住"逐镜 MP4"）

- **FR-PRJ-001**：项目 CRUD + 软删除 + 跨租户 404。（已实现）
- **FR-PRJ-002**：`current_state_json` 是编排快照的唯一位置，
  字段级修改走 ADR-029 的 Patch 端点，**不再由前端整份覆盖**。
  后端已具备，缺前端接线（模块 11 的迁移清单第 2 条）。
- **FR-PRJ-003**：**创意项目入口**——`plot_index` 的输入从"小说原文"
  放宽到"一句话 / 一段创意 + 可选参考"（决策记录 §3.1、§3.2 取）。
  项目创建时要能带上这份输入。
- **FR-PRJ-004**：**分集大纲**进 `current_state_json`，
  它是剧本按集导航的前提（决策记录 §3.2 取）。
- **FR-PRJ-005**：项目当前的**视频模型**要能被分镜阶段读到
  （ADR-032 第 4 条，解析规则在
  [05_MODEL_GATEWAY.md](./05_MODEL_GATEWAY.md) §6）。
  项目这一侧要提供的是 `model_preference[video_generation]`，已有。
- **FR-PRJ-006**：项目概览给出**唯一下一步**，且这个下一步的落点必须
  在 freeflow 内（ADR-030 的前置条件）。当前 `project-overview.tsx`
  在"待确认"时跳回 `/projects/{id}`。

### 4.2 P1

- **FR-PRJ-020**：项目封面（`cover_asset_id`，优先取最新已确认的分镜图或角色图）。
- **FR-PRJ-021**：项目状态机收敛——要么让 `projects.status` 真的被推进，
  要么删掉这一列只留 `current_state_json.stage`。**两份真相并存是当前的问题**，
  见 §9.2。
- **FR-PRJ-022**：`current_state_json` 的 schema 版本号，防止无边界膨胀。
- **FR-PRJ-023**：项目搜索、排序、归档。
- **FR-PRJ-024**：Project Overview 的聚合 DTO（一次调用拿全），
  现在前端并发拼装多个弱类型接口。

### 4.3 P2 / 冻结

| 项 | 状态 | 依据 |
|---|---|---|
| 项目内选择 Skill 并真正生效 | **冻结** | 决策记录 §9："Skill 运行时 / Canvas / `_NEXT` 硬编码整体冻结" |
| 角色 / 场景 / 道具实体化（每个一行） | **不做（M2 内）** | 决策记录 §3.4 |
| `route_type` 的另外六条路线 | P2 | 没有对应 Skill，做了就是假下拉框 |
| 项目版本 / 快照 / 回滚 | P2 | ADR-029 的批次撤销已经覆盖了最常见的"改错了要退回" |

---

## 5. 核心流程与状态机

### 5.1 生产阶段（真相在 `current_state_json.stage`）

```text
routing → plot_index → screenplay → 【await_setup 门】
  → characters → scenes → storyboard → 【await_storyboard 门】→ done
```

这张图硬编码在 `agent/orchestrator.py::_NEXT`，**按决策记录 §9 冻结**，
本轮不迁到 Skill 编译器。前端只读展示，不能重排。

Wave 2 之后这张图要在 `storyboard` 之后接上配音 → 视频 → MP4
（模块 12），届时**仍然是改 `_NEXT`，不是启用 Skill 运行时**。

### 5.2 `projects.status` 与阶段的关系

`PROJECT_STATUSES = (draft, routing, producing, review, completed, archived)`
这一列**当前没有任何业务代码推进它**（除建项目时的默认 `draft`）。
界面上显示的"阶段"来自 `current_state_json.stage`。
这是一处真实的双真相，处理方案见 §9.2。

---

## 6. 数据模型与所有权

### 6.1 已有（`projects`）

| 列 | 说明 |
|---|---|
| `owner_user_id`、`title` | — |
| `route_type` | 七个枚举，只有 `NOVEL_TO_ANIME` 走得通 |
| `status` | 六个枚举，未被推进（§5.2） |
| `current_state_json` | JSONB，编排快照：阶段 + 各角色产出 + `stale_roles` |
| `selected_skill_id` | 指向 `org_skills.id`，**不加外键**（跨模块引用完整性由 service 保证）；运行时未接线 |
| `model_preference` | JSONB，capability → model_id（ADR-024） |
| `budget_cap_credits` / `spent_credits` | BIGINT 最小单位 |
| 索引 `ix_projects_org_created` | 列表页主查询：租户 + 时间倒序 |

`BaseEntity` 已提供 `id` / `created_at` / `updated_at` / `deleted_at`，
所以"用 `updated_at` 排最近项目"不需要加列。

### 6.2 需要新增

- `cover_asset_id`（P1）。
- `current_state_json` 里的 `schema_version`（P1，不加列）。
- **不新增**角色 / 场景 / 分镜实体表（决策记录 §3.4）。

---

## 7. API、事件与前端入口

```text
POST   /api/v1/projects
GET    /api/v1/projects
GET    /api/v1/projects/{id}
PATCH  /api/v1/projects/{id}
PATCH  /api/v1/projects/{id}/model-preference     # 按 key 合并，不整份覆盖
DELETE /api/v1/projects/{id}                      # 软删除
```

ADR-029 的内容写路径（已实现，未提交）：

```text
PATCH /api/v1/projects/{id}/outputs/{role}        # JSON Patch
GET   /api/v1/projects/{id}/revisions             # 修订历史
POST  /api/v1/projects/{id}/revisions/{batch}/undo
```

事件走项目级 SSE（模块 08）：`task.*`、`approval.*`、`asset.created`。

前端入口（ADR-030 之后唯一）：
`/freeflow`（大厅）、`/freeflow/projects`、`/freeflow/projects/{id}/*`（七个子页）。

---

## 8. 技术选择和工程设计

- **`model_preference` 按 key 合并不整份覆盖**：前端只传正在改的那个能力，
  整份覆盖会让两个标签页互相冲掉。
- **JSONB 必须整份换新 dict 才会被标记为脏**——原地
  `row.model_preference[k] = v` 改的是同一个对象，flush 时比较不出差异，
  改动被悄悄丢掉。
- **UPDATE 之后必须 `db.refresh(row)`**：`updated_at` 是 server 端
  `onupdate=now()`，读它会触发同步 refresh，在 async 上下文里抛
  `MissingGreenlet`，把一次成功的更新变成 500。
- **项目上下文来自 URL**，前端不维护一个会与路由不同步的全局 project state。
- **跨模块只调对方 service**，聚合概览不跨模块直读 ORM。

---

## 9. 当前缺口与风险

### 9.1 freeflow 未独立闭环（ADR-030 的唯一前置条件）

审批仍跳回 `/projects/{id}`（`project-overview.tsx:482` 与 `:761`），
故事 / 分镜编辑只存组件本地。完整清单在
[11_WEB_WORKBENCH.md](./11_WEB_WORKBENCH.md) §5。

### 9.2 `projects.status` 与 `current_state_json.stage` 是两份真相

前者六个枚举没人推进，后者是真的。今天不出问题是因为没人读前者，
但它出现在 `ProjectOut` 里、也出现在旧大厅的 `STAGE_LABEL` 映射里，
迟早有人按它做判断。**建议 P1 二选一**（删列，或让 service 在推进阶段时
同步写它），不要长期并存——这与"执行状态只认一处"是同一条纪律。

### 9.3 其他

- `route_type` 七选一里六个没有实现路径，界面上给全七个就是假选项。
- `selected_skill_id` 选了不生效，界面必须继续标注"运行时未接线"（ADR-026）。
- ADR-029 的 content 模块**未提交**，卡在"Docker 未运行、跑不了迁移和测试"。
  合并前必须跑一次迁移 + `tests/integration/test_content_patch.py`
  与 `test_content_revisions.py`。
- 项目概览是前端并发拼几个接口拼出来的，任何一个接口变形都会让概览显示错。

---

## 10. 模块依赖

- **依赖**：01（`org_id` / `owner_user_id`）、09（预算熔断）、
  05（`model_preference` 的合法值来自模型目录）。
- **被依赖**：03 内容、04 Agent、06 一致性、07 资产、08 任务、
  11 Web、12 媒体——它们全部以 `project_id` 为聚合键。

---

## 11. 迭代计划

1. **Wave 0**：content 模块（ADR-029）跑迁移与测试，作为独立 commit 收进来。
2. **Wave 1**：前端接 Patch / 修订历史 / 撤销；审批门迁入 freeflow；
   创意入口与分集大纲。
3. **Wave 2**：分镜阶段消费视频模型单段上限（FR-PRJ-005 的下游）。
4. **P1**：状态双真相收敛、项目封面、概览聚合 DTO。
5. **冻结**：Skill 运行时与项目内选 Skill、角色实体化。

---

## 12. 验收标准和测试

**已可验收**

- 创建、列表、详情、PATCH、软删除、跨租户 404 有集成测试。
- `model_preference` 按 key 合并、`null` 只清那一条、非法 model_id 被拒。
  （`tests/integration/test_model_preference.py`）
- 项目预算上限能拦住超支任务。（`tests/integration/test_billing_ledger.py::test_project_budget_cap_blocks`）
- 过期角色记账能透出到 `stale_roles`。（`tests/integration/test_stale_roles.py`）

**新增**

- 软删除后的项目不出现在列表、按 id 取返回 404、其下的任务不再可建
  （当前没有专门覆盖 `deleted_at` 行为的用例）。
- 字段级修改走 Patch 端点后，刷新页面能看到改动（不再只存组件本地）。
- 一句话创意能建出项目并跑到情节目录。
- 项目概览给出的"下一步"链接全部落在 `/freeflow` 内，
  没有一条跳到 `/projects/{id}`（ADR-030 的可验证前置条件）。
