"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiRequestError,
  projects,
  type AgentRun,
  type Approval,
  type ChatMessage,
  type GateName,
  type Project,
  type ProjectStateSnapshot,
  type ReviseTarget,
  type Stage,
} from "@/lib/api";

/**
 * 阶段产出的**兜底**来源：`agent_runs.output_json`，按 agent_id 取最新一版。
 *
 * 正常情况下产出读 `current_state_json`（ADR-008 的唯一权威，
 * `GET /projects/{id}/state`）。这份映射只在 state 里缺某一块时补位——
 * 比如那条读接口临时挂了，或者存量项目的 state 是旧格式。
 */
const AGENT_ID_OF: Record<ReviseTarget, string> = {
  plot_index: "story.plot_index.v1",
  screenplay: "story.screenplay.v1",
  characters: "visual.character.v1",
  scenes: "visual.scene.v1",
  storyboard: "visual.storyboard.v1",
};

export const ROLE_LABEL: Record<ReviseTarget, string> = {
  plot_index: "情节目录",
  screenplay: "剧本",
  characters: "角色档案",
  scenes: "场景档案",
  storyboard: "分镜",
};

/**
 * 生产顺序。与后端 `orchestrator.PRODUCING_STAGES` 同序——过期记账
 * （`stale_roles`）按这个顺序返回，本地合并时也要按它排，否则同一份状态
 * 在"刚改完"和"刷新之后"会给出两种顺序。
 */
export const ROLE_ORDER: ReviseTarget[] = [
  "plot_index",
  "screenplay",
  "characters",
  "scenes",
  "storyboard",
];

export const STAGE_LABEL: Record<Stage, string> = {
  routing: "路线判断",
  plot_index: "情节目录",
  screenplay: "剧本改编",
  await_setup: "等待确认剧本",
  characters: "角色档案",
  scenes: "场景档案",
  storyboard: "分镜表",
  await_storyboard: "等待确认分镜",
  done: "已走完文本链路",
};

/** 门对应的阶段，以及门开在哪一份产出上。 */
export const GATE_STAGE: Record<GateName, Stage> = {
  setup: "await_setup",
  storyboard: "await_storyboard",
};

export const GATE_LABEL: Record<GateName, string> = {
  setup: "确认剧本",
  storyboard: "确认分镜",
};

export type OutputSet = Record<ReviseTarget, any>;

export type ProjectAction = "advance" | "approve" | "reject" | "revise";

function latestApproval(approvals: Approval[], gate: GateName): Approval | null {
  return (
    approvals
      .filter((a) => a.gate === gate)
      .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0] ?? null
  );
}

/**
 * 从"已有哪些产出 + 审核记录"倒推阶段。
 *
 * **这是兜底，不是主路径。** 主路径是 `GET /projects/{id}/state` 直接给的
 * `stage`（后端按 `orchestrator._NEXT` 算，含旧阶段名翻译）。只有那条接口
 * 拿不到时才走这里——否则界面会因为一次网络抖动整个变成"未知阶段"。
 *
 * 倒推按后端同一张图，并处理了门被 `changes_requested` 打回、后端退回
 * 生产阶段重做的情形。
 */
export function deriveStage(output: OutputSet, approvals: Approval[], runs: AgentRun[]): Stage {
  const storyboardGate = latestApproval(approvals, "storyboard");
  const setupGate = latestApproval(approvals, "setup");

  if (storyboardGate) {
    if (storyboardGate.status === "pending") return "await_storyboard";
    if (storyboardGate.status === "approved") return "done";
    // rejected 是"停在这里不动"，changes_requested 是"退回分镜重做"
    return storyboardGate.status === "rejected" ? "await_storyboard" : "storyboard";
  }

  if (setupGate?.status === "pending") return "await_setup";
  if (setupGate?.status === "changes_requested") return "screenplay";
  if (setupGate?.status === "rejected") return "await_setup";

  if (setupGate?.status === "approved") {
    if (output.storyboard) return "await_storyboard";
    if (output.scenes) return "storyboard";
    if (output.characters) return "scenes";
    return "characters";
  }

  if (output.screenplay) return "await_setup";
  if (output.plot_index) return "screenplay";
  return runs.length > 0 ? "plot_index" : "routing";
}

/**
 * 一个项目的完整生产状态：产出、运行记录、审核门、返工对话，
 * 以及四个会写库的动作。
 *
 * 页面只调这个 hook，不自己拼接口——「同一业务动作全站只有一个标准入口」
 * （11_WEB_WORKBENCH.md §9）。
 */
export function useProjectState(projectId: string) {
  const [project, setProject] = useState<Project | null>(null);
  const [snapshot, setSnapshot] = useState<ProjectStateSnapshot | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [conversation, setConversation] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<ProjectAction | null>(null);

  const reload = useCallback(async () => {
    const [p, st, r, a, c] = await Promise.all([
      projects.get(projectId),
      // 编排状态：阶段和阶段产出的权威来源。拿不到就退回从 agent_runs 倒推，
      // 不让整页空白——倒推出来的阶段在绝大多数情形下是对的。
      projects.state(projectId).catch(() => null),
      projects.runs(projectId),
      projects.approvals(projectId),
      // 返工对话是加分项，拿不到不该让整页空白
      projects.conversation(projectId).catch(() => [] as ChatMessage[]),
    ]);
    setProject(p);
    setSnapshot(st);
    setRuns(r);
    setApprovals(a);
    setConversation(c);
  }, [projectId]);

  useEffect(() => {
    setLoading(true);
    reload()
      .then(() => setError(null))
      .catch((cause) =>
        setError(cause instanceof ApiRequestError ? cause.error.user_message : "加载项目失败"),
      )
      .finally(() => setLoading(false));
  }, [reload]);

  const output = useMemo<OutputSet>(() => {
    const state = snapshot?.current_state_json ?? {};
    const latestRun = (agentId: string) =>
      runs.find((run) => run.agent_id === agentId && run.output_json)?.output_json;
    // state 里的那一份优先：字段级编辑（ADR-029）改的是它，`agent_runs`
    // 停在"上一次模型吐出来的样子"，两边不一致时它才是当前值。
    const of = (role: ReviseTarget) => state[role] ?? latestRun(AGENT_ID_OF[role]);
    return {
      plot_index: of("plot_index"),
      screenplay: of("screenplay"),
      characters: of("characters"),
      scenes: of("scenes"),
      storyboard: of("storyboard"),
    };
  }, [snapshot, runs]);

  const pendingApproval = useMemo(
    () => approvals.find((a) => a.status === "pending") ?? null,
    [approvals],
  );

  const stage = useMemo(
    () => snapshot?.stage ?? deriveStage(output, approvals, runs),
    [snapshot, output, approvals, runs],
  );

  /** 跑一次会写库的动作。成功失败都重新拉状态，界面上不留下乐观更新。 */
  const run = useCallback(
    async (action: ProjectAction, fn: () => Promise<unknown>) => {
      setBusy(action);
      setActionError(null);
      try {
        await fn();
        await reload();
        return true;
      } catch (cause) {
        setActionError(
          cause instanceof ApiRequestError ? cause.error.user_message : "操作失败，请稍后重试",
        );
        // 失败也重拉：advance 可能已经跑完前半段才在某一步失败
        await reload().catch(() => undefined);
        return false;
      } finally {
        setBusy(null);
      }
    },
    [reload],
  );

  /**
   * 推进生产。一路跑到下一个审核门（`to_gate=true`），是**真实 LLM 调用，
   * 会扣 Credits**，所以只能由明确的用户点击触发。
   *
   * `userInput` 只在第一次有意义：后端把它存进 `current_state_json.source`
   * 作为整条生产链的原始素材，之后每一步都从 state 拼输入。
   */
  const advance = useCallback(
    (userInput = "") => run("advance", () => projects.advance(projectId, userInput)),
    [projectId, run],
  );

  const approve = useCallback(
    (comment?: string) => {
      if (!pendingApproval) return Promise.resolve(false);
      return run("approve", () => projects.approve(projectId, pendingApproval.id, comment));
    },
    [projectId, pendingApproval, run],
  );

  /**
   * 打回重做。映射到后端的 `changes_requested`：退回产出这批内容的阶段。
   *
   * 后端还有一个 `rejected`——项目就地停死，`_NEXT` 不再推进，也没有任何
   * 接口能让它恢复。界面不给这个入口：一个点下去就再也回不来、且没有
   * 撤销路径的按钮，不该摆在生产流程里。要它得先有"重开项目"的后端语义。
   */
  const reject = useCallback(
    (comment?: string) => {
      if (!pendingApproval) return Promise.resolve(false);
      return run("reject", () => projects.reject(projectId, pendingApproval.id, comment));
    },
    [projectId, pendingApproval, run],
  );

  /** 自然语言返工。同一个 Agent、同一个 schema，只是输入形式是一句话。 */
  const revise = useCallback(
    (target: ReviseTarget, instruction: string) =>
      run("revise", () => projects.revise(projectId, target, instruction)),
    [projectId, run],
  );

  /**
   * 把一次字段级编辑（或撤销）的结果并回本地状态。**不重新拉接口。**
   *
   * ADR-029 的 PATCH / undo 都把"改完之后这个 role 的整块产出"放在响应里，
   * 就是为了省掉这一次 GET：中间隔着一次网络往返的话，界面上会有一段
   * 时间显示的还是旧值，看起来像"保存了但没生效"。
   *
   * 过期记账要**合并**不能替换：响应里的 `stale_roles` 只是"因为这次改动
   * 而新过期的下游"，比 `role` 更靠前的阶段如果本来就过期，那个标记仍然
   * 在库里（后端 `mark_stale` 是并集），直接替换会把它在界面上抹掉。
   * 这里做的是和后端同一件事的本地投影：并上新的，划掉刚改过的那个。
   *
   * `snapshot` 为 null（那次 state 接口没取到，产出正走 `agent_runs` 兜底）
   * 时无处可写，只能退回重新拉一次——那种情况下宁可闪一下也不能显示旧值。
   */
  const applyPatchedOutput = useCallback(
    (role: ReviseTarget, patched: Record<string, any>, newlyStale: ReviseTarget[]) => {
      // 判断走闭包里的 snapshot，不在 setState 的 updater 里记标志位——
      // updater 什么时候跑由 React 决定（StrictMode 下还会跑两次），
      // 拿它的副作用当条件必然读到过期的值。
      if (!snapshot) {
        void reload().catch(() => undefined);
        return;
      }
      const mergeStale = (prev: ReviseTarget[]) => {
        const merged = new Set<ReviseTarget>([...prev, ...newlyStale]);
        merged.delete(role);
        return ROLE_ORDER.filter((r) => merged.has(r));
      };
      setSnapshot((prev) =>
        prev
          ? {
              ...prev,
              current_state_json: { ...prev.current_state_json, [role]: patched },
              stale_roles: mergeStale(prev.stale_roles),
            }
          : prev,
      );
      setProject((prev) =>
        prev ? { ...prev, stale_roles: mergeStale(prev.stale_roles ?? []) } : prev,
      );
    },
    [snapshot, reload],
  );

  return {
    project,
    runs,
    approvals,
    conversation,
    output,
    stage,
    stageLabel: STAGE_LABEL[stage],
    pendingApproval,
    pendingGate: (pendingApproval?.gate as GateName | undefined) ?? null,
    /** 还没跑过任何一步：这时 `advance` 需要用户先给原始素材。 */
    needsSource: runs.length === 0,
    /** 整份编排状态。没取到就是 null——调用方要处理这种情况。 */
    snapshot,
    /** 过期记账：上游改过、这些下游产出停在旧版本。 */
    staleRoles: (snapshot?.stale_roles ?? project?.stale_roles ?? []) as ReviseTarget[],
    loading,
    error,
    actionError,
    busy,
    advance,
    approve,
    reject,
    revise,
    /** 字段级编辑 / 撤销之后把新产出并回本地状态。见上面的说明。 */
    applyPatchedOutput,
    reload,
    clearActionError: () => setActionError(null),
  };
}

export type ProjectState = ReturnType<typeof useProjectState>;
