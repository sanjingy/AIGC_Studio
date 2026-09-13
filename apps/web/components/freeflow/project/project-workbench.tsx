"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import {
  AssetLibraryIcon,
  CharacterIcon,
  ProjectOverviewIcon,
  QueueIcon,
  SceneIcon,
  SettingsIcon,
  StoryIcon,
  StoryboardIcon,
} from "@/components/icons/studio-icons";

import {
  AsideConsistency,
  AsideStageCard,
  AsideTaskList,
} from "@/components/freeflow/shell/context-aside";
import {
  WorkbenchShell,
  type NavItem,
  type StageState,
} from "@/components/freeflow/shell/workbench-shell";
import type { Images, RenderSubject } from "@/lib/freeflow/use-images";
import type { ProjectState } from "@/lib/freeflow/use-project-state";
import { buildStages, gateStatusOf, segmentOf } from "@/lib/freeflow/stage-map";
import { taskTitle, type TasksState } from "@/lib/freeflow/use-tasks";

/**
 * 项目工作台的外壳装配。**页面只管中间那块内容**，导航、阶段条、右栏
 * 三块都在这里拼——每页各拼一份，改一条导航就要改八个文件。
 *
 * 数据全部由页面通过 hook 传进来（`useProjectState` / `useTasks` /
 * `useImages`），这一层自己不发请求：它是装配，不是数据源。
 */

/** 制作流程。顺序就是生产顺序，和阶段条对得上。 */
function navigationOf(base: string): NavItem[] {
  return [
    { id: "overview", label: "项目总览", href: `${base}/overview`, icon: ProjectOverviewIcon },
    { id: "story", label: "故事与剧本", href: `${base}/story`, icon: StoryIcon },
    { id: "characters", label: "角色设定", href: `${base}/characters`, icon: CharacterIcon },
    { id: "scenes", label: "世界美术", href: `${base}/scenes`, icon: SceneIcon },
    { id: "storyboard", label: "镜头工作台", href: `${base}/storyboard`, icon: StoryboardIcon },
  ];
}

/**
 * 资产与运行。
 *
 * **没有「版本历史」**：`GET /projects/{id}/revisions`（ADR-029）现在接上了，
 * 但入口在分镜页顶栏的「改动记录」抽屉里，不在这条导航上。那份历史是
 * 按 role 过滤的，回答的是"我刚才把这一镜改成什么了"——它属于正在编辑的
 * 那份产出，不是一个独立的目的地。给它一个顶级位置，用户得先离开正在改的
 * 东西才能看它改了什么。其余四个 role 的字段编辑还没做（FR-WEB-004 剩下的
 * 一半），真做全了再考虑要不要一个跨 role 的总历史页。
 *
 * 也没有「节点画布」：ADR-030 第 5 条，路由保留、从导航隐藏。
 */
function utilityNavigationOf(base: string, runningTasks: number): NavItem[] {
  return [
    {
      id: "tasks",
      label: "生成记录",
      href: `${base}/tasks`,
      icon: QueueIcon,
      // 0 不显示徽标：一个写着 0 的红点只是噪音
      badge: runningTasks > 0 ? runningTasks : undefined,
    },
    { id: "assets", label: "资产库", href: `${base}/assets`, icon: AssetLibraryIcon },
    { id: "settings", label: "项目设置", href: `${base}/settings`, icon: SettingsIcon },
  ];
}

type Summary = { done: number; total: number };

function summarize(refs: RenderSubject[], images: Images): Summary {
  let done = 0;
  for (const subject of refs) {
    if (images.renderOf(subject)?.assetId) done += 1;
  }
  return { done, total: refs.length };
}

function arrayOf(value: unknown): any[] {
  return Array.isArray(value) ? value : [];
}

/**
 * 顶栏那颗主按钮的**唯一**构造处。
 *
 * 只在 `advance` 真的能跑的时候才返回一个动作：门开着要先处理门，
 * 还没给原始素材要先在正文的 `AdvanceAction` 里填，文本链路走完了就没有
 * 下一步。这三种情况下画一颗点了没用的按钮，等于假入口。
 */
export function advancePrimaryAction(state: ProjectState) {
  if (state.pendingGate || state.needsSource || state.stage === "done") return undefined;
  return {
    label: "推进生产",
    onClick: () => void state.advance(),
    loading: state.busy === "advance",
  };
}

export function ProjectWorkbench({
  projectId,
  state,
  tasks,
  images,
  activeHref,
  primaryAction,
  children,
}: {
  /**
   * 路由参数里的项目 id，**不是从 `state.project` 上取**：项目是异步来的，
   * 加载那一两秒里 `state.project` 还是 null，导航会全部指向
   * `/freeflow/projects//overview`——点一下就是 404。
   */
  projectId: string;
  state: ProjectState;
  tasks: TasksState;
  images: Images;
  /** 当前路由，用来在侧栏里高亮。传完整 pathname。 */
  activeHref: string;
  /** 顶栏右上角那颗主按钮。不传就不画——没有动作的页面不该有一颗按钮。 */
  primaryAction?: { label: string; onClick: () => void; disabled?: boolean; loading?: boolean };
  children: React.ReactNode;
}) {
  const router = useRouter();
  const project = state.project;
  const base = `/freeflow/projects/${projectId}`;

  const running = tasks.items.filter((t) => t.status === "queued" || t.status === "running");

  const stages: StageState[] = useMemo(
    () =>
      buildStages({
        stage: state.stage,
        approvals: state.approvals,
        output: state.output,
        pendingGate: state.pendingGate,
      }),
    [state.stage, state.approvals, state.output, state.pendingGate],
  );

  const activeStage =
    stages.find((s) => s.key === segmentOf(state.stage)) ?? stages[0] ?? null;

  /**
   * 一致性档案的就绪情况。三行都是**真实计数**：角色/场景档案里有几个 ref，
   * 其中几个已经有基准图；分镜有几个镜号，其中几个出过首帧图。
   *
   * 没有「风格档案是否存在」这一行——`consistency_style_profiles` 没有任何
   * 读接口，写不出真值。编一行"风格已锁定"就是假的。
   */
  const consistency = useMemo(() => {
    const characters = arrayOf(state.output.characters?.characters)
      .filter((c) => typeof c?.ref === "string")
      .map((c): RenderSubject => ({ kind: "character", ref: c.ref }));
    const scenes = arrayOf(state.output.scenes?.scenes)
      .filter((s) => typeof s?.ref === "string")
      .map((s): RenderSubject => ({ kind: "scene", ref: s.ref }));
    const shots = arrayOf(state.output.storyboard?.shots)
      .filter((s) => Number.isFinite(Number(s?.index)))
      .map((s): RenderSubject => ({ kind: "shot", index: Number(s.index) }));

    const rows: { label: string; value: string; ok: boolean }[] = [];
    const push = (label: string, summary: Summary) => {
      if (summary.total === 0) return; // 那一步还没产出，不占一行
      rows.push({
        label,
        value: `${summary.done} / ${summary.total} 已出图`,
        ok: summary.done === summary.total,
      });
    };
    push("角色基准立绘", summarize(characters, images));
    push("场景参考图", summarize(scenes, images));
    push("分镜首帧图", summarize(shots, images));
    return rows;
  }, [state.output, images]);

  const gateStatus = gateStatusOf(state.stage, state.approvals);

  const aside = (
    <div className="flex flex-col gap-4">
      {activeStage && (
        <AsideStageCard
          stage={activeStage}
          gate={{
            status: gateStatus,
            // 回调只在门真的开着时给：组件按"有没有回调"决定画不画按钮，
            // 无条件传下去会在没有门的时候也画出一颗点了没反应的「通过」
            onApprove: state.pendingGate ? () => void state.approve() : undefined,
            onReject: state.pendingGate ? () => void state.reject() : undefined,
            busy: state.busy === "approve" || state.busy === "reject",
          }}
        />
      )}

      <AsideTaskList
        tasks={running.slice(0, 5).map((t) => ({
          id: t.id,
          title: taskTitle(t.type),
          status: t.status,
          progress: t.progress,
          error: t.error_code ?? undefined,
        }))}
        onViewAll={() => router.push(`${base}/tasks`)}
      />

      <AsideConsistency items={consistency} />
    </div>
  );

  return (
    <WorkbenchShell
      project={{
        id: projectId,
        title: project?.title ?? "…",
        subtitle: project?.route_type ?? undefined,
        savedAgo: state.snapshot
          ? `状态更新于 ${new Date(state.snapshot.updated_at).toLocaleString("zh-CN")}`
          : undefined,
      }}
      navigation={navigationOf(base)}
      utilityNavigation={utilityNavigationOf(base, running.length)}
      activeHref={activeHref}
      stages={stages}
      primaryAction={primaryAction}
      aside={aside}
    >
      {children}
    </WorkbenchShell>
  );
}
