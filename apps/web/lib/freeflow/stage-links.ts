/**
 * 顶栏五段胶片条 ←→ 页面落点。**纯函数，不依赖 React / Next / 路径别名**，
 * 这样 `node --test` 能直接测（`stage-links.test.ts`）。
 *
 * 两件事分开：
 * - `stageHref`：点某一段去哪看它的内容。五段都有落点，包括还没走到、被门锁住的段
 *   ——点击只是**查看**，不推进、不审批、不改 `state.stage`，生产状态仍由后端决定。
 * - `viewingStage`：当前**正在看**的是哪一段。它和"生产走到哪一段"（active /
 *   ready / locked）是两种信息，不能混：在看剧本不等于剧本是当前生产阶段。
 */

export type StageLinkKey = "story" | "script" | "assets" | "storyboard" | "generation";

/** 故事页里两块内容的锚点 id。`story-workspace.tsx` 的 section 上用的是同一组值。 */
export const STORY_ANCHORS = { story: "plot-index", script: "screenplay" } as const;

export function stageHref(key: StageLinkKey, base: string): string {
  switch (key) {
    case "story":
      return `${base}/story#${STORY_ANCHORS.story}`;
    case "script":
      return `${base}/story#${STORY_ANCHORS.script}`;
    case "assets":
      return `${base}/characters`;
    case "storyboard":
      return `${base}/storyboard`;
    case "generation":
      // 出图、生成记录与运行队列都在这一页；生成段没有别的更具体的落点
      return `${base}/tasks`;
  }
}

/**
 * 当前路由对应哪一段。概览、资产库、设置不属于任何一段，返回 null。
 * `hash` 带不带 `#` 都行。
 */
export function viewingStage(pathname: string, hash: string): StageLinkKey | null {
  const path = pathname.replace(/\/+$/, "");
  const anchor = hash.replace(/^#/, "");
  if (path.endsWith("/story") || path.endsWith("/screenplay")) {
    return anchor === STORY_ANCHORS.script ? "script" : "story";
  }
  if (path.endsWith("/characters") || path.endsWith("/scenes")) return "assets";
  if (path.endsWith("/storyboard")) return "storyboard";
  if (path.endsWith("/tasks")) return "generation";
  return null;
}
