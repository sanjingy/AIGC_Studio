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
