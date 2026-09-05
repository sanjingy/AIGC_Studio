# 11 Web 工作台与交互

> 状态：**部分实现**（2026-09-03 起 freeflow 是**唯一的壳**，旧壳 `(app)` 已删；「新建 → 推进 → 剧本门 → 分镜门 → 出图」全程在 freeflow 内走通。未做：字段级编辑接 Patch、每镜模型选择、候选版本、逐镜 MP4）
> 优先级：**P0**（ADR-030 的前置条件全部落在本模块）
> 负责人：待定
> 最近核对：2026-09-04（WA / WB / WC / WD 四条工作线交付并经 Lead 验收后）
> 权威顺序：[DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §1 / §2 / §3 / §8 / §11.4 > ADR-029 / 030 / 031 / 032 / 033 > 当前代码 > `_research/`

---

## 1. 模块目标与边界

把生产链路组织成用户能理解、能操作、能验收的界面。工作台**只呈现和触发**真实业务
状态，不自己维护项目、任务、资产或账务的真相。

**拥有**：路由与信息架构、页面组件、前端状态、`lib/api.ts` 这层 transport、
设计系统的落地。

**不拥有**：阶段图（后端 `orchestrator._NEXT` 硬编码，前端排不了）、
执行状态（`tasks`，ADR-008）、Credits 金额、内容正文（`current_state_json`）。

**产品终点口径**（[决策记录](../DECISIONS_2026-09-02.md) §1、ADR-032）：
本模块的终点是**每镜一条带配音的 MP4，可单镜下载，也可批量下载全部镜头**。
**没有"成片页面"、没有"导出中心"、没有时间线编辑器**——整集时间线合成、字幕、
BGM、转场、剪映导出都在"明确不做（M2 内）"里。任何在界面上暗示"整集成片"的
入口都是假入口。

---

## 2. 用户与使用场景

用户是外部创作者，桌面浏览器，1440 / 1920 两档为主。

1. 登录后看到自己的项目，知道每个项目**下一步该做什么**。
2. 新建一个项目，贴进小说或写一句创意，**能把生产跑起来**（今天做不到，§6.1）。
3. 在剧本和分镜两道门上确认或打回，**不用跳到另一套界面**（今天做不到，§6.2）。
4. 改一个字段（角色描述、镜头台词、镜时长），刷新之后改动还在（今天做不到，§6.4）。
5. 出一张图 / 一条视频前，**在界面上选用哪个模型**并看到 Credits 估算（§7.1）。
6. 一个镜头出了三版，选一版当当前版，另外两版留着（§7.2）。
7. 在一个地方看到这个项目所有正在跑的东西，不管它是文本、出图、配音还是视频。
8. 逐镜下载 MP4，或一次打包下载全部（§7.3）。

---

## 3. 当前真实能力

状态词按 [决策记录](../DECISIONS_2026-09-02.md) §0。
**占位页、类型声明、只有路由没有数据的页面一律"预留"，不计入功能。**

### 3.1 freeflow（正式壳）

| 能力 | 状态 | 代码 / 证据 |
|---|---|---|
| 全局两级 IA：72px 导航 rail + 项目大厅 | 已实现 | `components/freeflow/global-nav-rail.tsx`（`NAV` 只有 首页 / 项目 / 资产）、`project-lobby.tsx` |
| 项目 Header + 七个项目 Tab | 已实现 | `project-header.tsx::TABS` = overview / story / characters / scenes / storyboard / assets / tasks |
| 项目概览读真实产出、任务、运行、审核 | 已实现 | `components/freeflow/project/project-overview.tsx` + `lib/freeflow/use-project-output.ts` |
| 故事 / 角色 / 场景 / 分镜四页复用旧壳的真实展示组件 | 已实现 | `story-workspace.tsx` → `plot-index-view` / `screenplay-view`；`characters/page.tsx` → `characters-view`；`scenes/page.tsx` → `scenes-view` |
| 角色 / 场景 / 分镜出图（含进度、重试、基准图三条来路） | 已实现 | `RenderSlot` / `RenderThumb` + `useRenders`，与旧壳同一份状态；S11 / S14 |
| 项目设置（名称 / 类型 / 模型偏好 / 真实删除） | 已实现 | `project/project-settings.tsx`；名称走 `PATCH /projects/{id}`，删除走 `DELETE`（软删）。"成员 / 权限 / 高级"三个假分区与示例字段已删，页面上只剩后端有列的东西 |
| 模型目录页（含 BYOK Key 管理） | 已实现，已进导航 | `app/freeflow/(global)/models/page.tsx` → `model-catalog-page.tsx`；`NAV` 里没有它，全仓没有指向它的链接 |
| 项目根路由默认落点 | 已实现 | `app/freeflow/projects/[id]/page.tsx` redirect → `/overview` |
| `screenplay` 路由 | 已实现 | 是到 `/story` 的**兼容重定向**，不是重复页面 |
| 素材页上传 | 已实现 | `asset-library-grid.tsx` 接 `assets.upload` 三段式直传（多选、串行）；MIME / 大小 / 配额仍全在后端那条链路上 |
| 任务页读 `tasks` | 已实现 | `lib/freeflow/use-tasks.ts` 读 `GET /tasks` + SSE 增量；重试 / 取消按钮由后端状态决定显隐；`MOCK_TASKS` 已删 |
| 推进生产（`advance`）入口 | 已实现 | 概览 / 故事 / 角色 / 场景页的「开始生产 / 推进到下一道门」，经 `lib/freeflow/use-project-state.ts`；原证据： `lib/api.ts:239` 的 `projects.advance()` 在 `app/freeflow` 与 `components/freeflow` 下**零引用** |
| 返工（`revise`）入口 | 已实现 | 故事 / 角色 / 场景 / 分镜页各自的返工框，`target_role` 按页固定；返工对话读 `GET /projects/{id}/conversation` |
| 审批门 | 已实现 | 概览 / 故事 / 分镜页内「确认通过 / 打回重做」，`POST .../approvals/{id}`；`rejected` 故意不给按钮（无恢复路径） |
| **分镜**字段编辑写回后端 | 已实现 | `storyboard/shot-detail.tsx`（版式与控件）+ `shot-editor.tsx`（草稿/脏字段/保存）+ `lib/freeflow/use-content-edit.ts`，走 `PATCH /projects/{id}/outputs/storyboard`。九个字段可改（场景 / 出场人物 / 景别 / 角度 / 运镜 / 画面内容 / 说话人 / 台词 / 音效），镜号与节点号只读；一次保存 = 一个可撤销批次；用响应里的 `output` 就地更新，不 refetch |
| 字段级改动历史 + 批次撤销 | 已实现 | `storyboard/revision-history.tsx` 抽屉，读 `GET /revisions?role=storyboard`、撤销走 `POST /revisions/{batch}/undo`；已撤过的批按 `undone_by_batch_id` 提前禁用。**不进主导航**（理由见 §6.4） |
| 故事 / 角色 / 场景字段编辑写回后端 | 未实现 | 后端同一条 PATCH 端点只是 role 不同，前端只做了分镜（本轮范围）。这三页仍然是只读展示 + 自然语言返工 |
| 每镜模型选择、候选版本、逐镜 MP4 下载 | 未实现 | 原来不发请求的「生成视频 / 重新生成 / 换模型」假按钮已删；模型选择卡在 `GET /model-options`，视频是 M2 |
| 节点画布 Canvas | 预留 | `app/freeflow/projects/[id]/canvas/page.tsx` + `components/freeflow/canvas/`（12 文件）；数据来自 `lib/freeflow/mock-data.ts`，不接后端。ADR-030 第 5 条：搁置 |
| 悬浮 AI 助手 | 预留 | `components/freeflow/floating-assistant.tsx` **全仓零引用**（`freeflow/layout.tsx` 注释说明有意不挂载） |
| 全局占位页 `members` / `servers` / `templates` / `skills` / `billing` / `settings` | 预留 | 六个都只 `return <GlobalPlaceholder title=… />`，没有数据 |

> **一处口径纠正**：ADR-030 第 4 条说"`skills` 页保留，它诚实标注了运行时未接线"。
> 代码里 `app/freeflow/(global)/skills/page.tsx` 是 `GlobalPlaceholder`（"此功能尚未接入"），
> **不是技能库**——真正的技能列表在 `/freeflow/assets` 的「技能」chip 里。
> 处置见 [`10_SKILL_WORKFLOW.md`](10_SKILL_WORKFLOW.md)，本文档不重复。

### 3.2 旧壳 `(app)`（ADR-030：**已于 2026-09-03 删除**）

`app/(app)/` 整目录、`components/shell/*`、`components/project-composer.tsx` 与 §5.3 列出的孤儿组件已全部删除；`/`、登录后落点、`/dashboard` 三处都指向 `/freeflow`（`app/dashboard/page.tsx` 只剩重定向）。下表是删除前的记录，保留作对照：

| 页面 | 删除前状态 | 说明 |
|---|---|---|
| `(auth)/login` | 已实现 | **唯一保留项** |
| `(app)/dashboard` | 已实现但要删 | 改成重定向到 `/freeflow` |
| `(app)/projects/[id]` | 已实现但要删 | 四栏工作台。**它是 `advance` / `revise` / 审批的唯一入口**，删之前必须先把这几条能力迁到 freeflow |
| `(app)/tasks` | 已实现但要删 | 读真实 `tasks` + SSE，但带 `mock.echo` / `mock.fail` 调试按钮 |
| `(app)/assets` | 已实现但要删 | — |
| `(app)/settings/keys` | 已实现但要删 | **模型目录页有一条链接指向它**（`model-catalog-page.tsx:390`），删它会留死链，见 §5.4 |

---

## 4. 功能需求

P0 = 挡住"逐镜 MP4"或挡住 ADR-030 删旧壳的。

### 4.1 P0

| 编号 | 需求 | 依据 |
|---|---|---|
| FR-WEB-001 | freeflow 提供**「开始 / 推进生产」入口**，调 `projects.advance()`，覆盖从路线判断到分镜的全部非门阶段 | §11.4 裁决 2；ADR-030 第 2 条 |
| FR-WEB-002 | 剧本门、分镜门的**确认 / 打回**在 freeflow 内完成，不跳回 `/projects/{id}` | ADR-030 第 2 条 |
| FR-WEB-003 | 返工（`revise`）在 freeflow 内有入口，能带用户的修改意见 | ADR-030 第 3 条 |
| FR-WEB-004 | 故事 / 角色 / 场景 / 分镜的字段级修改走 ADR-029 的 Patch 端点，刷新后仍在；不再有"只存本地"的可编辑控件 | ADR-029；决策记录 §3.2 | **分镜已完成**（2026-09-05）；故事 / 角色 / 场景仍未接 |
| FR-WEB-005 | 任务页统一读 `tasks`，按需关联 `agent_runs` 展示推理细节；删掉 `MOCK_TASKS` | ADR-008；[`08_TASK_REALTIME.md`](08_TASK_REALTIME.md) §7 |
| FR-WEB-006 | 每镜提供**模型选择按钮**（不是让用户发消息），带 Credits 估算与 Key 来源 | ADR-031；决策记录 §4 |
| FR-WEB-007 | 每镜的图与视频以**多候选 + 恰好一个当前版**呈现，切换当前版不删旧候选 | ADR-033 |
| FR-WEB-008 | 逐镜 MP4 下载 + 批量下载（服务端打 zip，走一个任务，完成后给预签名 URL） | ADR-032；§11.1 裁决 8 |
| FR-WEB-009 | 旧壳按 §5 的文件级清单删除；`/`、登录后默认落点改 `/freeflow`，`/dashboard` 保留为重定向 | ADR-030 第 1 条 |
| FR-WEB-010 | 上游产出变更后，下游显示"过期"标记，并提供「重出」与「保留当前版」两个动作；**只标记，不自动传播** | 决策记录 §3.2 |
| FR-WEB-011 | 主导航不出现任何未实现入口；已实现的页面（模型目录）必须有入口 | 决策记录 §2 |

> FR-WEB-004 的表头比其他几行多一列「状态」，因为它是本表里唯一部分完成的：
> 补一整列会让其余十条都写上「未开始」这种没有信息量的字。

> **FR-WEB-010（过期标记）在分镜这一段也落地了**，但落的是 ADR-033 第 4 条的
> "只标记"那一半：改了某一镜之后，那一镜已经出的图会显示「图可能过期」，
> 界面**不自动重跑、不删旧图**。需求里的「重出」复用既有的「再出一张」
> （会再扣一次 Credits），「保留当前版」不给按钮——不点就是保留，
> 给一个什么都不做的按钮只会让人以为它做了什么。

### 4.2 P1

| 编号 | 需求 |
|---|---|
| FR-WEB-020 | 素材页接上传，复用资产模块现成的三段式直传（MIME 白名单、大小上限、容量配额都在那条链路上，前端不另写一套阈值） |
| FR-WEB-021 | 创意入口：一句话 + 可选参考内容起项目（决策记录 §3.2「取」） |
| FR-WEB-022 | 分集大纲导航（剧本按集浏览的前提，决策记录 §3.2「取」） |
| FR-WEB-023 | 生成动作统一显示"估算 → 排队 → 运行 → 完成 / 失败 → 重试"，失败展示可读原因与 `X-Trace-Id` |
| FR-WEB-024 | Playwright 黄金路径 E2E（登录 → 建项目 → 剧本确认 → 分镜确认 → 出图），决策记录 §8「进 Wave 1」 |
| FR-WEB-025 | 引入 TanStack Query 承载服务端状态，SSE 只做缓存失效；引入 OpenAPI TS 类型生成，逐步消除业务 `any` |
| FR-WEB-026 | 1280–1920 不产生 body 横向滚动；键盘焦点、ARIA、对比度、最小点击区域满足底线 |
| FR-WEB-027 | 恢复 lint 门禁（需先补 ESLint 9 flat config，见 §14.2） |

### 4.3 P2 / 冻结 / 不做

| 项 | 处置 | 依据 |
|---|---|---|
| 节点画布 Canvas | **冻结**：路由从导航隐藏，代码保留，不接后端 | ADR-030 第 5 条；决策记录 §9 |
| 悬浮 AI 助手 | **删**（它只返回本地预设回复） | 决策记录 §2 |
| 从模板新建项目（首页模板区） | **删** | 决策记录 §3.2「不取」；§11.4 裁决 1 |
| 成员 / 服务器 / 模板 / 账单 / 全局设置 占位页 | 删路由，或保持不进导航 | 决策记录 §2、§1（不做支付、团队成员） |
| 图片 AI 编辑、多参考图提交 | 不做（M2 后） | 决策记录 §3.2 |
| **整集成片页、导出中心、时间线编辑器、字幕 / BGM / 转场 / 剪映导出** | **不做** | 决策记录 §1；终点是逐镜 MP4 + 批量 zip |
| 改编取舍 / 爽点表等叙事质量工具 | P2 | 决策记录 §3.2 |
| 字段级变更历史 UI / 批次撤销 UI | **分镜已做**（2026-09-05，随 FR-WEB-004 一起）；其余 role 仍 P2 | — |

---

## 5. 旧壳删除清单（文件级，可直接当作后续 Worker 的任务输入）

> **2026-09-03 已按本清单执行完毕**（WD 工作线，Lead 验收：`typecheck + build` 绿，旧壳路径零引用，`MOCK_` 只剩 canvas 目录）。清单保留作记录。唯一偏离：`components/project/skill-upload.tsx` 没删——Skill 上传入口迁到了资产库「技能」chip，它的 hook 被 `asset-library-grid.tsx` 复用。

### 5.1 直接删除的路由

```
apps/web/app/(app)/layout.tsx
apps/web/app/(app)/dashboard/page.tsx          ← 不是删干净，改成重定向到 /freeflow（决策记录 §2 明确要求保留这条重定向）
apps/web/app/(app)/projects/[id]/page.tsx
apps/web/app/(app)/tasks/page.tsx
apps/web/app/(app)/assets/page.tsx
apps/web/app/(app)/settings/keys/page.tsx
```

**保留**：`apps/web/app/(auth)/login/page.tsx`。

### 5.2 重定向要改三处（不是一处）

| 文件 | 现状 | 改成 |
|---|---|---|
| `apps/web/app/page.tsx` | `redirect("/dashboard")` | `redirect("/freeflow")` |
| `apps/web/app/(auth)/login/page.tsx:19` | 登录后默认 `next = "/dashboard"` | `/freeflow` |
| `apps/web/app/(app)/dashboard/page.tsx` | 真实页面 | 只留 `redirect("/freeflow")` |

### 5.3 随之成为孤儿、可一并删除

已用精确 import 核对。删除前**再跑一次全仓 import 检查**，因为期间可能有新引用。

```
components/shell/sidebar.tsx          只被 (app)/layout.tsx + (app)/projects/[id]/page.tsx 引用
components/shell/topbar.tsx           只被 (app)/layout.tsx 引用（freeflow 用自己的 global-topbar）
components/shell/workspace.tsx        只被 (app) 两处 + shell/sidebar + shell/topbar 引用
components/shell/page-scroll.tsx      只被 (app) 四个页面引用
components/project-composer.tsx       只被 (app)/projects/[id]/page.tsx 引用
components/live-indicator.tsx         只被 (app)/tasks/page.tsx 引用
components/project/asset-panel.tsx        只被 (app)/projects/[id]/page.tsx
components/project/agent-runs-view.tsx    同上
components/project/chat-transcript.tsx    同上
components/project/output-card.tsx        同上
components/project/output-drawer.tsx      同上
components/project/project-rail.tsx       同上
components/project/stale-notice.tsx       同上
components/project/storyboard-view.tsx    同上（storyboard-editor 只在注释里提到它）
components/project/stages.ts              只被上面这些 + shell/* 引用
components/project/skill-upload.tsx       只被 project-composer + shell/sidebar 引用 → 见 10_SKILL_WORKFLOW.md FR-SKILL-020
```

### 5.4 **绝对不能删**：freeflow 正在 import 的 `components/project/` 组件

> `components/project/` **不是一个整体可删的目录**。误删下面六个，freeflow 的
> 故事 / 角色 / 场景 / 分镜四个页面会同时编译失败。

```
plot-index-view.tsx    ← freeflow/project/story-workspace.tsx
screenplay-view.tsx    ← freeflow/project/story-workspace.tsx
characters-view.tsx    ← freeflow/projects/[id]/characters/page.tsx、freeflow/asset-library-grid.tsx
scenes-view.tsx        ← freeflow/projects/[id]/scenes/page.tsx、freeflow/asset-library-grid.tsx
render-slot.tsx        ← freeflow/project/project-assets.tsx、freeflow/project/storyboard-editor.tsx
asset-picker.tsx       ← 被 render-slot.tsx 引用，因此也保留
```

另有一处删除会留下**死链**：`components/freeflow/model-catalog-page.tsx:390`
有一个 `href="/settings/keys"`（文案「在设置页统一管理」）。删掉
`(app)/settings/keys` 之前，要么把 BYOK Key 管理搬进 freeflow 的某个真实页面，
要么删掉这条链接。**不能留着指向 404。**

### 5.5 freeflow 内的清理

| 对象 | 处置 | 文件 |
|---|---|---|
| 成员 / 服务器 / 模板 三个空壳 | 删路由 | `app/freeflow/(global)/members/page.tsx`、`servers/page.tsx`、`templates/page.tsx` |
| 悬浮 AI 助手 | 删文件（**已无任何引用，删它不会破坏编译**） | `components/freeflow/floating-assistant.tsx` |
| 首页模板区 | 删（"从模板起手"属于决策记录 §3.2 未取的功能） | `components/freeflow/home-template-grid.tsx` |
| `mock-data.ts` | **移到 `components/freeflow/canvas/` 目录下，随 Canvas 一起搁置**，并删掉 canvas 之外的全部引用 | `lib/freeflow/mock-data.ts` → `components/freeflow/canvas/mock-data.ts` |

`mock-data.ts` 的处置按 [§11.4 裁决 1](../DECISIONS_2026-09-02.md)：它今天同时被
`canvas/workflow-canvas.tsx:23`（`MOCK_GRAPH`）和 `home-template-grid.tsx:6`
（`MOCK_TEMPLATES`）引用。删首页模板区的引用 + 把文件移进 canvas 目录，
让"主链路上没有 mock"和"Canvas 冻结时自带它的假数据"两条决定同时成立。

导航侧**已经先行清理过**：`global-nav-rail.tsx` 的 `NAV` 只剩 首页 / 项目 / 资产，
`project-global-rail.tsx` 同样三项。所以决策记录里"摘出导航"这一步实际已完成，
剩下的是删路由与删文件。

`billing` / `settings` 两个全局占位页决策记录没点名，但它们与 `members` 同形
（`GlobalPlaceholder`）：**要么删，要么保证不进导航**。今天它们不在 `NAV` 里，
满足 FR-WEB-011 的底线，本轮不强制删。

### 5.6 Canvas：路由隐藏、代码保留（ADR-030 第 5 条）

保留 `app/freeflow/projects/[id]/canvas/page.tsx` 与 `components/freeflow/canvas/`
（12 个文件）。但要摘掉三处**通往 canvas 的跳转**，否则"从导航隐藏"名存实亡：

```
components/freeflow/home-project-grid.tsx:126   项目卡片 href      → 改 /freeflow/projects/{id}/overview
components/freeflow/home-project-grid.tsx:204   点击卡片 router.push → 同上
components/freeflow/home-quick-start.tsx:55     新建项目后的落地页   → 同上
```

后两处是**新建项目后的落地页**——今天新建完直接跳进一个不接后端的画布，
是当前 freeflow 最误导的一处交互。

---

## 5.7 项目大厅的卡片显示的是两个从来不动的列（2026-09-05 Lead 服务器验收发现）

`components/freeflow/project-lobby.tsx::ProjectCard` 上有两处标签：

| 位置 | 读的是 | 实际情况 |
|---|---|---|
| 右上角状态徽标 | `projects.status` | **编排器从不推进它**，永远是 `draft`。徽标因此永远显示"草稿" |
| 卡片底部一行 | `project.route_type ?? "等待路线判断"` | `route_type` **从来没有任何代码写过**，永远是 null，于是永远显示"等待路线判断" |

服务器实测：一个已经跑完剧本门、角色、场景、分镜、十张分镜图、
`current_state_json.stage` 已经是 `done` 的项目，在大厅里显示的是
**"草稿 / 等待路线判断"**。而点进去，项目页顶部的阶段条显示的是"05 生成 · 当前阶段"。
同一个项目，两处说法互相矛盾，且大厅那处是用户进入产品后看到的第一屏。

这不是本轮引入的。`projects.status` 不被推进这件事在别处已经记过（项目设置页把它
降级标注成"项目记录状态"），但大厅这两处**没有做同样的降级处理**，仍然把它们当成
进度在展示。

**修法**：大厅卡片改用推导出的真实阶段。数据来源已经有了——
`GET /projects/{id}/state` 返回 `stage`。要避免列表页 N+1 请求，
更干净的做法是让 `ProjectOut`（或列表接口）带上 `stage`，由后端算一次。
`route_type` 那一行要么接上真实的路线判断产出，要么直接删掉——
它今天是一句永远为真的空话。

---

## 6. freeflow 独立闭环缺口（ADR-030 的唯一前置条件）

> **2026-09-04 Lead 手动验收结论**：在香港测试机上从注册走到十张分镜首帧图，
> 全程没有跳出 freeflow——注册 → 新建项目 → 填原始素材 → 开始生产 → 剧本门确认 →
> 推进到角色/场景 → 角色出基准立绘 → 分镜门确认 → 批量出图 10 镜 → 生成队列看真实扣费。
> 十张图 10/10 成功。**ADR-030 的前置条件已由运行验证，不再只是代码判断。**
> 两处与前端无关的环境问题另记：图像链路缺 Mock 回退（模块 06 FR-CONS-011）、
> 预签名地址写死 localhost（模块 14 FR-OPS-019）。

> **2026-09-03 状态**：6.1 / 6.2 / 6.3 / 6.5 / 6.6 已关闭（WB 工作线）。6.4 关了一半：假的本地编辑删掉了，Patch 端点前端仍未接（FR-WEB-004 仍开）。前置条件已满足，旧壳已删。下文保留删除前的缺口记录。

> **2026-09-05 状态**：6.4 的分镜部分已关闭（WF 工作线）——三条 Patch / 历史 / 撤销端点在分镜页全部接上，
> 浏览器实测走通"改字段 → 保存 → 刷新还在 → 整批撤销回旧值 → 下游图标过期"。FR-WEB-004 仍开，
> 剩故事 / 角色 / 场景三页。

前置条件：「新建项目 → 剧本确认 → 分镜确认 → 出图」全程不跳回旧页。
下面**逐处指到文件**，除 6.6 外都是 P0。

### 6.1 缺"开始 / 推进生产"入口（最严重，且比 ADR-030 假设的更靠前）

`lib/api.ts:239` 的 `projects.advance()` 在整个 `app/freeflow` 与
`components/freeflow` 下**零引用**。全仓唯一的调用点是
`components/project-composer.tsx:133` 与 `:345`——那个文件在 §5.3 的删除清单里。

后果：在 freeflow 里新建项目之后，**没有任何地方能让编排器跑起来**。
不是"审批跳回旧页"这种局部缺口，是**连开始都不行**。

按 [§11.4 裁决 2](../DECISIONS_2026-09-02.md)，这一条计入 ADR-030 前置条件的
缺口清单，并且排在"审批门、`revise` 迁入 freeflow"**之前**——
没有入口，后面的审批门迁移无从验证。

### 6.2 审批门跳回旧页

`components/freeflow/project/project-overview.tsx:482` 是 freeflow 侧
**唯一**指向 `/projects/{id}` 的链接，文案写着"审核会推进真实生产阶段，
因此只在主线审核入口处理"。剧本门和分镜门都卡在这里。

### 6.3 缺返工（`revise`）入口

`lib/api.ts:256` 的 `projects.revise()` 在 freeflow 下零引用。
`storyboard-editor.tsx:185` 只是在提示文案里提到这个端点。

### 6.4 分镜字段编辑（**2026-09-05 已接上**；故事 / 角色 / 场景仍未接）

ADR-029 的三条 Patch 端点后端一直都有（`apps/api/modules/content/`），
前端此前一行没接。分镜这一段现在接上了：

| 端点 | 前端落点 |
|---|---|
| `PATCH /projects/{id}/outputs/storyboard` | `lib/api.ts::projects.patchOutput`，由 `shot-editor.tsx` 的「保存这一镜」触发 |
| `GET /projects/{id}/revisions?role=storyboard` | `projects.revisions`，「改动记录」抽屉 |
| `POST /projects/{id}/revisions/{batch}/undo` | `projects.undoRevision`，每批一个「撤销」 |

几条定死的交互决定，改之前先读明白为什么：

- **一次保存 = 一个请求 = 一个批次。** 改了 3 个字段点一次保存就发一条
  带 3 个 patch 的请求。拆成 3 个请求的话，用户眼里的一次操作要点三次
  撤销才退得回去。
- **不 refetch。** 响应里带着改完之后的整块产出，直接并回
  `useProjectState`（`applyPatchedOutput`）。改完再 GET 一次，中间那段
  时间界面还是旧值，看起来像"保存了但没生效"。
- **过期记账要合并不能替换。** 响应里的 `stale_roles` 只是"因为这次改动
  而新过期的下游"（后端 `mark_role_edited` 返回 `downstream_of(role)`，
  同时把 `role` 自己划掉），比它更靠前的阶段的过期标记仍在库里。
  本地算 `(旧 ∪ 新) \ {role}`。实测：改角色档案 → 响应
  `['scenes','storyboard']`；接着改分镜 → 响应 `[]`，而库里是 `['scenes']`。
  直接替换会把 `scenes` 的标记在界面上抹掉。
- **镜号 / 节点号只读。** 后端收它们的 patch，但镜号是出图记录
  （`tasks.input_json.shot_index`）唯一的关联键，改掉它会让这一镜已经出的图
  默默挂到另一镜上，而撤销修不回来（撤回来的是分镜，图的归属不会跟着回去）。
- **JSON Pointer 里的下标是数组位置，不是 `shot.index`。** 两者在正常产出里
  差 1，但 Agent 不保证镜号连续，照镜号算会改到别的镜上去。
- **前端不写第二套字段白名单。** 合法性一律由后端按 `StoryboardShot` 判。
  景别用 `datalist` 建议而不是 `select` 白名单，字数只显示计数不做硬截断——
  两处都是"后端放宽的那天前端不会拦住合法输入"。
- **未保存的改动有三道提示**：字段旁的脏标记、底栏的未保存横幅、切换镜头时的
  确认弹窗，外加 `beforeunload`。站内点侧栏跳走拦不住（App Router 没有稳定的
  路由拦截钩子），这是已知缺口。

**「改动记录」不进主导航**：这份历史是按 role 过滤的，它回答的是"我刚才把
这一镜改成什么了"，属于正在编辑的那份产出，不是一个独立的目的地。给它一个
顶级位置，用户得先离开正在改的东西才能看它改了什么。

仍然没做的（都不是这次的范围，且都缺后端能力）：

| 缺口 | 为什么没做 |
|---|---|
| 增删镜头 | 后端 `patching.py` 规则 1 明确不支持数组追加/删除，只能整段重生成 |
| 镜时长 | `StoryboardShot` 里根本没有这个字段（schema 注释：时长来自 TTS 真实音频长度）。按 §11.1 裁决 3 要先加字段 |
| 镜头级参考图上传 | 后端 `advance` / `revise` 只收文本，既没有字段也没有上传通道 |
| 故事 / 角色 / 场景的字段编辑 | 同一条端点只是 role 不同，前端未做（FR-WEB-004 剩下的一半） |

### 6.5 任务页读错表

`components/freeflow/project/task-center.tsx` 读 `projects.runs()`（`agent_runs`），
另有一段写死的 `MOCK_TASKS`。必须改读 `tasks`，四条理由见
[`08_TASK_REALTIME.md`](08_TASK_REALTIME.md) §7；最硬的一条是
**出图 / 视频 / 配音 / 合成根本不产生 `agent_runs`**——用 Run 列表当任务中心，
逐镜 MP4 的每一步都看不见。

### 6.6 素材页不能上传（P1）

`components/freeflow/asset-library-grid.tsx:209` 按钮 `disabled`。
它**不挡** ADR-030 的前置条件（前置条件里没有上传），列为 FR-WEB-020。

### 6.7 **不缺**：出图

角色 / 场景 / 分镜出图在 freeflow 里是通的（`RenderSlot` / `RenderThumb` /
`useRenders`，与旧壳共用同一份状态与端点）。基准图三条来路
（AI 生成 / 从资产库选 / 本地上传，S14）在 freeflow 里也是通的。这一条不用做。

---

## 7. 新增页面需求（只写需求与数据依赖，不画稿）

### 7.1 每镜的"模型选择按钮"（ADR-031、决策记录 §4）

- **形态**：界面上的按钮 / 下拉，**不是让用户发消息**。落点是分镜表每一行，
  以及出图 / 出视频的动作旁。
- **数据依赖**：`GET /model-options?capability=&project_id=&task_type=&units=`
  （接口形状见 [`05_MODEL_GATEWAY.md`](05_MODEL_GATEWAY.md) §7，**当前未实现**，
  是本需求的硬阻塞）。
- **必须同时显示**：每个候选模型的 Credits 估算、Key 来源（平台 Key / 用户自带 Key）。
  换模型必重算 Credits 预估（ADR-024 硬约束 1，**未被 ADR-031 推翻，仍然有效**）。
- **作用域**：三层默认里的第三层——**只对本次生成生效，不回写项目或组织默认**。
- **failover 仍然开着**：实际用了别家时，结果里显著标注
  "实际使用 X，你选的 Y 失败"（§11.2 裁决 4、ADR-031 第 7 条）。
- 视频能力还要显示该模型的**单段最大时长**，它是分镜阶段的输入（ADR-032）。
- 现状：`storyboard-editor.tsx` 上的「生成视频 / 重新生成 / 换模型」三个按钮
  **不发任何请求**（`:62` 有说明文案），是占位，不能当已实现。

### 7.2 候选版本切换（ADR-033、决策记录 §5）

- **界面要求**：同一镜的多个候选并排；**恰好一个当前版**；切换当前版不删旧候选；
  每个候选显示生成模型、比例、分辨率、生成时间。
- **数据依赖**：候选列表接口 + 显式 `is_current` 字段。
  按 [§11.1 裁决 1](../DECISIONS_2026-09-02.md)，**候选表由各业务模块自建**
  （首帧图候选、配音候选、段视频候选），`assets` 只存文件与血缘，
  **不加"变体组 / 当前版"字段**。接口归属见
  [`07_ASSET_LIBRARY.md`](07_ASSET_LIBRARY.md) §5 与
  [`12_MEDIA_TIMELINE_EXPORT.md`](12_MEDIA_TIMELINE_EXPORT.md)。
- **作废的旧约定**：`GET /projects/{id}/images` 那套"取第一条就是当前版"
  必须换成显式 `is_current`（决策记录 §5）。前端凡是依赖"第一条"的地方都要改。
- **过期标记**（FR-WEB-010）：上游换了当前版之后，下游只显示"过期"，
  给「重出」和「保留当前版」两个动作，**不自动传播**（决策记录 §3.2）。
- 用户手改过的镜时长**锁定**；TTS 真实时长超出时界面提示"配音比镜长"，
  不静默覆盖（§11.1 裁决 7）。
- 状态区分不能只靠颜色：「当前版 / 旧版 / 过期 / 待重出 / 失败」一律
  图标 + 文案 + 颜色三件套。

### 7.3 逐镜 MP4 下载（ADR-032）

- **单镜下载**：每镜一条带配音的 MP4。数据依赖模块 12 的合成产物
  + 模块 07 的 `download-url`（已实现，900 秒预签名）。
- **批量下载**：**服务端打 zip，走一个任务，完成后给预签名 URL**
  （§11.1 裁决 8）。前端形态因此是"发起打包任务 → 在任务中心看进度 →
  完成后下载"，**不是前端逐个拉**。
- **没有"成片页"**。下载入口挂在分镜页（单镜）和项目概览 / 任务中心（批量）。

---

## 8. 目标信息架构

```text
全局（72px rail）
  首页 / 项目 / 资产 / 模型          ← 「模型」要补进 NAV（FR-WEB-011）

项目（Header + Tab）
  概览 / 故事 / 角色 / 场景 / 分镜 / 素材 / 任务
  项目设置（Header 右上「…」进入）：基础 / 模型 / 预算 / 删除

隐藏（不进导航）
  Canvas（ADR-030 第 5 条，搁置）
  screenplay（到 /story 的兼容重定向）
```

- 「场景与道具」这个 Tab 名**超前于真实能力**：后端只有场景实体，
  `agents/schemas.py` 里没有道具产出。建议改回「场景」，或等实体化后再加。
- **不加「成片」页**（决策记录 §1）。

---

## 9. 前端数据设计与工程约定

- transport 统一在 `lib/api.ts`；按领域拆分时保持同一层 transport 与错误处理。
- 服务端状态引入 TanStack Query（**尚未进 `package.json`**，FR-WEB-025）；
  SSE 事件只做缓存失效或最终状态更新，不在前端拼装状态。
- URL 保存项目、页面与可分享筛选状态；刷新不丢上下文。
- Zustand 仅用于抽屉、编辑器选择、画布视口等临时交互（同样尚未引入）。
- **不在前端制造假进度、假任务、假资产、假余额。** 今天违反这条的有三处：
  `task-center.tsx` 的 `MOCK_TASKS`、`home-template-grid.tsx` 的 `MOCK_TEMPLATES`、
  `storyboard-editor.tsx` 的三个占位按钮。
- 路由页面只负责数据装配与布局，业务 mutation 放 hook / service。
- 跨页面复用的领域组件：ProjectCard、TaskStatus、AssetPicker、ApprovalGate，
  以及本轮新增的 **VersionSelector**（§7.2）与 **ModelPicker**（§7.1）。
- Error Boundary + Toast / Inline Error 分级 + 失败时展示 `X-Trace-Id`
  （后端中间件已在响应头里回传它）。
- 同一业务动作全站只有一个标准入口 / 组件（S14 的 `BaseImageActions` 是范例：
  两个落点共用一份实现）。

---

## 10. 设计系统

- Token 基线仍是 `design-system/aigc-studio/MASTER.md`，**但 §5 的四栏版式作废**
  （ADR-030 代价第 2 条），需要一次设计系统文档同步：freeflow 是
  「Topbar + 全局 rail + 主编辑区 + 可折叠 inspector」，不是旧壳的四栏。
- 延续 neutral slate + teal，桌面高密度生产工具定位；边框和分区优先，
  少用大圆角卡片墙和无意义阴影。
- 图片、视频、状态和真实结果是视觉主体；图像评审区用纯中性 surface，
  不用彩色底影响颜色判断。
- 尺寸建议（`_research/workbench_redesign_spec.md`）：Topbar 56px、
  全局 rail 72px（1920 可 80px）、主编辑区 `minmax(600px,1fr)`、
  inspector 320–340px（1920 400–440px）、内容内边距 24px（1920 32px）。
- **禁止 body 横向滚动**；空间不足时按"rail 收窄 → 隐藏层级列 →
  inspector 变覆盖抽屉 → 分镜预览/提示词切 tab"的顺序降级，
  不得把两个编辑列各压到 300px 以下。
- 动效 150–200ms ease-out，仅用于状态与层级反馈，支持 `prefers-reduced-motion`；
  不用 shimmer 骨架屏，用静态占位。

---

## 11. 模块依赖

| 依赖 | 用途 | 风险 |
|---|---|---|
| 02 项目与工作区 | 项目 CRUD、`current_state_json`、`advance` / `revise` | `advance` 接 reserve/settle 后（§11.1 裁决 5）前端要处理"余额不足"与幂等冲突 |
| 03 内容与故事 | ADR-029 的 Patch / 修订历史 / 批次撤销端点 | 分镜已接（2026-09-05）；故事 / 角色 / 场景未接，是 FR-WEB-004 剩下的一半 |
| 05 模型网关 | `GET /model-options` | **未实现**，FR-WEB-006 被它硬挡 |
| 06 一致性与出图 | 角色 / 场景 / 镜头出图 | 已通 |
| 07 资产库 | 素材列表、上传、`download-url` | 上传前端未接 |
| 08 任务与实时 | `tasks` + 项目 SSE | 任务页读错表 |
| 09 计费 | Credits 估算与余额 | 模型选择按钮要显示估算 |
| 12 媒体与合成 | 逐镜 MP4、批量 zip | M2，未实现 |

---

## 12. 当前缺口与风险

1. **两套壳并存**，重复的那一份永远是假的（freeflow 的故事编辑存本地、
   任务页读 `agent_runs`）。ADR-030 已定终止条件，但前置条件（§6）一条都没做。
2. **`advance` 零引用**可能改变对 ADR-030 工作量的估计——
   它比"审批跳回旧页"严重一个量级。
3. **删除清单被误读的风险**：如果后续 Worker 把 `components/project/`
   当成整目录可删，freeflow 四个页面会同时编译失败。§5.4 的六个文件必须原样保留。
4. **没有 E2E**，视觉改动容易静默破坏主链路（FR-WEB-024）。
5. **ESLint 9 没有 flat config**：`apps/web` 下没有任何 eslint 配置文件，
   `package.json` 有 `lint: eslint .` 脚本但跑不出结果。因此 CI 地板**不含 lint**（§14.2）。
6. 部分 freeflow 组件写成超长单行 JSX（如 `project-header.tsx`），审查和 diff 成本高。
7. `/freeflow/models` 已实现却没有入口，等于白做；`/freeflow/skills` 是占位页
   却被 ADR-030 描述成技能库。两处口径都要收拾。

---

## 13. 迭代计划

**Wave 1（ADR-030 前置条件，全部 P0）**

1. ~~freeflow 加「开始 / 推进生产」入口（FR-WEB-001）~~ **已完成 2026-09-03**。
2. ~~审批门 + `revise` 迁入 freeflow（FR-WEB-002 / 003）~~ **已完成 2026-09-03**。
3. ~~分镜编辑接 ADR-029 Patch 端点（FR-WEB-004 的分镜部分）~~ **已完成 2026-09-05**（含改动历史与批次撤销）。故事 / 角色 / 场景三页仍未接。
4. ~~任务页切到 `tasks`，删 `MOCK_TASKS`（FR-WEB-005）~~ **已完成 2026-09-03**。
5. ~~跑通黄金路径不跳回旧页 → 按 §5 删旧壳（FR-WEB-009）~~ **已完成 2026-09-03**。
6. ~~CI 加前端 `typecheck + build`（§14.2）~~ **已完成**（`ci.yml` 的 `web` job）；Playwright 黄金路径（FR-WEB-024）未做。

**Wave 2（逐镜 MP4 的界面）**

7. 模型选择按钮（FR-WEB-006，依赖 `GET /model-options`）。
8. 候选版本 + 当前版 + 过期标记（FR-WEB-007 / 010）。
9. 逐镜 MP4 下载 + 批量 zip（FR-WEB-008）。

**Wave 3（补齐与提质）**

10. 素材上传、创意入口、分集大纲（FR-WEB-020 / 021 / 022）。
11. TanStack Query + OpenAPI 类型生成（FR-WEB-025）。
12. 设计系统文档同步（MASTER §5 四栏版式作废）；恢复 lint（FR-WEB-027）。

**冻结**：Canvas（ADR-030 第 5 条、决策记录 §9），M2 之后再评估。

---

## 14. 验收标准和测试

### 14.1 功能验收

- 用户只看到**一套**正式工作台；`(app)` 下除 `/login` 外没有可达页面，
  `/`、登录后默认落点、`/dashboard` 三处都到 `/freeflow`。
- 「新建项目 → 剧本确认 → 分镜确认 → 出图」**全程不出现旧壳链接**。
  可用一条 grep 断言：`app/freeflow` 与 `components/freeflow` 下指向
  `/projects/`、`/tasks`、`/assets`、`/dashboard`、`/settings/keys` 的链接数为 0
  （今天是 2 条：`project-overview.tsx:482`、`model-catalog-page.tsx:390`）。
- 故事、分镜、审批、任务、素材操作**刷新后不丢失**；界面上不存在
  "改了不写回后端"的可编辑控件。
- 主导航不出现未实现入口；已实现的模型目录页有入口。
- 每镜能选模型并看到 Credits 估算；failover 发生时界面明确说出实际用了谁。
- 每镜的图 / 视频有多候选、恰好一个当前版，切换不删旧候选。
- 单镜 MP4 可下载；批量下载走一个任务并给出预签名 URL。
- **界面上不存在"成片页 / 导出中心 / 时间线"任何入口。**
- 删除清单执行后 `npm run build` 通过，且 §5.4 的六个文件仍在。

### 14.2 CI 地板（决策记录 §8）

`.github/workflows/ci.yml` 原来**只有一个 `backend` job**
（ruff check + format → mypy → alembic up/down/up → pytest）。**2026-09-03 已加上 `web` job**
（Node 20，工作目录 `apps/web`，`npm ci → typecheck → build`）：

```yaml
frontend:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4      # 带 npm 缓存
    - run: npm ci
    - run: npm run typecheck           # tsc --noEmit
    - run: npm run build               # next build
```

- **不加 `lint`**：`apps/web` 下没有任何 ESLint 配置文件，加进去必然红。
  等 flat config 补好再进（FR-WEB-027；决策记录 §8 原话："`lint` 等 flat config 修好再进"）。
- **不把 E2E 放进地板**：Playwright 黄金路径进 Wave 1，但先作为独立 job，
  稳定之后再设为必须通过。
- CI 只当地板不当天花板，合并后仍由 Lead 手动验收；不通过打回同一个 Worker 返工
  （决策记录 §8）。

### 14.3 布局与可访问性

- 1280 / 1440 / 1920 三档不产生 body 横向滚动；1440 下主编辑区不小于 600px。
- 键盘可完成：新建项目 → 推进 → 确认门 → 触发出图。
- 正文对比 ≥7:1，其余 ≥4.5:1（`design-system/` 有对比度校验脚本）。
- 缩略图加载失败要写明原因（已删除 / 无权限 / 加载失败 / 可重试），不留黑块。
- 批量操作按钮写清作用域，例如「生成缺失 34 镜（预计 N Credits）」，
  并提供取消与失败续跑。
