/**
 * 分镜工作台的目录、筛选与补图范围。纯函数，不依赖 React 与 `@/` 别名，
 * `tests/web-logic.test.mjs` 直接加载它。
 *
 * **身份一律是数组位置 `at`**，不是镜号 `index`：字段级编辑的 JSON Pointer
 * 走数组位置（ADR-029），而 Agent 不保证镜号连续。筛选只决定显示哪些，
 * 不改变任何一镜的身份——筛完再编辑，改到的仍是原数组里的那一条。
 */

export type ShotLike = {
  index: number;
  node_index?: number | null;
  scene_ref?: string | null;
  content?: string | null;
  dialogue?: string | null;
};

export type NodeLike = { index: number; summary?: string | null; scene_ref?: string | null };

/** 一镜在这一刻的出图事实。由页面从 `useImages` 算出来传进来。 */
export type ShotFact = {
  hasImage: boolean;
  /** 最新一条出图任务的状态；没出过是 null */
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | null;
  /** 本页刚点了、请求还没回来 */
  pending: boolean;
  /** 图出在这一镜最后一次改动之前 */
  outdated: boolean;
};

export type ShotFilter = "all" | "missing" | "failed" | "outdated" | "running";

export type DirectoryGroup = {
  /** `null` = 未归属节点（`node_index` 不在 `nodes` 里） */
  nodeIndex: number | null;
  summary: string;
  sceneRef: string;
  /** 这一组镜头的数组位置，按原数组顺序 */
  positions: number[];
};

export const ORPHAN_KEY = "orphan";

/** 节点的稳定 key。未归属组固定用 `orphan`。 */
export function groupKey(nodeIndex: number | null): string {
  return nodeIndex === null ? ORPHAN_KEY : `n${nodeIndex}`;
}

export function shotCode(index: number): string {
  return `S${String(index).padStart(2, "0")}`;
}

/**
 * 节点 → 镜头。节点按 `nodes` 数组顺序；没有镜头的节点也保留（覆盖表要标出来）；
 * `node_index` 缺失或指向不存在节点的镜头统一进最后的未归属组。
 */
export function buildDirectory(shots: ShotLike[], nodes: NodeLike[]): DirectoryGroup[] {
  const groups: DirectoryGroup[] = [];
  const byNode = new Map<number, DirectoryGroup>();
  for (const node of nodes ?? []) {
    const idx = Number(node?.index);
    if (!Number.isFinite(idx) || byNode.has(idx)) continue;
    const group: DirectoryGroup = {
      nodeIndex: idx,
      summary: String(node.summary ?? "").trim(),
      sceneRef: String(node.scene_ref ?? ""),
      positions: [],
    };
    byNode.set(idx, group);
    groups.push(group);
  }
  const orphan: DirectoryGroup = { nodeIndex: null, summary: "", sceneRef: "", positions: [] };
  (shots ?? []).forEach((shot, at) => {
    const idx = Number(shot?.node_index);
    const group = Number.isFinite(idx) ? byNode.get(idx) : undefined;
    (group ?? orphan).positions.push(at);
  });
  if (orphan.positions.length > 0) groups.push(orphan);
  return groups;
}

export function matchesFilter(fact: ShotFact | undefined, filter: ShotFilter): boolean {
  const f = fact ?? { hasImage: false, status: null, pending: false, outdated: false };
  switch (filter) {
    case "all":
      return true;
    case "missing":
      return !f.hasImage;
    case "failed":
      return f.status === "failed";
    case "outdated":
      return f.outdated;
    case "running":
      return isInFlight(f);
  }
}

export function isInFlight(fact: ShotFact | undefined): boolean {
  return Boolean(fact && (fact.pending || fact.status === "queued" || fact.status === "running"));
}

/**
 * 搜索：镜号（`S07` / `7`）、画面内容、台词、场景 ref 或场景名。
 * 空查询匹配全部。
 */
export function matchesQuery(
  shot: ShotLike,
  query: string,
  sceneName?: (ref: string) => string | undefined,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const code = shotCode(shot.index).toLowerCase();
  if (code === q || String(shot.index) === q.replace(/^s0*/, "")) return true;
  const ref = String(shot.scene_ref ?? "");
  const hay = [shot.content, shot.dialogue, ref, sceneName?.(ref)]
    .map((v) => String(v ?? "").toLowerCase())
    .join("\n");
  return hay.includes(q);
}

/** 筛选后仍可见的数组位置（保持原顺序）。 */
export function visiblePositions(
  shots: ShotLike[],
  facts: ShotFact[],
  opts: { query: string; filter: ShotFilter; sceneName?: (ref: string) => string | undefined },
): number[] {
  const out: number[] = [];
  shots.forEach((shot, at) => {
    if (!matchesFilter(facts[at], opts.filter)) return;
    if (!matchesQuery(shot, opts.query, opts.sceneName)) return;
    out.push(at);
  });
  return out;
}

export type Tally = {
  total: number;
  withImage: number;
  missing: number;
  failed: number;
  running: number;
  outdated: number;
};

export function tally(positions: number[], facts: ShotFact[]): Tally {
  const t: Tally = { total: 0, withImage: 0, missing: 0, failed: 0, running: 0, outdated: 0 };
  for (const at of positions) {
    const f = facts[at];
    t.total += 1;
    if (f?.hasImage) t.withImage += 1;
    else t.missing += 1;
    if (f?.status === "failed") t.failed += 1;
    if (isInFlight(f)) t.running += 1;
    if (f?.outdated) t.outdated += 1;
  }
  return t;
}

export type BatchPlan = {
  /** 要提交的数组位置 */
  submit: number[];
  /** 已有图，跳过（批量补图从不覆盖已有图——重出是再花一次钱） */
  skippedHasImage: number;
  /** 正在排队 / 运行 / 本页提交中，跳过（不重复提交） */
  skippedInFlight: number;
  /** 提交集中上次失败过的，会重新提交 */
  retryingFailed: number;
};

/** 在给定范围里算出补图要提交哪些镜头。 */
export function planBatch(positions: number[], facts: ShotFact[]): BatchPlan {
  const plan: BatchPlan = { submit: [], skippedHasImage: 0, skippedInFlight: 0, retryingFailed: 0 };
  for (const at of positions) {
    const f = facts[at];
    if (f?.hasImage) {
      plan.skippedHasImage += 1;
      continue;
    }
    if (isInFlight(f)) {
      plan.skippedInFlight += 1;
      continue;
    }
    if (f?.status === "failed") plan.retryingFailed += 1;
    plan.submit.push(at);
  }
  return plan;
}

/** 字段级编辑的 JSON Pointer。下标是数组位置，不是镜号。 */
export function shotPointer(at: number, field: string): string {
  return `/shots/${at}/${field}`;
}

/**
 * 数组位置 → 要提交的镜号，去重、保持顺序。
 *
 * 出图按镜号关联（`POST /images/shots/{index}`）。镜号重复的两条共用同一份
 * 出图记录，提交两次就是同一镜花两次钱。
 */
export function submitIndexes(positions: number[], shots: ShotLike[]): number[] {
  const seen = new Set<number>();
  const out: number[] = [];
  for (const at of positions) {
    const index = Number(shots[at]?.index);
    if (!Number.isFinite(index) || seen.has(index)) continue;
    seen.add(index);
    out.push(index);
  }
  return out;
}
