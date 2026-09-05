# 03 故事、剧本与内容修订

> 状态：**部分实现**（结构化生成已实现；字段级编辑后端已实现但未提交、前端未接）
> 优先级：P0
> 负责人：待定
> 最近核对：2026-09-02

## 0. 2026-09-05 新增的两条缺口（字段级编辑上线后暴露）

1. **`stale_roles` 同名不同义。** 写路径（`PatchResultOut`、`POST /revise`）返回的是
   "**因这次改动而新过期**的下游"，而 `ProjectStateSnapshot.stale_roles` 返回的是
   "**当前全部**过期的阶段"。前端必须知道这个区别才能写对——直接用写路径的返回替换
   本地状态，会把更靠前阶段本来就有的过期标记抹掉。今天前端是靠
   `(旧 ∪ 新) \ {本 role}` 合并绕过去的，那等于在前端复制了一份后端
   `mark_stale` / `mark_fresh` 的语义。
   **修法**：写路径也返回全量，或者改名成 `newly_stale_roles` 让区别显式化。
2. **撤销冲突（409）的 `detail.conflicts` 是 JSON Pointer 原文**（如 `/shots/2/sfx`），
   直接显示给用户不是人话。后端在 `detail` 里多带一个人读字段，或前端做路径翻译。

---

## 1. 模块目标与边界

把用户的小说原文或一句话创意，变成后续图像 / 视频 / 配音能直接消费的**结构化内容**：
情节目录、剧本、角色档案、场景档案、分镜表。并让用户能逐字段改这些内容、看见改了什么、
按批撤销。

边界：

- 本模块拥有**用户对产出的修改**（`content_revisions`）与产出正文所在的
  `projects.current_state_json`。
- **生成**这些产出的是 Agent 模块（04）；本模块不调 Provider、不建任务、不扣费。
- 角色 / 场景一旦要出图，其结构化档案由一致性模块（06）持有；本模块只负责 Agent 的
  原始 JSON，同步动作是 04 做的。
- 分镜表里的"时长 / 段数"属于视频生产参数，语义由 12 定义，本模块只负责它落在哪个字段、
  能不能被用户改。

## 2. 用户与使用场景

- 贴一篇小说原文，或写一句话创意，得到情节目录 → 剧本 → 角色 → 场景 → 分镜。
- 在剧本里改一句台词、在角色档案里改一个发色，保存后立刻看到新值。
- 打开变更历史，看到"谁、什么时候、把哪个字段从什么改成了什么、为什么"。
- 一次改错了，整批撤销。
- 改了上游（比如角色外貌）之后，界面告诉他哪些下游产出已经过期。
- 对某个阶段说一句"这段太平了，加点冲突"，让 Agent 局部返工。

## 3. 当前真实能力

### 3.1 已实现

| 能力 | 证据 |
|---|---|
| 五个阶段的结构化产出，全部有严格 Pydantic schema（`extra="forbid"`） | `agents/schemas.py`：`PlotIndex` / `Screenplay` / `CharacterSheets` / `SceneSheets` / `Storyboard` |
| 产出正文存 `projects.current_state_json`，键名与阶段同名 | `apps/api/modules/agent/orchestrator.py` 的 `_STATE_KEY` / `_save` |
| 自然语言局部返工（`POST /projects/{id}/revise`），记录对话与版本号 | `apps/api/modules/agent/revise.py`、`models.ConversationMessage`；`tests/integration/test_revise_chat.py` |
| 过期记账：上游被改后把下游阶段标为 stale，存进 `current_state_json.stale_roles` | `orchestrator.mark_stale` / `mark_fresh` / `stale_roles`；`tests/integration/test_stale_roles.py` |
| 分镜产出 9 列（镜号 / 节点 / 景别 / 角度 / 运镜 / 画面 / 出场人物 / 场景 / 对白+音效） | `agents/schemas.py` 的 `StoryboardShot` |
| 角色 / 场景用稳定 `ref`（`^[a-z][a-z0-9_]{1,30}$`），不靠显示名关联 | `agents/schemas.py` 的 `REF_PATTERN`；`tests/integration/test_character_ref_rescue.py` |

### 3.2 已实现，但**未提交**（工作树代码，`apps/api/modules/content/`）

这一段全部按 ADR-029 落地，有迁移、有测试、已挂进 `main.py`，但仍停在未提交状态。
**合并前必须跑迁移和测试**（Docker 未开时无法验证）。

| 能力 | 证据 |
|---|---|
| 字段级 Patch：RFC 6901 JSON Pointer，只替换已存在路径，不新建键、不追加数组元素 | `apps/api/modules/content/patching.py` 规则 1 |
| 路径与值的安全校验：拒 `_` 开头的键、拒 `__proto__` / `constructor` / `prototype`、限长 512 / 限深 12 / 值限 20 KB | `patching.py` 规则 2 与 `MAX_*` 常量；`tests/unit/test_content_patching.py`（16 个用例） |
| 校验直接复用产出自己的 Pydantic schema，不另写一套字段规则 | `patching.py` 规则 3 |
| 一次请求 = 一个 `batch_id`，一批最多 200 处改动 | `schemas.MAX_PATCHES_PER_BATCH` |
| 变更历史按批分页、倒序，可按 role 过滤 | `router.list_revisions`；`tests/integration/test_content_revisions.py` |
| 按批撤销 = 反向重放，撤销本身也进历史、也可再被撤销 | `router.undo_revision_batch`、`service.undo_batch` |
| 批内任一字段在这批之后又被改过 → 整批 409，不做部分撤销 | `service.undo_batch` |
| 写路径在同一事务里标记下游 stale，响应体带 `stale_roles` | `service._commit_batch` 调 `agent_service.mark_role_edited` |
| **这条路径一分钱不花**：不建任务、不跑 Agent、不预扣也不结算 | `apps/api/modules/content/` 全模块无 billing 引用 |
| 迁移建 `content_revisions`；`old_value` / `new_value` 是 **NOT NULL 的 JSONB**（JSON null ≠ 这列没填） | `migrations/versions/b5e21a7c9f40_content_revisions.py` |

接口路径（与 `apps/api/modules/content/router.py` 逐条核对过，前缀 `/api/v1`）：

```text
PATCH /api/v1/projects/{project_id}/outputs/{role}
GET   /api/v1/projects/{project_id}/revisions
POST  /api/v1/projects/{project_id}/revisions/{batch_id}/undo
```

> 旧版本文档写的是 `/api/v1/content/{project_id}/outputs/{role}`，**与代码不符**：
> router 的 prefix 是 `/projects`，路径里没有 `content` 这一段。以代码为准。

### 3.3 部分实现

- **字段级编辑的前端**：`apps/web/lib/api.ts` 里没有任何 `outputs/` 或 `revisions` 的封装，
  全仓库前端代码零处引用。后端闭环、前端未接 → 用户视角**还不能改字段**。
- **分集大纲**：`Screenplay` 里有 `Episode`，但"先出分集大纲再写剧本"这一步不存在，
  `_NEXT` 里没有对应阶段。
- **stale 传播**：只标记不传播，且只覆盖五个文本阶段（`PRODUCING_STAGES`），
  不覆盖图、视频、配音。

### 3.4 预留 / 未实现

| 项 | 状态 | 说明 |
|---|---|---|
| `content_revisions.source = "agent_revise"` | 预留 | 常量已在 `models.SOURCES` 里，`revise` 链路不写这张表 |
| 创意入口（一句话 + 参考内容起步） | 未实现 | `advance` 只认 `state["source"]` 一段文本，没有参考内容字段 |
| 角色 / 场景实体化（每个角色一行） | 未实现 | 决策记录 §3.4 明确放到 M2 之后 |
| 内容快照 / 生产锁（被媒体引用的版本不可原地覆盖） | 仅设计 | 无表、无代码 |

## 4. 功能需求

### P0（挡住"逐镜 MP4"）

- **FR-CONTENT-001**：`content` 模块合入主干。合入前跑通 `alembic upgrade head` 与三份测试
  （`tests/unit/test_content_patching.py`、`tests/integration/test_content_patch.py`、
  `tests/integration/test_content_revisions.py`）。
- **FR-CONTENT-002**：前端接上 PATCH / 变更历史 / 撤销三个接口，落在剧本、角色、场景、分镜
  四个视图里。没有这一条，用户改一个错别字也只能重跑 Agent。
- **FR-CONTENT-003**：分镜表要承载**每镜时长**与**段数**。当前 `StoryboardShot` 明确不含这两项
  （注释写着"时长来自 TTS、批次由 15 秒规则切分"），而 ADR-032 第 4 条要求段上限在**分镜阶段**
  就参与计算。二者必须调和：字段加在哪、谁写它（Agent 还是确定性计算）由 12 与 04 共同定，
  本模块负责它可被用户 Patch。
- **FR-CONTENT-004**：角色 / 场景的 `ref` 一经被下游（一致性档案、分镜、图、视频）引用即不可
  静默改变。改 `ref` 要么是显式操作并同步下游引用，要么直接禁止。
- **FR-CONTENT-005**：过期标记覆盖到媒体产出。用户改了某镜台词，该镜的配音必须显示"待重出"
  （ADR-033 第 3、4 条：只标记，重出还是保留由用户决定）。

### P1

- **FR-CONTENT-006**：创意入口——一句话 + 可选参考内容，仍进同一条 `plot_index` 链路
  （决策记录 §3.2 取"创意项目入口"）。
- **FR-CONTENT-007**：分集大纲成为剧本前的一步，让剧本能按集导航（决策记录 §3.2 取"分集大纲"）。
- **FR-CONTENT-008**：`revise` 的结果写进 `content_revisions`（`source="agent_revise"`），
  让"Agent 改的"和"人改的"在同一条历史里。
- **FR-CONTENT-009**：自然语言返工先展示字段级 diff 再落盘；高风险改动（改 `ref`、删角色）二次确认。
- **FR-CONTENT-010**：影响集计算与自动传播（ADR-033 第 4 条把它划为 P1）。

### P2 / 不做

- 改编取舍表、爽点分布表：决策记录 §3.2 判为"不取（P2）"——叙事质量工具，不挡出片。
- 角色 / 美术 / 剧本三者**并行**迭代：决策记录 §3.2 明确"不取"，串行过门是我们比 ReelBench 可靠的地方。
- 角色实体化（每个角色一行 ORM）。
- 道具：`agents/schemas.py` 里没有这个产出，做入口就是假入口。

## 5. 内容依赖与状态

```text
source（小说原文 / 创意）
  → plot_index
  → screenplay                 【门：确认剧本】
  → characters → scenes        （串行）
  → storyboard                 【门：确认分镜】
  → 首帧图 → 配音 → 段视频 → 每镜 MP4
```

四条状态轴分开，不用一个 `status` 表达全部（采纳 `_research/reelbench_ux_review.md` §2.3 的建议）：

| 轴 | 值 | 真相所在 |
|---|---|---|
| 任务运行 | `queued/running/succeeded/failed/cancelled` | `tasks.status`，唯一真相（ADR-008） |
| 审核门 | `pending/approved/rejected/revision_requested` | `approvals`（模块 04） |
| 产出新鲜度 | 新鲜 / 过期 | `current_state_json.stale_roles` |
| 版本 | 当前版 / 历史候选 | ADR-033，媒体侧见模块 12 |

## 6. 数据模型与所有权

- `projects.current_state_json`：五个阶段的产出正文，加 `stage`、`source`、`stale_roles`。
  **不实体化**（决策记录 §3.4）。
- `content_revisions`：字段级改动，一行一个字段，同批共享 `batch_id`。批的元信息
  （reason / actor / source / 时间）冗余在每一行上，不单独建批表——冗余几十字节换掉一张表和一次 join。
- `conversation_messages`：`revise` 的对话与版本号，**不参与任何状态判断**。

现在不为每个 JSON 子对象建 ORM 表。先把四件事做完整：schema、稳定 ref、revision、快照。

## 7. API、事件与前端入口

已实现（未提交）：

```text
PATCH /api/v1/projects/{project_id}/outputs/{role}
GET   /api/v1/projects/{project_id}/revisions?limit&cursor&role
POST  /api/v1/projects/{project_id}/revisions/{batch_id}/undo
```

已实现（模块 04 提供，本模块消费）：

```text
POST  /api/v1/projects/{project_id}/revise
GET   /api/v1/projects/{project_id}/conversation
```

前端落点在 `/freeflow` 下（ADR-030）：`story` / `screenplay` / `characters` / `scenes` /
`storyboard` 五个页面。编辑用表单或表格，**不给用户裸 JSON 编辑器**——裸 JSON 会让用户
以为可以加字段，而 schema 是 `extra="forbid"`。

## 8. 技术选择与工程设计

- Patch 的**全部安全责任**在 `patching.py` 这一层纯函数里：不碰 DB、不碰租户、不知道 HTTP。
  拆出来是为了这些规则能被单元测试逐条打靶。
- 改动与 revision 在同一事务写入；撤销不是 DELETE，是反向重放。
- 只有"替换"一种语义。不支持追加数组元素：追加会让撤销从"写回旧值"变成"删掉一个元素"，
  是另一套语义和另一套并发风险。要加人物就重跑 Agent。
- 拒绝 `_` 开头的键不是 Python 的需要——这份 JSON 会原样进前端，那边 `obj[key] = v` 就是真的原型污染。
- 每个查询带 `org_id`；跨租户返 404 不返 403。

## 9. 模块依赖

- 04 Agent 编排：产出正文的唯一生产者；`mark_role_edited` 由本模块调用。
- 06 一致性：消费 `characters` / `scenes` 产出，同步成档案。
- 12 媒体：消费 `storyboard`，需要每镜时长与段数（见 FR-CONTENT-003）。
- 02 项目：`current_state_json` 的宿主表。

## 10. 当前缺口与风险

| 缺口 | 影响 | 优先级 |
|---|---|---|
| `content` 模块未提交 | 一次 `git checkout` 就全没了，且 CI 从没跑过它 | P0 |
| 前端零接线 | 后端做完了但用户用不上，等于没做 | P0 |
| 分镜 schema 无时长 / 段数 | 与 ADR-032 第 4 条直接冲突，挡住视频阶段 | P0 |
| stale 不覆盖媒体 | ADR-033 的"过期"机制在媒体侧无处落脚 | P0 |
| `revise` 不写 revision 表 | 变更历史缺一半，用户看不到 Agent 改了什么 | P1 |
| 无生产锁 | 已被 MP4 引用的分镜版本可被原地改掉 | P1 |

## 11. 迭代计划

1. 跑迁移 + 三份测试，`content` 模块作为独立 commit 合入。
2. 前端接 PATCH / 历史 / 撤销，先做剧本与分镜两个视图。
3. 与 04、12 定分镜的时长 / 段数字段，改 `StoryboardShot` 并补 schema 样例。
4. 把 stale 记账扩到媒体产出，接上 ADR-033 的"消费了哪一版"。
5. `revise` 写进 `content_revisions`。
6. 创意入口与分集大纲。

## 12. 验收标准与测试

- `alembic upgrade head` 后 `content_revisions` 存在，三份测试全绿。
- 非法路径（越界、`__proto__`、超深、超长、空路径）逐条有单测，返回 4xx 而不是 500。
- 一次 PATCH 只产生一个 batch；撤销该 batch 后产出**逐字节回到**改前状态。
- 批内字段在这批之后被改过时，撤销返回 409 且不写入任何行。
- 跨租户 PATCH / 读历史 / 撤销一律 404。
- PATCH 全程无 `tasks` 行、无 Ledger 流水、余额不动（与 `test_base_image_assign.py` 同款反面断言）。
- 前端保存后无需刷新即显示新值（响应体已带整块 `output`）。
- 改角色外貌后，该角色的下游阶段在界面上显示为过期。
