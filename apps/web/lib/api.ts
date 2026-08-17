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

export const projects = {
  list: () => apiFetch<{ items: Project[]; next_cursor: string | null }>("/projects?limit=50"),

  create: (title: string) =>
    apiFetch<Project>("/projects", { method: "POST", body: JSON.stringify({ title }) }),

  get: (id: string) => apiFetch<Project>(`/projects/${id}`),

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
