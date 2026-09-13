"use client";

import type { ComponentType } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  Loader2,
} from "lucide-react";

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
  const { output, runs, approvals } = state;
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

  const cards = {
    story: {
      title: "故事结构",
      eyebrow: "Story source",
      description: String(output.plot_index?.logline ?? "等待情节目录产出"),
      href: `/freeflow/projects/${project.id}/story`,
      icon: StoryIcon,
      state: stateOf("plot_index"),
      metrics: [
        { label: "节点", value: storyNodes.length },
        { label: "规划场景", value: numberOf(output.plot_index?.scene_count) },
      ],
    },
    characters: {
      title: "角色",
      eyebrow: "Characters",
      description: characters.length > 0 ? "稳定角色实体与基准立绘" : "等待角色档案产出",
      href: `/freeflow/projects/${project.id}/characters`,
      icon: CharacterIcon,
      state: stateOf("characters"),
      metrics: [
        { label: "角色", value: characters.length },
        { label: "有图", value: characterRenders.completed },
        { label: "缺图", value: characterRenders.missing + characterRenders.failed },
      ],
    },
    scenes: {
      title: "场景",
      eyebrow: "Scenes",
      description: scenes.length > 0 ? "空间设定与一致性参照" : "等待场景档案产出",
      href: `/freeflow/projects/${project.id}/scenes`,
      icon: SceneIcon,
      state: stateOf("scenes"),
      metrics: [
        { label: "场景", value: scenes.length },
        { label: "有图", value: sceneRenders.completed },
        { label: "缺图", value: sceneRenders.missing + sceneRenders.failed },
      ],
    },
    screenplay: {
      title: "剧本",
      eyebrow: "Screenplay",
      description: String(output.screenplay?.synopsis ?? "等待剧本产出"),
      href: `/freeflow/projects/${project.id}/story`,
      icon: ScreenplayIcon,
      state: stateOf("screenplay"),
      metrics: [
        { label: "集", value: episodes.length },
        { label: "场", value: screenplayScenes },
      ],
    },
    storyboard: {
      title: "分镜",
      eyebrow: "Storyboard",
      description: shots.length > 0 ? "镜号、画面描述与实体引用" : "等待分镜产出",
      href: `/freeflow/projects/${project.id}/storyboard`,
      icon: StoryboardIcon,
      state: stateOf("storyboard"),
      metrics: [
        { label: "段/节点", value: storyboardNodes.length },
        { label: "镜头", value: shots.length },
        { label: "有图", value: shotRenders.completed },
      ],
    },
    renders: {
      title: "分镜图与基准图",
      eyebrow: "Image production",
      description: "当前真实生产终点；视频制作与整集合成尚未接入。",
      href:
        shotRenders.missing + shotRenders.failed > 0
          ? `/freeflow/projects/${project.id}/storyboard`
          : `/freeflow/projects/${project.id}/assets`,
      icon: RenderImageIcon,
      state: renderState,
      metrics: [
        { label: "就绪", value: allRenders.completed },
        { label: "处理中", value: allRenders.active },
        { label: "缺失/失败", value: allRenders.missing + allRenders.failed },
      ],
    },
  } satisfies Record<string, OverviewCardProps>;

  // 右栏（阶段 + 门、运行任务、一致性档案）现在由 `WorkbenchShell` 提供，
  // 这里只剩中间那一列。原来这个组件自带的「任务与审核检查器」已经删掉——
  // 一屏两条右栏，用户不知道该看哪条。
  return (
        <div className="mx-auto flex w-full max-w-[1360px] flex-col gap-5 p-4 lg:p-6 2xl:max-w-[1480px]">
          <section className="rf-panel-card flex flex-col gap-3 rounded-2xl border border-border p-4 sm:flex-row sm:items-end sm:justify-between lg:p-5">
            <div className="min-w-0">
              <div className="text-[10px] font-medium tracking-[0.18em] text-fg-subtle uppercase">
                Production overview
              </div>
              <h2 className="mt-1 text-2xl font-semibold tracking-tight text-fg">{project.title}</h2>
              <p className="mt-1 text-sm text-fg-subtle">
                {STAGE_LABEL[state.stage]}
                {project.route_type ? ` · ${project.route_type}` : " · 路线尚未判定"}
                {` · 创建于 ${new Date(project.created_at).toLocaleDateString("zh-CN")}`}
              </p>
              {/* projects.status 这一列后端从来没有推进过（决策记录 §11.5 裁决 1
                  列为 P1），所以阶段用推断值显示，这一列只作为附注。 */}
              <p className="mt-0.5 text-xs text-fg-subtle">
                项目记录状态：{PROJECT_STATUS[project.status] ?? project.status}
              </p>
            </div>
            <Link
              href={nextAction.href}
              className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-lg bg-primary px-3.5 text-sm font-medium text-primary-fg transition-colors duration-150 hover:bg-primary-hover"
            >
              {nextAction.label}
              <ArrowRight aria-hidden className="size-4" />
            </Link>
          </section>

          <AdvanceAction state={state} />

          {state.pendingGate && <GateActions state={state} gate={state.pendingGate} />}

          {(pendingApproval || stale.size > 0 || runCounts.failed > 0 || renders.error) && (
            <section aria-label="需要关注" className="grid gap-2 lg:grid-cols-3">
              {pendingApproval && (
                <AttentionCard
                  icon={GatePendingIcon}
                  tone="review"
                  title={`${GATE_LABEL[pendingApproval.gate as GateName] ?? "有一道门"}等待处理`}
                  detail={`要审的内容在${GATE_PAGE[pendingApproval.gate as GateName]?.label ?? "对应产出页"}，上面的审核门和那一页是同一个动作。确认本身不花钱，下一次「推进生产」才会调用模型。`}
                />
              )}
              {stale.size > 0 && (
                <AttentionCard
                  icon={StaleIcon}
                  tone="stale"
                  title={`${stale.size} 个阶段的上游已变`}
                  detail={`${Array.from(stale)
                    .map((role) => ROLE_LABEL[role as ReviseTarget] ?? role)
                    .join("、")}需要重跑或返工才会同步。`}
                />
              )}
              {(runCounts.failed > 0 || renders.error) && (
                <AttentionCard
                  icon={AlertTriangle}
                  tone="danger"
                  title={renders.error ? "出图状态读取失败" : `${runCounts.failed} 次运行失败`}
                  detail={renders.error ?? "打开生成队列，查看失败原因和运行详情。"}
                />
              )}
            </section>
          )}

          <section aria-labelledby="dependency-heading" className="rf-panel-card rounded-2xl border border-border p-4 lg:p-5">
            <div className="mb-4 flex items-end justify-between gap-3">
              <div>
                <h2 id="dependency-heading" className="mt-1 text-base font-semibold text-fg">
                  创作进度
                </h2>
              </div>
              <p className="hidden max-w-md text-right text-xs leading-5 text-fg-subtle lg:block">
                从故事到画面，查看每个阶段的产出与待办。
              </p>
            </div>

            <div className="mx-auto flex max-w-[1180px] flex-col items-stretch">
              <OverviewCard {...cards.story} />
              <FlowArrow label="并行收敛" />
              <div className="grid min-w-0 gap-3 lg:grid-cols-3">
                <OverviewCard {...cards.characters} />
                <OverviewCard {...cards.scenes} />
                <OverviewCard {...cards.screenplay} />
              </div>
              <FlowArrow label="合入分镜" />
              <OverviewCard {...cards.storyboard} />
              <FlowArrow label="按实体与镜号出图" />
              <OverviewCard {...cards.renders} />
            </div>
          </section>

          <section className="grid gap-3 md:grid-cols-3">
            <SummaryCard
              icon={StoryboardIcon}
              label="内容实体"
              value={characters.length + scenes.length + shots.length}
              detail={`${characters.length} 角色 · ${scenes.length} 场景 · ${shots.length} 镜头`}
            />
            <SummaryCard
              icon={RenderImageIcon}
              label="当前图像就绪"
              value={allRenders.completed}
              detail={`共 ${allRenders.expected} 个当前出图位 · ${renders.count} 条历史记录`}
            />
            <SummaryCard
              icon={QueueIcon}
              label="生成记录"
              value={runs.length}
              detail={`${runCounts.running} 进行中 · ${runCounts.failed} 失败 · ${runCounts.succeeded} 成功`}
            />
          </section>
        </div>
  );
}

type OverviewCardProps = {
  title: string;
  eyebrow: string;
  description: string;
  href: string;
  icon: ComponentType<{ className?: string }>;
  state: StageState;
  metrics: { label: string; value: number }[];
};

function OverviewCard({
  title,
  eyebrow,
  description,
  href,
  icon: Icon,
  state,
  metrics,
}: OverviewCardProps) {
  return (
    <Link
      href={href}
      className="rf-grid-card group min-w-0 rounded-xl border border-border p-4 transition-[color,background-color,border-color,box-shadow] duration-150 hover:border-border-strong hover:shadow-rf-card"
    >
      <div className="flex min-w-0 items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-fg-muted transition-colors duration-150 group-hover:border-primary/25 group-hover:bg-primary-soft group-hover:text-primary">
          <Icon aria-hidden className="size-4.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-medium tracking-[0.13em] text-fg-subtle uppercase">
            {eyebrow}
          </div>
          <div className="mt-0.5 flex min-w-0 items-center justify-between gap-2">
            <h3 className="truncate text-sm font-semibold text-fg">{title}</h3>
            <StageBadge state={state} />
          </div>
        </div>
      </div>
      <p className="mt-3 line-clamp-2 min-h-10 text-xs leading-5 text-fg-subtle">{description}</p>
      <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-2 border-t border-border pt-3">
        {metrics.map((metric) => (
          <div key={metric.label}>
            <dt className="text-[10px] text-fg-subtle">{metric.label}</dt>
            <dd className="tnum mt-0.5 text-base font-semibold text-fg">{metric.value}</dd>
          </div>
        ))}
      </dl>
    </Link>
  );
}

function StageBadge({ state }: { state: StageState }) {
  const spec = STAGE_SPEC[state];
  const Icon = spec.icon;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium whitespace-nowrap",
        spec.className,
      )}
    >
      <Icon aria-hidden className={cn("size-3", spec.spin && "animate-spin")} />
      {spec.label}
    </span>
  );
}

function FlowArrow({ label }: { label: string }) {
  return (
    <div aria-hidden className="flex h-11 items-center justify-center gap-2 text-[10px] text-fg-subtle">
      <span className="h-px w-8 bg-border" />
      <ArrowDown className="size-3.5 text-primary/65" />
      {label}
      <span className="h-px w-8 bg-border" />
    </div>
  );
}

function SummaryCard({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  value: number;
  detail: string;
}) {
  return (
    <div className="rf-grid-card flex items-center gap-3 rounded-xl border border-border p-4">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-fg-muted">
        <Icon aria-hidden className="size-4.5" />
      </div>
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-xs text-fg-subtle">{label}</span>
          <span className="tnum text-lg font-semibold text-fg">{value}</span>
        </div>
        <p className="truncate text-xs text-fg-subtle" title={detail}>
          {detail}
        </p>
      </div>
    </div>
  );
}

function AttentionCard({
  icon: Icon,
  tone,
  title,
  detail,
}: {
  icon: ComponentType<{ className?: string }>;
  tone: "review" | "stale" | "danger";
  title: string;
  detail: string;
}) {
  return (
    <div
      className={cn(
        "flex gap-3 rounded-xl border p-3",
        tone === "review" && "border-primary/25 bg-primary-soft",
        tone === "stale" && "border-rf-agent/25 bg-rf-agent-soft",
        tone === "danger" && "border-danger/25 bg-danger-soft",
      )}
    >
      <Icon
        aria-hidden
        className={cn(
          "mt-0.5 size-4 shrink-0",
          tone === "review" && "text-primary",
          tone === "stale" && "text-rf-agent",
          tone === "danger" && "text-danger",
        )}
      />
      <div className="min-w-0">
        <div className="text-xs font-semibold text-fg">{title}</div>
        <p className="mt-0.5 text-xs leading-5 text-fg-muted">{detail}</p>
      </div>
    </div>
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
  const base = `/freeflow/projects/${project.id}`;
  if (pendingApproval) {
    // 门开在**产出页**上：门① 要核对的情节目录、门③ 要核对的锚点卡都在那里，
    // 而它们的正文太长，塞不进概览页的一张卡。所以这里只负责把人送过去。
    const page = GATE_PAGE[pendingApproval.gate as GateName];
    return {
      href: `${base}${page?.path ?? "/overview"}`,
      label: page ? `去${page.label}处理` : "处理待确认的门",
      detail: "确认、打回和这道门要审的内容在同一页——那是唯一能边看边判断的地方。",
    };
  }
  if (runs.some((run) => run.status === "running" || run.status === "queued")) {
    return { href: `${base}/tasks`, label: "查看运行任务", detail: "项目有真实任务正在运行或排队。" };
  }
  const staleRole = (["plot_index", "screenplay", "characters", "scenes", "storyboard"] as const).find(
    (role) => stale.has(role),
  );
  if (staleRole) {
    const href =
      staleRole === "plot_index" || staleRole === "screenplay" ? `${base}/story` : `${base}/${staleRole}`;
    return {
      href,
      label: `检查${ROLE_LABEL[staleRole]}`,
      detail: "上游设定已经变化；先看旧产物，再决定重跑还是用一句话返工。",
    };
  }
  if (!output.plot_index || !output.screenplay) {
    return { href: `${base}/story`, label: "查看故事", detail: "故事结构或剧本尚未完成。" };
  }
  if (!output.characters) {
    return { href: `${base}/characters`, label: "查看角色", detail: "角色档案尚未产出。" };
  }
  if (!output.scenes) {
    return { href: `${base}/scenes`, label: "查看场景", detail: "场景档案尚未产出。" };
  }
  if (!output.storyboard) {
    return { href: `${base}/storyboard`, label: "查看分镜", detail: "分镜尚未产出。" };
  }
  if (renderSummary.completed < renderSummary.expected) {
    if (characterRenders.completed < characterRenders.expected) {
      return { href: `${base}/characters`, label: "补齐角色基准图", detail: "打开角色页查看缺失与失败的真实出图位。" };
    }
    if (sceneRenders.completed < sceneRenders.expected) {
      return { href: `${base}/scenes`, label: "补齐场景基准图", detail: "打开场景页查看缺失与失败的真实出图位。" };
    }
    if (shotRenders.completed < shotRenders.expected) {
      return { href: `${base}/storyboard`, label: "补齐分镜图", detail: "打开分镜页按镜号生成、查看进度或重试失败任务。" };
    }
  }
  return { href: `${base}/assets`, label: "查看项目素材", detail: "当前结构化产物与分镜图已就绪。" };
}
