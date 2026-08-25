import type { ReviseTarget } from "@/lib/api";

/**
 * 阶段与分组的单一定义处。
 *
 * 页面、进度条、折叠分组、修订目标 pill 全都读这里——各写一份，
 * 改一次阶段就得四处对齐，必然漏掉一处。
 *
 * 顺序与后端 `orchestrator._NEXT` 一致，但**这里不是权威**：
 * 权威在后端（ADR-008）。前端只是照着它渲染。
 */

export const STAGE_STEPS = [
  { key: "routing", label: "路线" },
  { key: "plot_index", label: "情节目录" },
  { key: "screenplay", label: "剧本" },
  { key: "await_setup", label: "确认剧本" },
  { key: "characters", label: "角色" },
  { key: "scenes", label: "场景" },
  { key: "storyboard", label: "分镜" },
  { key: "await_storyboard", label: "确认分镜" },
  { key: "done", label: "完成" },
] as const;

export type StageKey = (typeof STAGE_STEPS)[number]["key"];

export const TARGET_LABEL: Record<ReviseTarget, string> = {
  plot_index: "情节目录",
  screenplay: "剧本",
  characters: "角色",
  scenes: "场景",
  storyboard: "分镜",
};

/** 折叠分组。故事线的两步合成一组，其余一步一组。 */
export const GROUPS = [
  { key: "story", label: "故事", roles: ["plot_index", "screenplay"] },
  { key: "characters", label: "角色", roles: ["characters"] },
  { key: "scenes", label: "场景", roles: ["scenes"] },
  { key: "storyboard", label: "分镜", roles: ["storyboard"] },
] as const satisfies readonly { key: string; label: string; roles: readonly ReviseTarget[] }[];

export type GroupKey = (typeof GROUPS)[number]["key"] | "runs";

/** 当前阶段落在哪个分组里。 */
const GROUP_OF_STAGE: Record<StageKey, GroupKey> = {
  routing: "story",
  plot_index: "story",
  screenplay: "story",
  await_setup: "story",
  characters: "characters",
  scenes: "scenes",
  storyboard: "storyboard",
  await_storyboard: "storyboard",
  done: "storyboard",
};

/** 左栏「流程」上的某一步点下去该打开哪一组抽屉；没有产出的步骤返回 null。 */
export function groupOfStep(stage: StageKey): GroupKey | null {
  return stage === "routing" || stage === "done" ? null : GROUP_OF_STAGE[stage];
}

/**
 * 某一组在 URL hash 里的名字，形如 `#group-characters`。
 *
 * 外壳（左栏「流程」）和右栏都要能打开中栏的产出抽屉，但它们都在项目页
 * **上层或旁边**，回调传不过去。锚点是两边都能写、页面能听的公共通道。
 */
export function groupDomId(key: GroupKey): string {
  return `group-${key}`;
}
