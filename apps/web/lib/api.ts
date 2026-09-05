/**
 * API 客户端。
 *
 * 令牌走 httpOnly Cookie，前端读不到也不需要读——所以这里只管
 * `credentials: "include"`，不做任何 token 存取。
 */

export type ApiError = {
  code: string;
  message: string;
  /** 可以直接显示给用户的文案，永远优先用它 */
  user_message: string;
  retryable: boolean;
  trace_id: string;
  /**
   * 结构化补充信息。**形状随错误来源而变**，所以除了 `errors` 之外都不定型：
   *
   * - FastAPI 的请求体校验（`main.py` 的 `handle_validation_error`）给
   *   `errors: [{field, message, type}]`；
   * - `AppError` 自己带的 detail 什么都可能有——ADR-029 的字段级编辑
   *   校验不过时给 `errors: [{loc, type, msg}]`（pydantic 原始 loc），
   *   撤销冲突时给 `conflicts: string[]`。
   *
   * 想读非 `errors` 的键就自己收窄类型，不要往这里堆联合——每加一个后端
   * 错误就改一次公共类型，最后没人知道哪个键属于哪条接口。
   */
  detail?: {
    errors?: { field: string; message: string; type: string }[];
    [key: string]: unknown;
  };
};

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly error: ApiError,
  ) {
    super(error.message);
    this.name = "ApiRequestError";
  }

  /** 表单字段级错误，用于把提示放到对应输入框下面 */
  get fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const e of this.error.detail?.errors ?? []) {
      out[e.field] ??= e.message;
    }
    return out;
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });

  if (res.status === 204) return undefined as T;

  const body = await res.json().catch(() => null);

  // 会话过期就直接送回登录页。只把错误显示在按钮旁边，用户看到的是
  // "点了没反应"——他不会把一行小字和"我得重新登录"联系起来。
  // 登录接口本身的 401 是"密码错了"，不能跳，否则表单永远提交不了。
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/")) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = `/login?next=${next}`;
  }

  if (!res.ok) {
    throw new ApiRequestError(
      res.status,
      body?.error ?? {
        code: "common.internal",
        message: `HTTP ${res.status}`,
        user_message: "服务暂时不可用，请稍后重试",
        retryable: true,
        trace_id: "",
      },
    );
  }

  return body as T;
}

export type User = {
  id: string;
  org_id: string;
  email: string;
  display_name: string;
  role: string;
  status: string;
  created_at: string;
};

export type Project = {
  id: string;
  title: string;
  route_type: string | null;
  status: string;
  spent_credits: number;
  /**
   * 项目级模型覆盖（ADR-024）：capability → model_id。
   * 没设过的项目是 `{}` 而不是 null——后端已经统一过了，这里不用再兜底。
   */
  model_preference: Record<string, string>;
  /**
   * 上游被修订后已经过期、需要同步的阶段产出。
   * 后端记在 `current_state_json` 里，所以刷新页面也还在——
   * 不要用 revise 那一次响应的返回值当真相。
   */
  stale_roles: ReviseTarget[];
  created_at: string;
};

export type Advance = {
  stage: string;
  ran_role: string | null;
  gate_opened: string | null;
  blocked: boolean;
  output: Record<string, unknown> | null;
};

export type Approval = {
  id: string;
  gate: string;
  status: string;
  payload_json: { stage?: string; summary?: Record<string, unknown> };
  comment: string | null;
  created_at: string;
  resolved_at: string | null;
};

export type AgentRun = {
  id: string;
  agent_id: string;
  role: string;
  status: string;
  model_id: string | null;
  tokens_in: number;
  tokens_out: number;
  attempts: number;
  error_code: string | null;
  output_json: Record<string, any> | null;
  created_at: string;
  /** 还在跑时为 null。与 created_at 相减就是耗时，前端不另存开始时间。 */
  finished_at: string | null;
};

export type Balance = { balance: number; reserved: number; total: number };

export type ReviseTarget =
  | "plot_index"
  | "screenplay"
  | "characters"
  | "scenes"
  | "storyboard";

export type Revise = {
  target_role: ReviseTarget;
  revision: number;
  /** 后端 diff 出来的实际改动，不是模型自述 */
  changed_fields: string[];
  output: Record<string, any>;
  /** 基于旧版生成的下游产出，需要重新生成才会同步 */
  stale_roles: string[];
};

export type ChatMessage = {
  id: string;
  target_role: ReviseTarget;
  author: "user" | "assistant";
  text: string;
  revision: number;
  changed_fields: string[];
  created_at: string;
};

// ---------------------------------------------------------------- 字段级编辑（ADR-029）

/**
 * 一处字段改动。**只有"替换"一种语义**：`path` 指向的字段必须已经存在，
 * 不能新建键，也不能往数组里追加（后端 `content/patching.py` 规则 1）。
 * 要多一个角色 / 多一镜，只能重跑 Agent。
 */
export type PatchOp = {
  /**
   * RFC 6901 JSON Pointer，**相对于这个 role 的整块产出**，
   * 例如分镜第 1 镜的景别是 `/shots/0/shot_size`。
   *
   * 下标是**数组位置**，不是 `shot.index` 那个业务镜号。两者在正常产出里
   * 恰好差 1，但 Agent 并不保证——照镜号算会改到别的镜上去。
   */
  path: string;
  value: unknown;
};

/** 写路径的统一返回体。PATCH 和撤销共用——它们在后端是同一种操作。 */
export type PatchResult = {
  batch_id: string;
  role: ReviseTarget;
  changed: number;
  /**
   * 改完之后这个 role 的整块产出。**直接拿它更新本地状态**，
   * 不要改完再 GET 一次：那中间有一个窗口界面还是旧值，会闪。
   */
  output: Record<string, any>;
  /**
   * **因为这次改动而新过期的下游**，不是"当前全部过期的阶段"。
   *
   * 后端 `mark_role_edited` 返回的是 `downstream_of(role)`，同时把 `role`
   * 自己从过期账上划掉；比 `role` 更靠前的阶段如果本来就是过期的，
   * 那个标记还在，只是不出现在这个字段里。所以本地合并要算
   * `(旧的 ∪ 这次的) \ {role}`，直接拿它整个替换会把已有的过期标记抹掉。
   */
  stale_roles: ReviseTarget[];
};

/** 一批里的一行：改了哪个字段、从什么变成什么。 */
export type RevisionChange = {
  id: string;
  field_path: string;
  old_value: unknown;
  new_value: unknown;
};

export type RevisionBatch = {
  batch_id: string;
  role: ReviseTarget;
  /** `user_edit` = 用户改的，`undo` = 一次撤销。撤销本身也是一批。 */
  source: string;
  reason: string | null;
  actor_user_id: string | null;
  created_at: string;
  changes: RevisionChange[];
  /** 有值 = 这批已经被撤销过了，不能再撤第二次。 */
  undone_by_batch_id: string | null;
  /** 有值 = 这批本身是一次撤销。不能靠 `undone_by_batch_id` 反推。 */
  undoes_batch_id: string | null;
};

export type RevisionPage = { items: RevisionBatch[]; next_cursor: string | null };

/**
 * 把字段级编辑的报错变成能贴到界面上的一句话。
 *
 * 为什么不像别处那样只用 `user_message`：这条链路上的错误几乎都是
 * `common.validation_failed` / `common.conflict`，它们的 `user_message`
 * 是目录里那句通用的「请求参数有误」「操作冲突，请刷新后重试」，
 * 而用户真正需要知道的是**哪个字段、为什么不行**——那句话在 `message`
 * 里（后端为这条链路专门写的中文原文，如「这些字段在这批之后又被改过」）。
 *
 * 两种 `detail.errors` 形状都要认：FastAPI 请求体校验给 `field/message`，
 * ADR-029 的整块 schema 校验给 pydantic 原始的 `loc/msg`。
 */
export function editErrorText(cause: unknown): string {
  if (!(cause instanceof ApiRequestError)) return "操作失败，请稍后重试";

  const { message, user_message, detail } = cause.error;
  // message 默认值是错误码本身（后端 AppError：`self.message = message or code`），
  // 那种情况下它不比 user_message 有信息量。
  const head = message && !message.startsWith("common.") ? message : user_message;

  const parts: string[] = [head];

  const rows = Array.isArray(detail?.errors) ? (detail.errors as Record<string, unknown>[]) : [];
  const fields = rows
    .slice(0, 5)
    .map((e) => {
      const where = String(e.field ?? e.loc ?? "");
      const why = String(e.message ?? e.msg ?? "");
      return where ? `${where}：${why}` : why;
    })
    .filter(Boolean);
  if (fields.length > 0) parts.push(fields.join("；"));

  const conflicts = Array.isArray(detail?.conflicts) ? (detail.conflicts as string[]) : [];
  if (conflicts.length > 0) parts.push(`冲突字段：${conflicts.slice(0, 5).join("、")}`);

  return parts.join(" — ");
}

/**
 * 一次出图。提示词由后端用一致性引擎合成，前端不传也传不了——
 * 风格词必须由系统注入，这是画风一致性的唯一保障。
 */
export type Render = {
  /**
   * `source: "assigned"` 那条路没有任务，所以可空。
   * 重试按钮必须靠它判断——没有任务的东西重试不了。
   */
  task_id: string | null;
  subject_kind: "character" | "scene" | "shot";
  /** 角色立绘 / 场景参考图才有 */
  subject_ref: string | null;
  /** 分镜出图才有 */
  shot_index: number | null;
  status: TaskStatus;
  progress: number;
  error_code: string | null;
  /** 出成功了才有。取图 URL 走 assets.downloadUrl，预签名链接存不住 */
  asset_id: string | null;
  created_at: string;
  /**
   * 这张图是怎么来的。
   *
   * `generated` = 真跑过一次生成，扣过 Credits；
   * `assigned`  = 用户自己指定的一张既有资产（库存或本地上传），一分钱没花。
   *
   * 界面上两者占同一个位置、长得一样，但计费语义相反，所以要分开显示。
   */
  source: RenderSource;
};

export type RenderSource = "generated" | "assigned";

/** 把一张已有资产钉成基准图之后的回执。刻意不是 Task——这里没有任务在跑。 */
export type BaseImage = {
  subject_kind: "character" | "scene";
  subject_ref: string;
  asset_id: string;
  updated_at: string;
};

export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export type Task = {
  id: string;
  project_id: string | null;
  type: string;
  status: TaskStatus;
  progress: number;
  attempt: number;
  error_code: string | null;
  estimated_cost: number;
  actual_cost: number;
  output_json: Record<string, any> | null;
  created_at: string;
};

/**
 * 编排器的阶段名（后端 `orchestrator._NEXT` 的键）。
 *
 * `await_*` 是**门**，不是生产阶段：跑到那里就停下等人确认。
 *
 * 注意：**没有任何接口直接返回当前阶段**——它存在
 * `projects.current_state_json.stage` 里，而 `ProjectOut` 不暴露这一列。
 * 唯一会返回它的是 `advance` / 审核决议的响应（`Advance.stage`），
 * 那是一次性的，刷新就没了。所以前端读到的阶段是由
 * `lib/freeflow/use-project-state.ts` 从「已有哪些产出 + 审核记录」推出来的。
 */
export type Stage =
  | "routing"
  | "plot_index"
  | "screenplay"
  | "await_setup"
  | "characters"
  | "scenes"
  | "storyboard"
  | "await_storyboard"
  | "done";

/** 两道阻塞门（后端 `orchestrator._GATE_OF`）。 */
export type GateName = "setup" | "storyboard";

/** 审核决议。后端三个都收，界面只用前两个，理由见 use-project-state.ts。 */
export type ApprovalDecision = "approved" | "changes_requested" | "rejected";

export type TaskPage = { items: Task[]; next_cursor: string | null };

/**
 * 项目的编排状态（`GET /projects/{id}/state`）。
 *
 * `current_state_json` 是 ADR-008 里"状态的唯一权威"：`source`、`router`、
 * 五个阶段产出、`stale_roles` 都在里面。**只读**——写路径只有编排器和
 * content 模块。
 *
 * `stage` 由后端算好（含旧阶段名翻译），前端不再从 `agent_runs` 反推。
 */
export type ProjectStateSnapshot = {
  project_id: string;
  stage: Stage;
  current_state_json: Record<string, any>;
  stale_roles: ReviseTarget[];
  updated_at: string;
};

export const projects = {
  list: () => apiFetch<{ items: Project[]; next_cursor: string | null }>("/projects?limit=50"),

  create: (title: string) =>
    apiFetch<Project>("/projects", { method: "POST", body: JSON.stringify({ title }) }),

  get: (id: string) => apiFetch<Project>(`/projects/${id}`),

  /**
   * 编排状态：当前阶段 + 整份 `current_state_json`。
   *
   * 阶段和阶段产出都以它为准，不要再从 `agent_runs` 反推——`agent_runs`
   * 只记"某次运行吐了什么"，字段级编辑（ADR-029）改的是 `current_state_json`，
   * 两边会分叉。
   */
  state: (id: string) => apiFetch<ProjectStateSnapshot>(`/projects/${id}/state`),

  /**
   * 改项目本身。后端 `PATCH /projects/{id}` 只收这三样
   * （`ProjectUpdateIn`）——没有描述、分辨率、帧率这些列，别往里塞。
   *
   * 只传要改的那一项：三个字段都是可选的，漏传等于不改。
   */
  update: (
    id: string,
    patch: { title?: string; route_type?: string | null; budget_cap_credits?: number | null },
  ) => apiFetch<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),

  /** 软删除——后端早就有这条路由（`repo.soft_delete`，`deleted_at` 打时间戳，
   *  列表查询已经在过滤），只是这层封装一直没补。 */
  remove: (id: string) => apiFetch<void>(`/projects/${id}`, { method: "DELETE" }),

  /** 推进到下一个审核门。真实 LLM 调用，会花 Credits。 */
  advance: (id: string, userInput: string) =>
    apiFetch<Advance>(`/projects/${id}/advance?to_gate=true`, {
      method: "POST",
      body: JSON.stringify({ user_input: userInput }),
    }),

  approvals: (id: string) => apiFetch<Approval[]>(`/projects/${id}/approvals`),

  resolve: (id: string, approvalId: string, decision: ApprovalDecision, comment?: string) =>
    apiFetch<Advance>(`/projects/${id}/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({ decision, comment: comment || null }),
    }),

  /** 过门：编排器进入下一阶段。会让下一次 `advance` 真的花钱，所以要用户点。 */
  approve: (id: string, approvalId: string, comment?: string) =>
    projects.resolve(id, approvalId, "approved", comment),

  /**
   * 打回重做：退回产出这批内容的那个阶段（后端 `_REDO_FROM`），
   * 剧本门退回 `screenplay`，分镜门退回 `storyboard`。
   *
   * 这是"驳回"在生产流程里的可用形态。后端还有一个 `rejected`
   * （项目就地停死，没有恢复路径），界面不给入口——见
   * `lib/freeflow/use-project-state.ts` 的说明。
   */
  reject: (id: string, approvalId: string, comment?: string) =>
    projects.resolve(id, approvalId, "changes_requested", comment),

  runs: (id: string) => apiFetch<AgentRun[]>(`/projects/${id}/agent-runs?limit=50`),

  /** 用一句话改掉某个阶段的产出。走同一个 Agent 与同一个 schema。 */
  revise: (id: string, targetRole: ReviseTarget, instruction: string) =>
    apiFetch<Revise>(`/projects/${id}/revise`, {
      method: "POST",
      body: JSON.stringify({ target_role: targetRole, instruction }),
    }),

  conversation: (id: string) => apiFetch<ChatMessage[]>(`/projects/${id}/conversation`),

  /** 这个项目已经出过的图（含还在跑的和失败的），最新的在前。 */
  renders: (id: string) => apiFetch<Render[]>(`/projects/${id}/images`),

  /**
   * 给角色出基准立绘。真实出图调用，会扣 Credits，
   * 所以只能由明确的用户动作触发，不要放进任何自动重试里。
   */
  renderCharacter: (id: string, ref: string) =>
    apiFetch<Task>(`/projects/${id}/images/characters/${encodeURIComponent(ref)}`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    }),

  /**
   * 给场景出基准参考图。同上，会扣 Credits。
   *
   * 参考图的提示词里有摄影主轴和固定参照物，是同一场景后续所有镜头的
   * 空间基准——和角色立绘一样，提示词全部由后端 `compose` 合成，
   * 前端传不了也不该传。
   */
  renderScene: (id: string, ref: string) =>
    apiFetch<Task>(`/projects/${id}/images/scenes/${encodeURIComponent(ref)}`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    }),

  /**
   * 把一张**已有**的资产钉成角色的基准立绘。**不出图，不扣 Credits。**
   *
   * 和 `renderCharacter` 是同一个路径的两个动词，区别是根本性的：
   * POST 是"再生成一张新的"（每次花钱、结果不同），PUT 是"这个角色的
   * 基准图就是它"（幂等，点十次和点一次一样）。所以这条可以放心地
   * 重试，那条不行。
   */
  setCharacterPortrait: (id: string, ref: string, assetId: string) =>
    apiFetch<BaseImage>(`/projects/${id}/images/characters/${encodeURIComponent(ref)}`, {
      method: "PUT",
      body: JSON.stringify({ asset_id: assetId }),
    }),

  /** 把一张已有资产钉成场景的基准参考图。同上，不扣 Credits。 */
  setSceneReference: (id: string, ref: string, assetId: string) =>
    apiFetch<BaseImage>(`/projects/${id}/images/scenes/${encodeURIComponent(ref)}`, {
      method: "PUT",
      body: JSON.stringify({ asset_id: assetId }),
    }),

  /** 给一个镜号出图。同上，会扣 Credits。 */
  renderShot: (id: string, shotIndex: number) =>
    apiFetch<Task>(`/projects/${id}/images/shots/${shotIndex}`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    }),

  /**
   * 按能力覆盖这个项目用哪个模型（ADR-024）。
   *
   * 每次只传正在改的那一个能力，后端按 key 合并——不要把整份
   * `model_preference` 读出来再传回去，两个标签页同时开着会互相覆盖。
   *
   * `modelId = null` 是"回到默认"，不是"没填"。
   *
   * 存偏好本身不花钱：它只改下次解析的输入，不预扣也不结算。
   * 真正的重新估价发生在下一次生成，按 `model_pricing` 算（ADR-024 硬约束 1）。
   */
  setModelPreference: (id: string, capability: string, modelId: string | null) =>
    apiFetch<Project>(`/projects/${id}/model-preference`, {
      method: "PATCH",
      body: JSON.stringify({ capability, model_id: modelId }),
    }),

  // ---------------------------------------------------------------- 字段级编辑（ADR-029）

  /**
   * 按字段改一个阶段的产出。**一次调用 = 一次用户操作 = 一个可撤销的批次。**
   *
   * `patches` 必须把这次保存里改动的字段**一次全带上**：撤销是按批走的，
   * 拆成 N 个请求，用户眼里的"一次保存"就要点 N 次撤销才能退回去。
   *
   * 这条路径**一分钱不花**：不建任务、不跑 Agent、不预扣也不结算。
   * 改动由后端按该阶段自己的 Pydantic schema 校验——**前端不要另写一套
   * 字段白名单**，两套规则必然分叉，且分叉方向永远是前端更松。
   */
  patchOutput: (id: string, role: ReviseTarget, patches: PatchOp[], reason?: string) =>
    apiFetch<PatchResult>(`/projects/${id}/outputs/${role}`, {
      method: "PATCH",
      body: JSON.stringify({ patches, reason: reason?.trim() || null }),
    }),

  /**
   * 变更历史，按批分组、按时间倒序、游标分页。
   *
   * `role` 只是过滤条件：不传就是整个项目的改动。分页按**批**不按行，
   * 所以一批里的 `changes` 永远是全的。
   */
  revisions: (
    id: string,
    opts: { role?: ReviseTarget; limit?: number; cursor?: string } = {},
  ) => {
    const q = new URLSearchParams({ limit: String(opts.limit ?? 20) });
    if (opts.role) q.set("role", opts.role);
    if (opts.cursor) q.set("cursor", opts.cursor);
    return apiFetch<RevisionPage>(`/projects/${id}/revisions?${q}`);
  },

  /**
   * 撤销一整批改动。返回体和 `patchOutput` 完全一样——撤销在后端就是
   * "反向重放一批 patch"，是同一种操作，所以它自己也会进历史、也能再被撤销。
   *
   * 只要批次里有任何一个字段在这批之后又被改过，整批 409
   * （`detail.conflicts` 是冲突的路径）。已经撤过的批也是 409
   * （`detail.undone_by_batch_id`）——所以按钮要靠 `undone_by_batch_id`
   * 提前禁用，不要让用户点下去吃一个错误。
   */
  undoRevision: (id: string, batchId: string, reason?: string) =>
    apiFetch<PatchResult>(`/projects/${id}/revisions/${batchId}/undo`, {
      method: "POST",
      body: JSON.stringify({ reason: reason?.trim() || null }),
    }),
};

export const tasks = {
  /**
   * 任务列表。**`tasks` 是执行状态的唯一真相**（CLAUDE.md 不可违反的规则）：
   * 出图 / 视频 / 配音 / 合成都只在这张表里，`agent_runs` 里根本没有它们。
   *
   * 不带 `projectId` 是全组织的；任务页带上，只看当前项目。
   */
  list: (opts: { projectId?: string; status?: TaskStatus; limit?: number; cursor?: string } = {}) => {
    const q = new URLSearchParams({ limit: String(opts.limit ?? 50) });
    if (opts.projectId) q.set("project_id", opts.projectId);
    if (opts.status) q.set("status", opts.status);
    if (opts.cursor) q.set("cursor", opts.cursor);
    return apiFetch<TaskPage>(`/tasks?${q}`);
  },

  get: (id: string) => apiFetch<Task>(`/tasks/${id}`),

  /** 重试会重新预扣一笔，不是"免费再跑一次"。 */
  retry: (id: string) => apiFetch<Task>(`/tasks/${id}/retry`, { method: "POST" }),

  cancel: (id: string) => apiFetch<Task>(`/tasks/${id}/cancel`, { method: "POST" }),
};

export const realtime = {
  /**
   * 项目事件流的一次性票据。
   *
   * SSE 用 `EventSource`，它带不了自定义头也带不了我们的 httpOnly Cookie
   * 跨端口场景，所以先换一张短票据再拼进 query。订阅逻辑在
   * `lib/useProjectEvents.ts`，页面不直接用这个函数。
   */
  ticket: (projectId: string) =>
    apiFetch<{ ticket: string; expires_in: number }>(`/projects/${projectId}/events/ticket`, {
      method: "POST",
    }),

  /** EventSource 的地址。票据在 query 里——EventSource 没有别的地方放。 */
  eventsUrl: (projectId: string, ticket: string) =>
    `/api/v1/projects/${projectId}/events?ticket=${encodeURIComponent(ticket)}`,
};

/**
 * 预估结果。**给区间不给点值**（19_UnitEconomics.md §6）：真实成本受废片率
 * 和模型实际用量影响，报一个精确数字只会在结算时对不上。
 */
export type Estimate = {
  estimated_credits: number;
  range_low: number;
  range_high: number;
};

export const credits = {
  balance: () => apiFetch<Balance>("/credits/balance"),

  /**
   * 生成前估价。**只是算一下，不建任务、不预扣、不结算。**
   *
   * `input` 的形状按任务类型定（`billing/pricing.py` 的 `_shape`）：
   * 出图是 `{ n: 张数, model_id?: string }`，单价乘张数再折废片率。
   * 不传 `model_id` 走该能力的默认模型——所以这是**估算，不是报价**：
   * 项目级模型偏好和 failover 都可能让实际用的模型不是这一个。
   */
  estimate: (type: string, input: Record<string, unknown> = {}) =>
    apiFetch<Estimate>("/credits/estimate", {
      method: "POST",
      body: JSON.stringify({ type, input }),
    }),

  topup: (principal: number) =>
    apiFetch<Balance>("/credits/topup", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ principal }),
    }),
};

export const auth = {
  register: (data: { email: string; password: string; display_name: string }) =>
    apiFetch<{ user: User; access_expires_in: number }>("/auth/register", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  login: (data: { email: string; password: string }) =>
    apiFetch<{ user: User; access_expires_in: number }>("/auth/login", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  logout: () => apiFetch<void>("/auth/logout", { method: "POST" }),

  me: () => apiFetch<User>("/auth/me"),
};

// ---------------------------------------------------------------- 资产库

export type StorageUsage = {
  used_bytes: number;
  /** null = 后端没配配额规则，即不限容量 */
  quota_bytes: number | null;
  free_bytes: number | null;
  /** 后端算好的整数百分比。不要在前端重算，两处各算一次迟早对不上 */
  percent_used: number;
};

export type LibraryAsset = {
  id: string;
  project_id: string | null;
  /** null = 未分类 */
  folder_id: string | null;
  type: string;
  status: string;
  filename: string;
  mime_type: string;
  size_bytes: number | null;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  moderation_status: string;
  created_at: string;
};

/** 角色档案 / 场景档案。是 agent_runs 的产出，不是文件，不占容量。 */
export type ProfileEntry = {
  project_id: string;
  project_title: string;
  kind: "characters" | "scenes";
  agent_id: string;
  run_id: string;
  output: Record<string, any>;
  created_at: string;
  /** null = 未分类 */
  folder_id: string | null;
};

/**
 * 独立角色档案：一段参考描述直接生成的，**不挂任何项目**。
 * `output` 与 ProfileEntry 里的角色档案是同一个 schema，所以两处共用
 * CharactersView 渲染——抄一份必然分叉。
 */
export type CharacterEntry = {
  id: string;
  title: string;
  source_text: string;
  agent_id: string;
  output: Record<string, any>;
  model_id: string | null;
  created_at: string;
  folder_id: string | null;
};

export type AssetFolder = {
  id: string;
  name: string;
  item_count: number;
  created_at: string;
};

/** 能归类的东西。三种 id 指向三张不同的表，所以类型要一起带上。 */
export type FolderItemType = "asset" | "profile" | "character";

/**
 * 直传票据。上传是三段式（10_API.md）：建 pending 记录拿预签名地址 →
 * 客户端 PUT 到对象存储（不经过 API）→ complete 让服务端 HEAD 校验后置 ready。
 * 第三步不能省：只信客户端"我传完了"，后面会拿到一个坏文件。
 */
export type UploadTicket = {
  asset: LibraryAsset;
  upload_url: string;
  expires_at: string;
};

/**
 * 后端 MIME 白名单里的图片那几项（`apps/api/modules/asset/mime.py`）。
 *
 * 只用来给文件选择器过滤，**不是第二套校验**：真正说了算的还是后端，
 * 它在签发直传地址那一步就会拒掉不认识的类型和超限的大小。前端再写一遍
 * 大小上限只会和 `s3_max_upload_bytes` 分叉，所以这里没有那个数字。
 */
export const IMAGE_MIME_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"] as const;

export const IMAGE_ACCEPT = IMAGE_MIME_TYPES.join(",");

/**
 * 后端 MIME 白名单的全集（`apps/api/modules/asset/mime.py` 的 `ALLOWED_MIME`）。
 *
 * 同样**只用来给文件选择器过滤**，不是第二套校验：真正说了算的是后端，
 * 它在签发直传地址那一步就会拒掉不认识的类型和超限的大小。
 * 改后端那张表要同步这里，否则用户会挑不到一个其实能传的文件。
 */
export const UPLOAD_MIME_TYPES = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "video/mp4",
  "video/webm",
  "video/quicktime",
  "audio/mpeg",
  "audio/wav",
  "audio/x-wav",
  "audio/mp4",
  "text/plain",
  "text/markdown",
  "application/json",
  "application/pdf",
  "application/epub+zip",
] as const;

export const UPLOAD_ACCEPT = UPLOAD_MIME_TYPES.join(",");

export type Library = {
  usage: StorageUsage;
  assets: LibraryAsset[];
  next_cursor: string | null;
  profiles: ProfileEntry[];
  characters: CharacterEntry[];
  folders: AssetFolder[];
};

export const assets = {
  /**
   * 不带 folderId 是"全部"视图；带上就只列那个文件夹里的东西。
   *
   * 不带 projectId 是"全部项目"；带上就只列那个项目里的东西（项目内素材
   * 页用）。**筛选在后端做**——前端拿全量再筛的话，limit 100 一到就会漏。
   * 按项目筛时后端不返回独立角色档案：它们不挂任何项目。
   */
  library: (opts: { type?: string; folderId?: string; projectId?: string } = {}) => {
    const q = new URLSearchParams({ limit: "100" });
    if (opts.type) q.set("type", opts.type);
    if (opts.folderId) q.set("folder_id", opts.folderId);
    if (opts.projectId) q.set("project_id", opts.projectId);
    return apiFetch<Library>(`/assets/library?${q}`);
  },

  usage: () => apiFetch<StorageUsage>("/assets/usage"),

  /** 第一段：建一条 pending 记录，换回预签名直传地址。 */
  createUploadUrl: (file: File, projectId?: string) =>
    apiFetch<UploadTicket>("/assets/upload-url", {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        mime_type: file.type,
        size_bytes: file.size,
        project_id: projectId ?? null,
      }),
    }),

  /** 第三段：服务端 HEAD 校验后置 ready。不调它资产永远是 pending。 */
  completeUpload: (assetId: string) =>
    apiFetch<LibraryAsset>(`/assets/${assetId}/complete`, { method: "POST" }),

  /**
   * 三段式跑完，返回可用的 asset_id。
   *
   * 中间那一段**不走 `apiFetch`**：预签名地址指向对象存储本身，不是
   * 我们的 API，带上 `credentials` 和 JSON 头只会让签名对不上。
   * Content-Type 必须和签发时用的完全一致，SigV4 把它算进签名了。
   */
  upload: async (file: File, projectId?: string): Promise<string> => {
    const ticket = await assets.createUploadUrl(file, projectId);
    const put = await fetch(ticket.upload_url, {
      method: "PUT",
      body: file,
      headers: { "Content-Type": file.type },
    });
    if (!put.ok) {
      throw new ApiRequestError(put.status, {
        code: "asset.upload.checksum_mismatch",
        message: `PUT ${put.status}`,
        user_message: "文件上传失败，请重试",
        retryable: true,
        trace_id: "",
      });
    }
    await assets.completeUpload(ticket.asset.id);
    return ticket.asset.id;
  },

  /**
   * 缩略图/下载都要现签一个 URL：对象存储里的东西不能直接暴露，
   * 预签名链接也有有效期，存不住。
   */
  downloadUrl: (id: string) =>
    apiFetch<{ url: string; expires_at: string }>(`/assets/${id}/download-url`),

  createFolder: (name: string) =>
    apiFetch<AssetFolder>("/assets/folders", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  renameFolder: (id: string, name: string) =>
    apiFetch<AssetFolder>(`/assets/folders/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),

  /** 删文件夹不删里面的东西，它们只是回到未分类。 */
  deleteFolder: (id: string) => apiFetch<void>(`/assets/folders/${id}`, { method: "DELETE" }),

  /** folderId 传 null 表示移出文件夹。 */
  classify: (itemType: FolderItemType, itemId: string, folderId: string | null) =>
    apiFetch<void>("/assets/folder-items", {
      method: "PUT",
      body: JSON.stringify({ item_type: itemType, item_id: itemId, folder_id: folderId }),
    }),

  /**
   * 一段描述 → 一份正式角色档案。是一次真实的 LLM 调用，会扣 Credits，
   * 所以调用方必须有明确的用户动作触发，不能自动重试。
   */
  createCharacter: (description: string, folderId?: string | null) =>
    apiFetch<{ entry: CharacterEntry; reserved_credits: number; cost_credits: number }>(
      "/assets/characters",
      {
        method: "POST",
        body: JSON.stringify({ description, folder_id: folderId ?? null }),
      },
    ),

  deleteCharacter: (id: string) =>
    apiFetch<void>(`/assets/characters/${id}`, { method: "DELETE" }),
};

// ---------------------------------------------------------------- 自带 Key（BYOK）

export type ProviderCredential = {
  capability: string;
  /** 中文能力名由后端给：能配哪些能力也是后端定的，两边各维护一份必然分叉 */
  label: string;
  configured: boolean;
  provider_id: string;
  provider_label: string;
  /**
   * 形如 `sk-••••••••••••a91f`，只够认出"这是哪一把"。
   * **完整明文服务端永远不会返回**——存进去之后前端再也拿不到它。
   */
  masked_key: string | null;
  updated_at: string | null;
};

export type KeyTestResult = {
  ok: boolean;
  provider_id: string;
  /** 成功/失败的具体原因，后端已脱敏。失败时直接展示它，不要换成"连接失败" */
  message: string;
  error_code: string | null;
};

export const providerCredentials = {
  list: () => apiFetch<{ items: ProviderCredential[] }>("/provider-credentials"),

  /** 新增或更换。同一个能力只有一把 Key，PUT 就地覆盖。 */
  put: (capability: string, apiKey: string) =>
    apiFetch<ProviderCredential>(`/provider-credentials/${capability}`, {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey }),
    }),

  remove: (capability: string) =>
    apiFetch<void>(`/provider-credentials/${capability}`, { method: "DELETE" }),

  /** 不传 apiKey 就测已保存的那把——明文前端拿不到，只能让后端自己解。 */
  test: (capability: string, apiKey?: string) =>
    apiFetch<KeyTestResult>(`/provider-credentials/${capability}/test`, {
      method: "POST",
      body: JSON.stringify(apiKey ? { api_key: apiKey } : {}),
    }),
};

// ---------------------------------------------------------------- 模型目录（ADR-024）

export type ModelOption = {
  model_id: string;
  /** 档位名，例如「快速档」。后端给——有哪些模型是后端定的 */
  label: string;
  /**
   * 这一档的定位说明。**只讲模型本身（快 / 推理强 / 细节多），不讲价格**：
   * 价格在 `model_pricing` 表里，上游一调价，前端冻着的那句结论就是谎话。
   */
  note: string;
};

export type CapabilityModels = {
  capability: string;
  label: string;
  /** 平台真的接了这家。false 时 models 必然为空，界面要照实说"未接入" */
  available: boolean;
  provider_id: string | null;
  provider_label: string | null;
  /** Skill 的 model_policy.user_selectable 允不允许用户改这个能力 */
  user_selectable: boolean;
  models: ModelOption[];
  /** 没设偏好时 Gateway 先试的那个，用来标注「默认」 */
  default_model_id: string | null;
  /** available=false 时为什么。后端给的原话，前端不要自己编 */
  unavailable_reason: string | null;
};

export const modelCatalog = {
  /** 有哪些能力、每个能力能选哪几个模型。只读，选择动作落在项目上。 */
  list: () => apiFetch<{ items: CapabilityModels[] }>("/model-catalog"),
};

// ---------------------------------------------------------------- Skill（ADR-026）

export type OrgSkill = {
  id: string;
  name: string;
  version: string;
  /** valid | invalid。invalid 的照样返回，错误在 validation_errors 里 */
  status: string;
  validation_errors: string | null;
  created_at: string;

  /** 下面几个只有 status=valid 才有值 */
  skill_id: string | null;
  route: string | null;
  stage_count: number;
  gates: string[];
  /** spec 引用了但 Agent 注册表里没有的 id。不影响 status，只是提醒 */
  missing_agents: string[];

  /**
   * 恒为 false。ADR-026：本轮只做到"能传、能选"，**运行时没有接线**。
   * 界面必须照实说，不能做成一个看起来能用、实际不生效的功能。
   * 由后端给这个字段而不是前端写死文案——真接上了前端不用改。
   */
  runtime_wired: boolean;
};

export const orgSkills = {
  list: () => apiFetch<{ items: OrgSkill[] }>("/skills"),

  /**
   * 上传一份 Skill YAML。
   *
   * **校验不过也是 201**：记录建成功了，只是 `status=invalid`，
   * 错误原文在 `validation_errors` 里。真正的失败只有"根本不是 YAML"。
   */
  upload: (specYaml: string) =>
    apiFetch<OrgSkill>("/skills", {
      method: "POST",
      body: JSON.stringify({ spec_yaml: specYaml }),
    }),

  spec: (id: string) => apiFetch<{ id: string; spec_yaml: string }>(`/skills/${id}/spec`),

  remove: (id: string) => apiFetch<void>(`/skills/${id}`, { method: "DELETE" }),
};
