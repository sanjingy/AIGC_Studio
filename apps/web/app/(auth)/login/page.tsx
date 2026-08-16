"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Clapperboard } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { ApiRequestError, auth } from "@/lib/api";

type Mode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [pending, setPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setFormError(null);
    setFieldErrors({});

    const data = new FormData(e.currentTarget);
    const email = String(data.get("email") ?? "");
    const password = String(data.get("password") ?? "");
    const displayName = String(data.get("display_name") ?? "");

    try {
      if (mode === "login") {
        await auth.login({ email, password });
      } else {
        await auth.register({ email, password, display_name: displayName });
      }
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiRequestError) {
        // 后端已经给了可直接展示的文案，不在前端另写一套
        setFormError(err.error.user_message);
        setFieldErrors(err.fieldErrors);
      } else {
        setFormError("网络异常，请检查连接后重试");
      }
      setPending(false);
    }
  }

  const isRegister = mode === "register";

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <div className="w-full max-w-[360px]">
        <div className="mb-6 flex items-center gap-2">
          <Clapperboard aria-hidden className="size-5 text-primary" />
          <span className="text-lg font-semibold tracking-tight">AIGC Studio</span>
        </div>

        <h1 className="text-xl font-semibold">
          {isRegister ? "创建账号" : "登录"}
        </h1>
        <p className="mt-1 text-sm text-fg-muted">
          {isRegister ? "注册后可以免费生成故事、角色和分镜。" : "继续你的项目。"}
        </p>

        <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-4">
          {isRegister && (
            <Field
              label="昵称"
              name="display_name"
              autoComplete="nickname"
              required
              maxLength={80}
              error={fieldErrors["display_name"]}
            />
          )}

          <Field
            label="邮箱"
            name="email"
            type="email"
            autoComplete="email"
            required
            error={fieldErrors["email"]}
          />

          <Field
            label="密码"
            name="password"
            type="password"
            autoComplete={isRegister ? "new-password" : "current-password"}
            required
            minLength={isRegister ? 8 : undefined}
            hint={isRegister ? "至少 8 位，需同时包含字母和数字" : undefined}
            error={fieldErrors["password"]}
          />

          {formError && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
              {formError}
            </p>
          )}

          <Button type="submit" variant="primary" disabled={pending}>
            {pending ? "处理中…" : isRegister ? "创建账号" : "登录"}
          </Button>
        </form>

        <p className="mt-4 text-sm text-fg-muted">
          {isRegister ? "已经有账号了？" : "还没有账号？"}
          <button
            type="button"
            onClick={() => {
              setMode(isRegister ? "login" : "register");
              setFormError(null);
              setFieldErrors({});
            }}
            className="ml-1 cursor-pointer font-medium text-primary hover:underline"
          >
            {isRegister ? "去登录" : "免费注册"}
          </button>
        </p>
      </div>
    </main>
  );
}
