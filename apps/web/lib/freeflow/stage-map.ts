"use client";

import type { Approval, GateName, Stage } from "@/lib/api";
import type { StageKey, StageState } from "@/components/freeflow/shell/workbench-shell";
import type { OutputSet } from "@/lib/freeflow/use-project-state";

/**
 * 后端十一个阶段 ←→ 顶栏阶段条的五段。**全站只有这一份归并表。**
 *
 * 后端 `orchestrator._NEXT` 有十一个阶段（含四道门，ADR-037），阶段条的契约
 * （计划 §5）只有五段。两者不是同一层东西：后端那张图管"下一步跑什么"，
 * 阶段条管"用户走到哪了"。所以需要一次归并——但只需要一次，页面各写
 * 一份必然分叉。
 *
 *   routing / plot_index / await_plan  → story       故事（含开拍前确认）
 *   screenplay / await_setup           → script      剧本（含剧本门）
 *   characters / scenes / await_anchors→ assets      角色与世界（含锚点门）
 *   storyboard / await_storyboard      → storyboard  镜头（含分镜门）
 *   done                               → generation  生成
 *
 * 两道新门归在哪一段是按**它确认的是什么**定的，不是按它在时间轴上挨着谁：
 * `await_plan` 确认的是情节目录（故事阶段的收尾），`await_anchors` 确认的是
 * 场景的空间关系与光照（角色与世界的收尾）。归到下一段会让用户在"剧本"
 * 那一格上被要求核对情节目录，而情节目录根本不在那一页。
 *
 * 顺序按**生产顺序**排，不按 `StageKey` 联合类型的书写顺序——那只是一个
 * 类型，不表达先后。
 */
const SEGMENT_OF: Record<Stage, StageKey> = {
  routing: "story",
  plot_index: "story",
  await_plan: "story",
  screenplay: "script",
  await_setup: "script",
  characters: "assets",
  scenes: "assets",
  await_anchors: "assets",
  storyboard: "storyboard",
  await_storyboard: "storyboard",
  done: "generation",
};

const ORDER: readonly StageKey[] = ["story", "script", "assets", "storyboard", "generation"];

const LABEL: Record<StageKey, string> = {
  story: "故事",
  script: "剧本",
  assets: "角色与世界",
  storyboard: "镜头",
  generation: "生成",
};

/**
 * 哪一段上开着哪道门。没有门的段不出现在这里。
 *
 * **这张表和下面的 `SEGMENT_OF_GATE` 必须互为逆**：一段最多一道门，
 * 一道门只属于一段。ADR-037 之前它们是同一件事的两个三元式，加门时
 * 只改了其中一处。
 */
const GATE_OF: Partial<Record<StageKey, GateName>> = {
  story: "plan",
  script: "setup",
  assets: "anchors",
  storyboard: "storyboard",
};

/**
 * 门开在哪一段。
 *
 * **这里必须是一张表，不能是三元式。** 上一版写的是
 * `pendingGate === "setup" ? "script" : "storyboard"`——两道门时它碰巧对，
 * ADR-037 加到四道门时 `plan` 和 `anchors` 全落进了 `storyboard` 分支，
 * 于是"故事段的门开着"会把剧本、角色与世界两段一起显示成锁定。
 * 用 `Record<GateName, StageKey>` 的话，下次再加一道门，TypeScript 会
 * 因为缺键直接报错，而不是悄悄给出一个错的答案。
 */
const SEGMENT_OF_GATE: Record<GateName, StageKey> = {
  plan: "story",
  setup: "script",
  anchors: "assets",
  storyboard: "storyboard",
};

export function segmentOf(stage: Stage): StageKey {
  return SEGMENT_OF[stage];
}

/** 这道门开在哪一段。给"要批的东西在哪一页"这类跳转用。 */
export function segmentOfGate(gate: GateName): StageKey {
  return SEGMENT_OF_GATE[gate];
}

function latestApproval(approvals: Approval[], gate: GateName): Approval | null {
  return (
    approvals
      .filter((a) => a.gate === gate)
      .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0] ?? null
  );
}

/**
 * 阶段条的五段状态。
 *
 * 五种状态的含义（见 `stage-rail.tsx`）：
 * `approved` 已审核 / `ready` 已就绪 / `active` 当前阶段 /
 * `pending` 待开始 / `locked` 已锁定。
 *
 * 判定规则：
 * - 当前所在的那一段 → `active`
 * - 已经走过的段 → 有门且门通过了是 `approved`，否则 `ready`
 *   （没有门的段永远不该显示"已审核"，那会让用户以为自己批过一次）
 * - 还没走到的段 → `pending`；但**门开着时它后面的段是 `locked`**，
 *   因为 `advance` 在门上就停住了，不确认根本走不过去
 *
 * `generation` 段特殊：它对应后端的 `done`，也就是"文本链路走完"。
 * 出图本身不是一个后端阶段（出图是 `tasks`，任何时候都能发），所以这一段
 * 只在文本链路走完后才 `active`，之前一律 `pending` / `locked`。
 */
export function buildStages(params: {
  stage: Stage;
  approvals: Approval[];
  output: OutputSet;
  pendingGate: GateName | null;
}): StageState[] {
  const { stage, approvals, pendingGate } = params;
  const current = SEGMENT_OF[stage];
  const currentIndex = ORDER.indexOf(current);
  const gateIndex = pendingGate ? ORDER.indexOf(SEGMENT_OF_GATE[pendingGate]) : -1;

  return ORDER.map((key, index) => {
    let state: StageState["state"];
    if (index === currentIndex) {
      state = "active";
    } else if (index < currentIndex) {
      const gate = GATE_OF[key];
      const resolved = gate ? latestApproval(approvals, gate) : null;
      state = resolved?.status === "approved" ? "approved" : "ready";
    } else if (gateIndex >= 0 && index > gateIndex) {
      state = "locked";
    } else {
      state = "pending";
    }
    return { key, label: LABEL[key], state };
  });
}

/**
 * 当前这一段上那道门的状态，给右栏的 `AsideStageCard` 用。
 *
 * 只看**当前段自己的门**：四段各有各的门（`GATE_OF`），`generation` 段
 * 没有门就是 `none`。拿"最近一次审核"当当前段的状态会串台——剧本门通过
 * 之后走到角色阶段，右栏会一直显示"已通过"，而那一段的门是另一道。
 *
 * `changes_requested` 归到 `rejected`：契约只有四个值，而"打回重做"在用户
 * 眼里就是没通过。
 */
export function gateStatusOf(
  stage: Stage,
  approvals: Approval[],
): "needs_review" | "approved" | "rejected" | "none" {
  const gate = GATE_OF[SEGMENT_OF[stage]];
  if (!gate) return "none";
  const latest = latestApproval(approvals, gate);
  if (!latest) return "none";
  if (latest.status === "pending") return "needs_review";
  if (latest.status === "approved") return "approved";
  return "rejected";
}
