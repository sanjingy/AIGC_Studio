# 2026-09-03 工作台重做计划：ReelFlow 视觉壳 × freeflow 真实接口

> 负责人 2026-09-03 指令：今天只做一件事——用 GPT 生成的 ReelFlow 原型（`C:\Users\92505\Desktop\AICG Studio ReelFlow`）取代现有工作台，接口和功能要做好。
> 本计划由 Lead 编写，是今天所有 Worker 任务书的共同依据。与 `DECISIONS_2026-09-02.md`、ADR-030 ~ 033 冲突时以那两份为准。

## 0. 对 ReelFlow 原型的判断

原型是一个 **视觉壳**：一页 `app/page.tsx`（170 行）+ shadcn 组件 + 两个假 API。九个导航项只有「镜头工作台」有真实布局，其余八个是占位英雄页；"宫格联合参考""导演建议""本地引擎已连接"都没有后端对应。

**取**：视觉语言（`#070a0f` 控制室暗色、青色状态光、紫色 Agent 标识、`rounded-2xl` 卡片、`border-white/8` 分割）、信息架构（可折叠图标侧栏 + 顶栏阶段轨 + 内容区 + 右侧上下文栏）、镜头卡片墙 + 选中镜头详情、"生成"弹窗的形态。
**不取**：单页 state 切视图（违反 FR-WEB-003 刷新保持）、假数据、宫格策略、导演建议文案、`vinext`/Cloudflare 构建链、Geist 字体（走现有字体栈）。

## 1. 目标（今天的完成定义）

新壳在 `/freeflow` 路由下上线，能在**不进旧壳**的前提下跑完：

```
登录 → 项目大厅 → 新建项目（小说/创意）→ 推进生产（advance）→ 剧本确认（approve / 返工 revise）
  → 角色/场景档案（出图、钉图）→ 分镜确认 → 逐镜出图（多候选，当前版取第一条，ADR-033 的 is_current 今天不做）
  → 任务队列（读 tasks + SSE）→ 素材库
```

达到即满足 ADR-030 前置条件；旧 `(app)` 除 `/login` 外删除、`/dashboard` 重定向，今天最后一步做。

## 2. 导航与页面映射（ReelFlow → 我们的模块）

| ReelFlow 导航 | 路由 | 数据来源（真实接口） | 今天做到 |
|---|---|---|---|
| 项目总览 | `/freeflow/projects/[id]/overview` | project、runs、approvals、tasks 计数 | 依赖图 + 下一步动作 + 「推进生产」按钮 |
| 故事大纲 | `.../story` | `current_state_json.plot_index` + `screenplay`，`revise` | 查看 + 自然语言返工；字段级 Patch 若 content 模块已提交则接 |
| 角色设定 | `.../characters` | `character_profiles`、images POST/PUT | 卡片墙 + 出图 / 用已有图 |
| 世界美术 | `.../scenes` | `scene_profiles`、images | 同上（道具不存在，不出现） |
| 分场剧本 | `.../screenplay` → 与 story 合并为一页的两个 tab | screenplay episodes | 按集导航 + **确认门（approve / reject）** |
| 镜头工作台 | `.../storyboard` | `storyboard.shots`、images/shots POST | 卡片墙 + 选中详情 + 单镜出图 + **确认门** + 「批量出图」弹窗（逐镜循环 POST） |
| 生成队列 | `.../tasks` | **`tasks` 列表 + SSE**（不是 agent_runs） | 状态、进度、错误、重试 |
| 资产库 | `.../assets` | assets library + 三段式上传 | 列表 + 上传 |
| 版本历史 | `.../history` | `content_revisions`（需 content 模块已提交） | 有则列表 + 撤销；否则**不出现在导航** |
| 系统设置 | `.../settings` | model_preference、删除项目 | 现有页套壳 |

右侧上下文栏（原"导演建议"）改为真实内容：当前阶段与门状态、运行中任务、一致性档案状态（风格档案存在 / 角色已出基准图 N/M）。**不写建议文案**。

## 3. 硬规则（所有 Worker）

1. **不做假入口**：没有后端的功能不出现按钮；必须出现时标"未接线"并禁用。宫格策略、视频生成、道具、成员一律不出现。
2. **路由即状态**：每个视图是独立路由，刷新不丢；只有抽屉/选中项用本地 state。
3. **只调 `lib/api.ts`**，页面里不写 fetch。
4. `tasks.status` 是执行状态唯一真相；任务页读 `tasks`。
5. 设计 token 进 `globals.css` 的 CSS 变量，组件里不写 raw hex（原型里的 `#070a0f` 等先收成变量）。
6. `typecheck + build` 必须过；未跑过的不算完成。
7. 不动后端代码（今天只有前端 + `lib/api.ts` 封装）。例外：content 模块的提交由专门任务处理。
8. 不 commit、不 push（Lead 验收后统一提交）。

## 4. 分工与顺序（这台机最多 2 个 Worker 并行）

| 阶段 | Worker | 模型 | 产出 | 文件归属 |
|---|---|---|---|---|
| A1 | WA（Codex Sol） | 视觉壳移植 | 侧栏 / 顶栏阶段轨 / 右栏容器 / 主题 token / 镜头卡片与详情 / 生成弹窗，**纯展示组件，props 驱动，无 fetch** | `components/freeflow/shell/**`、`components/freeflow/storyboard/**`、`app/globals.css` 主题段、`components/ui/*` 新增文件 |
| B1 | WB（Opus 5 high） | 数据层与闭环 | `lib/api.ts` 补齐封装、hooks、SSE；freeflow 各页接 advance / approve / revise / tasks / 上传 | `lib/**`、`app/freeflow/**/page.tsx`、`components/freeflow/project/**`（数据装配） |
| A2 | WC（Opus 5 high） | 把 WA 的代码重构成仓库规范 | 拆组件、去 raw hex、a11y、与 B1 的页面接线 | WA 的文件 + 页面接线处 |
| C | WD（Opus 5 high 或 Codex） | 删旧壳 + 收口 | 删 `(app)` 除 `/login`、`/dashboard` 重定向、删空壳页与 mock、typecheck/build、CI 前端地板 | `app/(app)/**`、`.github/workflows/**` |

A1 与 B1 并行（文件不相交，靠 §5 的组件契约对齐）；A2 在 A1 之后；C 在 A2、B1 之后。

## 5. 组件契约（A1 产出、B1 消费）

WA 必须按这些签名导出，WB 按这些签名调用；两边都不改签名，要改先问 Lead。

```ts
// components/freeflow/shell/workbench-shell.tsx
export type StageKey = 'story' | 'assets' | 'script' | 'storyboard' | 'generation';
export interface StageState { key: StageKey; label: string; state: 'locked' | 'ready' | 'approved' | 'active' | 'pending' }
export interface NavItem { id: string; label: string; href: string; icon: LucideIcon; badge?: string | number; disabled?: boolean; disabledReason?: string }
export function WorkbenchShell(props: {
  project: { id: string; title: string; subtitle?: string; savedAgo?: string };
  navigation: NavItem[]; utilityNavigation: NavItem[]; activeHref: string;
  stages: StageState[];
  primaryAction?: { label: string; onClick: () => void; disabled?: boolean; loading?: boolean };
  aside?: React.ReactNode;   // 右栏内容由页面提供
  children: React.ReactNode;
}): JSX.Element

// components/freeflow/shell/context-aside.tsx  —— 右栏的三个真实区块
export function AsideStageCard(props: { stage: StageState; gate?: { status: 'needs_review'|'approved'|'rejected'|'none'; onApprove?: () => void; onReject?: () => void; busy?: boolean } })
export function AsideTaskList(props: { tasks: Array<{ id: string; title: string; status: string; progress?: number; error?: string }>; onViewAll: () => void })
export function AsideConsistency(props: { items: Array<{ label: string; value: string; ok: boolean }> })

// components/freeflow/storyboard/shot-card.tsx
export interface ShotCardData { index: number; code: string; title: string; framing: string; camera: string; durationLabel?: string; status: 'ready'|'draft'|'rendering'|'failed'; imageUrl?: string }
export function ShotCard(props: { shot: ShotCardData; selected: boolean; onSelect: () => void })
export function ShotGrid(props: { shots: ShotCardData[]; selectedIndex: number | null; onSelect: (i: number) => void })
export function ShotDetail(props: { shot: ShotCardData & { description: string; characters: string[]; scene?: string; dialogue?: string }; actions: React.ReactNode })

// components/freeflow/storyboard/batch-render-dialog.tsx
export function BatchRenderDialog(props: { open: boolean; onOpenChange: (o: boolean) => void; shotCount: number; estimateCredits?: number; estimateRange?: { low: number; high: number }; onConfirm: () => Promise<void> })
// estimateCredits 传后端 POST /credits/estimate 的 estimated_credits；estimateRange 传 range_low/high；都没有就不显示估算行（2026-09-03 20:40 修订）
// 只有"逐镜出图"一种策略；没有宫格、没有视频。
```

## 6. 验收（Lead 逐条点）

- 新壳下从登录到分镜出图全程不出现 `/projects/{id}`（旧壳）链接。
- 审批 / 返工 / 推进 / 出图 / 上传 都写入后端，刷新后状态一致。
- 任务页显示 `tasks` 的状态与进度，SSE 推送生效。
- 导航里没有任何未实现入口；右栏没有虚构文案。
- `npm run typecheck && npm run build` 通过；1440 宽下侧栏 + 内容 + 右栏可同时看见。
- 旧 `(app)` 只剩 `/login`。

## 7. 今天不做

视频、TTS、is_current 候选切换、字段级 Patch 的编辑表格（除非 content 模块已提交且时间富余）、Playwright、道具、成员。
