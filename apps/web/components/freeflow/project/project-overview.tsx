"use client";

import type { ComponentType } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, Loader2 } from "lucide-react";

import {
  CharacterIcon,
  GateApprovedIcon,
  GatePendingIcon,
  MissingFrameIcon,
  QueueIcon,
  RenderImageIcon,
  SceneIcon,
  ScreenplayIcon,
  StaleIcon,
  StoryIcon,
  StoryboardIcon,
} from "@/components/icons/studio-icons";
import type { AgentRun, Approval, GateName, Project, ReviseTarget } from "@/lib/api";
import { GATE_LABEL, STAGE_LABEL, type ProjectState } from "@/lib/freeflow/use-project-state";
import type { Images as ImagesState, RenderSubject } from "@/lib/freeflow/use-images";
import { cn } from "@/lib/utils";

import { AdvanceAction, GateActions } from "./production-actions";

/**
 * 项目概览 = 制作板（production board），不是一墙卡片。
 *
 * 上一版是「六张同尺寸卡片 + 竖排 FlowArrow 流程图」。那个版式有两个毛病：
 * 六张卡片体量相同，等于宣布这六件事一样重要；而真正要紧的两句
 * ——「现在拍到哪」和「下一步做什么」——被压在其中一张卡片的角落里。
 *
 * 现在按读的顺序分成四块，体量刻意不等：
 *
 *   1. 通告条    当前阶段（全页最大的字）+ 下一动作（全页唯一的主按钮）
 *   2. 写库动作  推进生产 / 过门，位置不变，它们是真实的写操作
 *   3. 待办 + 进度尺   谁在等我 / 手上的画面拍了多少格
 *   4. 产出台账  六段生产的账式行，序号 01..06 就是真实的生产序列
 *
 * 并行的那三段（角色 / 场景 / 剧本）用序号槽左侧的一条竖线表示，
 * 这是原来三个 FlowArrow 说的同一件事，但占一列宽而不是三行高。
 */

type OutputSet = Record<ReviseTarget, any>;
type StageState = "missing" | "ready" | "partial" | "blocked" | "running" | "review" | "stale";

const AGENT_ID: Record<ReviseTarget, string> = {
  plot_index: "story.plot_index.v1",
  screenplay: "story.screenplay.v1",
  characters: "visual.character.v1",
  scenes: "visual.scene.v1",
  storyboard: "visual.storyboard.v1",
};

const ROLE_LABEL: Record<ReviseTarget, string> = {
  plot_index: "故事结构",
  screenplay: "剧本",
  characters: "角色",
  scenes: "场景",
  storyboard: "分镜",
};

/**
 * 每道门要审的东西在哪一页（ADR-037 四道门）。
 *
 * **一张表，不是三元式。** 上一版这里写的是
 * `gate === "setup" ? story : storyboard`，两道门时碰巧对，加到四道门时
 * 门① 和门③ 全落进了分镜页——用户点「去处理」会到一个跟他要确认的东西
 * 毫无关系的页面上。`Record<GateName, ...>` 少一个键 TypeScript 直接报错。
 */
const GATE_PAGE: Record<GateName, { path: string; label: string }> = {
  plan: { path: "/story", label: "故事页" },
  setup: { path: "/story", label: "故事页" },
  anchors: { path: "/scenes", label: "世界美术页" },
  storyboard: { path: "/storyboard", label: "镜头工作台" },
};

const PROJECT_STATUS: Record<string, string> = {
  draft: "草稿",
  routing: "路线判断",
  producing: "生产中",
  review: "待确认",
  completed: "已完成",
  archived: "已归档",
};

const STAGE_SPEC: Record<
  StageState,
  { label: string; icon: ComponentType<{ className?: string }>; className: string; spin?: boolean }
> = {
  missing: {
    label: "尚未产出",
    icon: MissingFrameIcon,
    className: "border-border bg-surface-2 text-fg-subtle",
  },
  ready: {
    label: "已就绪",
    icon: GateApprovedIcon,
    className: "border-success/25 bg-success-soft text-success",
  },
  partial: {
    label: "部分完成",
    icon: QueueIcon,
    className: "border-running/25 bg-running-soft text-running",
  },
  blocked: {
    label: "需要处理",
    icon: AlertTriangle,
    className: "border-danger/25 bg-danger-soft text-danger",
  },
  running: {
    label: "生成中",
    icon: Loader2,
    className: "border-running/25 bg-running-soft text-running",
    spin: true,
  },
  review: {
    label: "待确认",
    icon: GatePendingIcon,
    className: "border-primary/25 bg-primary-soft text-primary",
  },
  stale: {
    label: "上游已变",
    icon: StaleIcon,
    className: "border-rf-agent/25 bg-rf-agent-soft text-rf-agent",
  },
};

type RenderSummary = {
  expected: number;
  completed: number;
  active: number;
  failed: number;
  missing: number;
};

type NextAction = { href: string; label: string; detail: string };

/** 台账里的一行。`branch` = 与相邻行并行，靠序号槽左侧的竖线表示。 */
type StageRow = {
  no: string;
  title: string;
  description: string;
  href: string;
  icon: ComponentType<{ className?: string }>;
  state: StageState;
  facts: { label: string; value: number }[];
  branch?: boolean;
};

type Todo = {
  key: string;
  flag: "review" | "attention" | "danger";
  icon: ComponentType<{ className?: string }>;
  title: string;
  detail: string;
  href: string;
  action: string;
  spin?: boolean;
};

export function ProjectOverview({
  state,
  renders,
  project,
}: {
  state: ProjectState;
  renders: ImagesState;
  /** 已确定非空的项目——外层页面在 loading 结束后才渲染这个组件 */
  project: Project;
}) {
  const { output, runs } = state;
  const characters = arrayOf(output.characters?.characters);
  const scenes = arrayOf(output.scenes?.scenes);
  const episodes = arrayOf(output.screenplay?.episodes);
  const screenplayScenes = episodes.reduce(
    (total, episode: any) => total + arrayOf(episode?.scenes).length,
    0,
  );
  const storyNodes = arrayOf(output.plot_index?.nodes);
  const storyboardNodes = arrayOf(output.storyboard?.nodes);
  const shots = arrayOf(output.storyboard?.shots);

  const characterRenders = summarizeRenders(
    characters,
    (character) =>
      typeof character?.ref === "string" ? { kind: "character", ref: character.ref } : null,
    renders,
  );
  const sceneRenders = summarizeRenders(
    scenes,
    (scene) => (typeof scene?.ref === "string" ? { kind: "scene", ref: scene.ref } : null),
    renders,
  );
  const shotRenders = summarizeRenders(
    shots,
    (shot) =>
      Number.isFinite(Number(shot?.index)) ? { kind: "shot", index: Number(shot.index) } : null,
    renders,
  );
  const allRenders = mergeRenderSummaries(characterRenders, sceneRenders, shotRenders);

  const pendingApproval = state.pendingApproval;
  const stale = new Set(project.stale_roles ?? []);

  const stateOf = (role: ReviseTarget): StageState => {
    const latest = runs.find((run) => run.agent_id === AGENT_ID[role]);
    const hasOutput = Boolean(output[role]);
    if (latest?.status === "running") return "running";
    if (
      pendingApproval &&
      ((pendingApproval.gate === "setup" && role === "screenplay") ||
        (pendingApproval.gate !== "setup" && role === "storyboard"))
    ) {
      return "review";
    }
    if (hasOutput && stale.has(role)) return "stale";
    if (hasOutput) return "ready";
    if (latest?.status === "failed") return "blocked";
    return "missing";
  };

  const renderState: StageState =
    allRenders.active > 0
      ? "running"
      : allRenders.expected === 0
        ? "missing"
        : allRenders.completed === allRenders.expected
          ? "ready"
          : allRenders.failed > 0 && allRenders.completed === 0
            ? "blocked"
            : allRenders.completed > 0 || allRenders.failed > 0
              ? "partial"
              : "missing";

  const nextAction = chooseNextAction({
    project,
    output,
    runs,
    pendingApproval,
    stale,
    renderSummary: allRenders,
    characterRenders,
    sceneRenders,
    shotRenders,
  });

  const runCounts = countStatuses(runs.map((run) => run.status));
  const base = "/freeflow/projects/" + project.id;

  const rows: StageRow[] = [
    {
      no: "01",
      title: "故事结构",
      description: String(output.plot_index?.logline ?? "等待情节目录产出"),
      href: base + "/story",
      icon: StoryIcon,
      state: stateOf("plot_index"),
      facts: [
        { label: "节点", value: storyNodes.length },
        { label: "规划场景", value: numberOf(output.plot_index?.scene_count) },
      ],
    },
    {
      no: "02",
      title: "角色",
      description: characters.length > 0 ? "稳定角色实体与基准立绘" : "等待角色档案产出",
      href: base + "/characters",
      icon: CharacterIcon,
      state: stateOf("characters"),
      branch: true,
      facts: [
        { label: "角色", value: characters.length },
        { label: "有图", value: characterRenders.completed },
      ],
    },
    {
      no: "03",
      title: "场景",
      description: scenes.length > 0 ? "空间设定与一致性参照" : "等待场景档案产出",
      href: base + "/scenes",
      icon: SceneIcon,
      state: stateOf("scenes"),
      branch: true,
      facts: [
        { label: "场景", value: scenes.length },
        { label: "有图", value: sceneRenders.completed },
      ],
    },
    {
      no: "04",
      title: "剧本",
      description: String(output.screenplay?.synopsis ?? "等待剧本产出"),
      href: base + "/story",
      icon: ScreenplayIcon,
      state: stateOf("screenplay"),
      branch: true,
      facts: [
        { label: "集", value: episodes.length },
        { label: "场", value: screenplayScenes },
      ],
    },
    {
      no: "05",
      title: "分镜",
      description: shots.length > 0 ? "镜号、画面描述与实体引用" : "等待分镜产出",
      href: base + "/storyboard",
      icon: StoryboardIcon,
      state: stateOf("storyboard"),
      facts: [
        { label: "段", value: storyboardNodes.length },
        { label: "镜头", value: shots.length },
      ],
    },
    {
      no: "06",
      title: "分镜图与基准图",
      description: "当前真实生产终点；视频与整集合成尚未接入",
      href: shotRenders.missing + shotRenders.failed > 0 ? base + "/storyboard" : base + "/assets",
      icon: RenderImageIcon,
      state: renderState,
      facts: [
        { label: "就绪", value: allRenders.completed },
        { label: "缺失", value: allRenders.missing + allRenders.failed },
      ],
    },
  ];

  const gatePage = pendingApproval
    ? GATE_PAGE[pendingApproval.gate as GateName]
    : undefined;

  const todos: Todo[] = [];
  if (pendingApproval) {
    todos.push({
      key: "gate",
      flag: "review",
      icon: GatePendingIcon,
      title: (GATE_LABEL[pendingApproval.gate as GateName] ?? "有一道门") + "等待处理",
      detail:
        "要审的内容在" +
        (gatePage?.label ?? "对应产出页") +
        "。确认本身不花钱，下一次「推进生产」才会调用模型。",
      href: base + (gatePage?.path ?? "/overview"),
      action: "去处理",
    });
  }
  if (stale.size > 0) {
    todos.push({
      key: "stale",
      flag: "attention",
      icon: StaleIcon,
      title: stale.size + " 个阶段的上游已变",
      detail:
        Array.from(stale)
          .map((role) => ROLE_LABEL[role as ReviseTarget] ?? role)
          .join("、") + "需要重跑或返工才会同步。",
      href: base + "/story",
      action: "去检查",
    });
  }
  if (runCounts.failed > 0 || renders.error) {
    todos.push({
      key: "failed",
      flag: "danger",
      icon: AlertTriangle,
      title: renders.error ? "出图状态读取失败" : runCounts.failed + " 次运行失败",
      detail: renders.error ?? "打开生成队列，查看失败原因和运行详情。",
      href: base + "/tasks",
      action: "看队列",
    });
  }
  if (allRenders.active > 0) {
    todos.push({
      key: "running",
      flag: "attention",
      icon: Loader2,
      title: allRenders.active + " 张画面正在生成",
      detail: "生成中的任务不占你的时间，完成后会出现在对应页面上。",
      href: base + "/tasks",
      action: "看队列",
      spin: true,
    });
  }

  const meters = [
    { label: "角色基准图", done: characterRenders.completed, total: characterRenders.expected },
    { label: "场景参考图", done: sceneRenders.completed, total: sceneRenders.expected },
    { label: "分镜图", done: shotRenders.completed, total: shotRenders.expected },
  ];

  // 右栏（阶段 + 门、运行任务、一致性档案）由 `WorkbenchShell` 提供，
  // 这里只负责中间那一列。
  return (
    <div className="ff-board">
      {/* 1. 通告条：现在拍到哪 + 下一步做什么 */}
      <section aria-label="制作通告" className="ff-board-call">
        <div className="ff-board-now">
          <span className="ff-board-now-key">现在拍到</span>
          <h1 className="ff-board-now-value">{STAGE_LABEL[state.stage]}</h1>
          {/* 两个真实字段，不是 `A · B · C` 的串：中间点是分隔符，
              「字段名 / 值」才是结构，扫一眼就知道哪个词是哪一栏。
              项目名和路线在上方场记板里已经有了，这里不再抄一遍，
              只留同一屏上别处没有的两件事。 */}
          <div className="ff-slate mt-0.5">
            <div className="ff-slate-field">
              <span className="ff-slate-key">路线</span>
              <span className="ff-slate-value" data-numeric={project.route_type ? "true" : undefined}>
                {project.route_type ?? "尚未判定"}
              </span>
            </div>
            <div className="ff-slate-field">
              <span className="ff-slate-key">记录状态</span>
              <span className="ff-slate-value">
                {PROJECT_STATUS[project.status] ?? project.status}
              </span>
            </div>
          </div>
        </div>
        <div className="ff-board-next">
          <span className="ff-board-next-key">下一动作</span>
          <Link
            href={nextAction.href}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-[2px] bg-primary px-5 text-sm font-semibold text-primary-fg transition-colors duration-150 hover:bg-primary-hover"
          >
            {nextAction.label}
            <ArrowRight aria-hidden className="size-4" />
          </Link>
          <p className="ff-board-next-detail">{nextAction.detail}</p>
        </div>
      </section>

      {/* 2. 真实写库动作。位置不动——它们是这一页唯一会花钱的按钮。 */}
      <AdvanceAction state={state} />
      {state.pendingGate && <GateActions state={state} gate={state.pendingGate} />}

      {/* 3. 待办（谁在等我）+ 制作进度（拍了多少格） */}
      <div className="ff-board-split">
        <section aria-label="需要你处理" className="ff-ledger">
          <div className="ff-ledger-head">
            <h2>需要你处理</h2>
            {todos.length > 0 && <span className="code">{todos.length}</span>}
          </div>
          {todos.length === 0 ? (
            <p className="ff-ledger-empty">
              没有等你确认的门，也没有失败的运行。按上面的下一动作继续就行。
            </p>
          ) : (
            todos.map((todo) => (
              <div key={todo.key} className="ff-ledger-row" data-flag={todo.flag}>
                <todo.icon
                  aria-hidden
                  className={cn(
                    "mt-0.5 size-4 shrink-0 self-start",
                    todo.flag === "review" && "text-primary",
                    todo.flag === "attention" && "text-running",
                    todo.flag === "danger" && "text-danger",
                    todo.spin && "animate-spin",
                  )}
                />
                <div className="ff-ledger-val">
                  <div className="text-sm font-medium text-fg">{todo.title}</div>
                  <p className="mt-0.5 text-xs leading-5 text-fg-muted">{todo.detail}</p>
                </div>
                <Link
                  href={todo.href}
                  className="shrink-0 self-start text-xs font-medium text-primary hover:underline"
                >
                  {todo.action}
                </Link>
              </div>
            ))
          )}
        </section>

        <section aria-label="制作进度" className="ff-ledger">
          <div className="ff-ledger-head">
            <h2>制作进度</h2>
            <span className="code">
              {allRenders.completed}/{allRenders.expected}
            </span>
          </div>
          {meters.map((meter) => (
            <div key={meter.label} className="ff-ledger-row">
              <span className="ff-ledger-key">{meter.label}</span>
              <span className="ff-ledger-val">
                <Meter done={meter.done} total={meter.total} />
              </span>
              <span className="ff-ledger-num">
                {meter.total === 0 ? "—" : meter.done + "/" + meter.total}
              </span>
            </div>
          ))}
          <p className="ff-ledger-note border-b-0">
            这里数的是「当前这一版有没有图」，不是历史次数。已经跑过
            <span className="code"> {renders.count} </span>
            次出图，留下
            <span className="code"> {runs.length} </span>
            条文本生成记录。
          </p>
        </section>
      </div>

      {/* 4. 产出台账：六段生产，一段一行 */}
      <section aria-labelledby="ledger-heading" className="ff-ledger">
        <div className="ff-ledger-head">
          <h2 id="ledger-heading">产出台账</h2>
          <span className="font-normal">02–04 三段并行，合入分镜后按实体与镜号出图</span>
        </div>
        {rows.map((row) => (
          <Link
            key={row.no}
            href={row.href}
            className="ff-ledger-row ff-stage-row"
            data-branch={row.branch ? "true" : undefined}
          >
            <span className="ff-stage-no">{row.no}</span>
            <row.icon aria-hidden className="size-4 shrink-0 text-fg-subtle" />
            <span className="ff-ledger-val">
              <span className="ff-stage-title">{row.title}</span>
              <span className="ff-stage-desc" title={row.description}>
                {row.description}
              </span>
            </span>
            <span className="ff-stage-facts hidden sm:flex">
              {row.facts.map((fact) => (
                <span key={fact.label}>
                  <b>{fact.value}</b> {fact.label}
                </span>
              ))}
            </span>
            <StageBadge state={row.state} />
          </Link>
        ))}
      </section>
    </div>
  );
}

/**
 * 进度尺。`total === 0` 时不画空槽——空槽会把「这个项目没有这一类」
 * 显示成「一张都还没做」，那是两件事。
 */
function Meter({ done, total }: { done: number; total: number }) {
  if (total === 0) {
    return <span className="text-xs text-fg-subtle">这个项目还没有这一类</span>;
  }
  const percent = Math.round((done / total) * 100);
  return (
    <span
      className="ff-meter"
      data-tone={done >= total ? "done" : "running"}
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span style={{ width: Math.max(percent, done > 0 ? 3 : 0) + "%" }} />
    </span>
  );
}

function StageBadge({ state }: { state: StageState }) {
  const spec = STAGE_SPEC[state];
  const Icon = spec.icon;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-[2px] border px-2 py-0.5 text-[10px] font-medium whitespace-nowrap",
        spec.className,
      )}
    >
      <Icon aria-hidden className={cn("size-3", spec.spin && "animate-spin")} />
      {spec.label}
    </span>
  );
}

function arrayOf(value: unknown): any[] {
  return Array.isArray(value) ? value : [];
}

function numberOf(value: unknown): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function summarizeRenders(
  items: any[],
  subjectOf: (item: any) => RenderSubject | null,
  renders: ImagesState,
): RenderSummary {
  let completed = 0;
  let active = 0;
  let failed = 0;

  for (const item of items) {
    const subject = subjectOf(item);
    const view = subject ? renders.renderOf(subject) : null;
    if (view?.assetId) completed += 1;
    else if (view?.status === "running" || view?.status === "queued") active += 1;
    else if (view?.status === "failed") failed += 1;
  }

  return {
    expected: items.length,
    completed,
    active,
    failed,
    missing: Math.max(0, items.length - completed - active - failed),
  };
}

function mergeRenderSummaries(...summaries: RenderSummary[]): RenderSummary {
  return summaries.reduce(
    (total, summary) => ({
      expected: total.expected + summary.expected,
      completed: total.completed + summary.completed,
      active: total.active + summary.active,
      failed: total.failed + summary.failed,
      missing: total.missing + summary.missing,
    }),
    { expected: 0, completed: 0, active: 0, failed: 0, missing: 0 },
  );
}

function countStatuses(statuses: string[]) {
  return {
    running: statuses.filter((status) => status === "running").length,
    waiting: statuses.filter((status) => status === "queued" || status === "pending").length,
    succeeded: statuses.filter((status) => status === "succeeded").length,
    failed: statuses.filter((status) => status === "failed" || status === "cancelled").length,
    pending: statuses.filter((status) => status === "pending").length,
    approved: statuses.filter((status) => status === "approved").length,
    changes_requested: statuses.filter((status) => status === "changes_requested").length,
    rejected: statuses.filter((status) => status === "rejected").length,
  };
}

function chooseNextAction({
  project,
  output,
  runs,
  pendingApproval,
  stale,
  renderSummary,
  characterRenders,
  sceneRenders,
  shotRenders,
}: {
  project: Project;
  output: OutputSet;
  runs: AgentRun[];
  pendingApproval: Approval | null;
  stale: Set<ReviseTarget>;
  renderSummary: RenderSummary;
  characterRenders: RenderSummary;
  sceneRenders: RenderSummary;
  shotRenders: RenderSummary;
}): NextAction {
  const base = "/freeflow/projects/" + project.id;
  if (pendingApproval) {
    // 门开在**产出页**上：门① 要核对的情节目录、门③ 要核对的锚点卡都在那里，
    // 而它们的正文太长，塞不进概览页的一行。所以这里只负责把人送过去。
    const page = GATE_PAGE[pendingApproval.gate as GateName];
    return {
      href: base + (page?.path ?? "/overview"),
      label: page ? "去" + page.label + "处理" : "处理待确认的门",
      detail: "确认、打回和这道门要审的内容在同一页——那是唯一能边看边判断的地方。",
    };
  }
  if (runs.some((run) => run.status === "running" || run.status === "queued")) {
    return {
      href: base + "/tasks",
      label: "查看运行任务",
      detail: "项目有真实任务正在运行或排队。",
    };
  }
  const staleRole = (
    ["plot_index", "screenplay", "characters", "scenes", "storyboard"] as const
  ).find((role) => stale.has(role));
  if (staleRole) {
    const href =
      staleRole === "plot_index" || staleRole === "screenplay"
        ? base + "/story"
        : base + "/" + staleRole;
    return {
      href,
      label: "检查" + ROLE_LABEL[staleRole],
      detail: "上游设定已经变化；先看旧产物，再决定重跑还是用一句话返工。",
    };
  }
  if (!output.plot_index || !output.screenplay) {
    return { href: base + "/story", label: "查看故事", detail: "故事结构或剧本尚未完成。" };
  }
  if (!output.characters) {
    return { href: base + "/characters", label: "查看角色", detail: "角色档案尚未产出。" };
  }
  if (!output.scenes) {
    return { href: base + "/scenes", label: "查看场景", detail: "场景档案尚未产出。" };
  }
  if (!output.storyboard) {
    return { href: base + "/storyboard", label: "查看分镜", detail: "分镜尚未产出。" };
  }
  if (renderSummary.completed < renderSummary.expected) {
    if (characterRenders.completed < characterRenders.expected) {
      return {
        href: base + "/characters",
        label: "补齐角色基准图",
        detail: "打开角色页查看缺失与失败的真实出图位。",
      };
    }
    if (sceneRenders.completed < sceneRenders.expected) {
      return {
        href: base + "/scenes",
        label: "补齐场景基准图",
        detail: "打开场景页查看缺失与失败的真实出图位。",
      };
    }
    if (shotRenders.completed < shotRenders.expected) {
      return {
        href: base + "/storyboard",
        label: "补齐分镜图",
        detail: "打开分镜页按镜号生成、查看进度或重试失败任务。",
      };
    }
  }
  return {
    href: base + "/assets",
    label: "查看项目素材",
    detail: "当前结构化产物与分镜图已就绪。",
  };
}
