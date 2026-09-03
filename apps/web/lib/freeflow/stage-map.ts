"use client";

import type { Approval, GateName, Stage } from "@/lib/api";
import type { StageKey, StageState } from "@/components/freeflow/shell/workbench-shell";
import type { OutputSet } from "@/lib/freeflow/use-project-state";

/**
 * 后端九个阶段 ←→ 顶栏阶段条的五段。**全站只有这一份归并表。**
 *
 * 后端 `orchestrator._NEXT` 有九个阶段（含两道门），阶段条的契约
 * （计划 §5）只有五段。两者不是同一层东西：后端那张图管"下一步跑什么"，
 * 阶段条管"用户走到哪了"。所以需要一次归并——但只需要一次，页面各写
 * 一份必然分叉。
 *
 *   routing / plot_index          → story       故事
 *   screenplay / await_setup      → script      剧本（含剧本门）
 *   characters / scenes           → assets      角色与世界
 *   storyboard / await_storyboard → storyboard  镜头（含分镜门）
 *   done                          → generation  生成
 *
 * 顺序按**生产顺序**排，不按 `StageKey` 联合类型的书写顺序——那只是一个
 * 类型，不表达先后。
 */
const SEGMENT_OF: Record<Stage, StageKey> = {
  routing: "story",
  plot_index: "story",
  screenplay: "script",
  await_setup: "script",
  characters: "assets",
  scenes: "assets",
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

/** 哪一段上开着门。没有门的段不出现在这里。 */
const GATE_OF: Partial<Record<StageKey, GateName>> = {
  script: "setup",
  storyboard: "storyboard",
};

export function segmentOf(stage: Stage): StageKey {
  return SEGMENT_OF[stage];
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
  const gateIndex = pendingGate
    ? ORDER.indexOf(pendingGate === "setup" ? "script" : "storyboard")
    : -1;

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
 * 只看**当前段自己的门**：`script` 段看剧本门，`storyboard` 段看分镜门，
 * 其余段没有门就是 `none`。拿"最近一次审核"当当前段的状态会串台——
 * 剧本门通过之后走到角色阶段，右栏会一直显示"已通过"，而那一段根本没有门。
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
