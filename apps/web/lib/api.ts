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
  detail?: { errors?: { field: string; message: string; type: string }[] };
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

/**
 * 一次出图。提示词由后端用一致性引擎合成，前端不传也传不了——
 * 风格词必须由系统注入，这是画风一致性的唯一保障。
 */
export type Render = {
  task_id: string;
  subject_kind: "character" | "shot";
  /** 角色立绘才有 */
  subject_ref: string | null;
  /** 分镜出图才有 */
  shot_index: number | null;
  status: TaskStatus;
  progress: number;
  error_code: string | null;
  /** 出成功了才有。取图 URL 走 assets.downloadUrl，预签名链接存不住 */
  asset_id: string | null;
  created_at: string;
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

export const projects = {
  list: () => apiFetch<{ items: Project[]; next_cursor: string | null }>("/projects?limit=50"),

  create: (title: string) =>
    apiFetch<Project>("/projects", { method: "POST", body: JSON.stringify({ title }) }),

  get: (id: string) => apiFetch<Project>(`/projects/${id}`),

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

  resolve: (id: string, approvalId: string, decision: string, comment?: string) =>
    apiFetch<Advance>(`/projects/${id}/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({ decision, comment: comment || null }),
    }),

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

  /** 给一个镜号出图。同上，会扣 Credits。 */
  renderShot: (id: string, shotIndex: number) =>
    apiFetch<Task>(`/projects/${id}/images/shots/${shotIndex}`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    }),
};

export const tasks = {
  get: (id: string) => apiFetch<Task>(`/tasks/${id}`),

  /** 重试会重新预扣一笔，不是"免费再跑一次"。 */
  retry: (id: string) => apiFetch<Task>(`/tasks/${id}/retry`, { method: "POST" }),

  cancel: (id: string) => apiFetch<Task>(`/tasks/${id}/cancel`, { method: "POST" }),
};

export const credits = {
  balance: () => apiFetch<Balance>("/credits/balance"),

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
