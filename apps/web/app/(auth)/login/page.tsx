"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { StudioMarkIcon } from "@/components/icons/studio-icons";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { ApiRequestError, auth } from "@/lib/api";

type Mode = "login" | "register";

/**
 * 登录页的「介绍」不是一段标语，而是这条生产线本身：
 * 五格胶片连续带列出产品真实会产出的东西，顺序与后端阶段图一致。
 * 新用户看一眼就知道这里能做什么，不需要一句形容词。
 *
 * 视频合成还没做（M2），所以带子里没有这一格——列出来就是假承诺。
 */
const PIPELINE = ["剧本", "角色档案", "场景档案", "分镜", "逐镜出图"];

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  // 会话过期被踢回来时带着原来的页面，登录后回到那儿而不是工作台首页。
  // 只接受站内相对路径——把 next 直接当 URL 用就是开放重定向。
  const rawNext = params.get("next") ?? "";
  const next = rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/freeflow";
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
      router.push(next);
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
    <main className="theme-reelflow grid min-h-dvh grid-cols-1 lg:grid-cols-[minmax(0,1fr)_480px]">
      {/* 左：产品身份。只在 lg 以上出现——窄屏第一位的是输入框。 */}
      <section className="hidden min-w-0 flex-col justify-between border-r border-border bg-surface p-10 lg:flex xl:p-14">
        <div className="flex items-center gap-2.5">
          <span className="grid size-8 place-items-center rounded-[2px] border border-border-strong bg-surface-2 text-primary">
            <StudioMarkIcon aria-hidden className="size-5" />
          </span>
          <span className="text-[15px] font-[650] tracking-[-0.03em]">AIGC Studio</span>
        </div>

        <div className="min-w-0">
          <h1 className="max-w-[16ch] text-[clamp(28px,3vw,42px)] leading-[1.15] font-[620] tracking-[-0.035em]">
            把小说做成逐镜画面
          </h1>
          <p className="mt-4 max-w-[46ch] text-sm leading-relaxed text-fg-muted">
            放进一份小说原文，工作台依次产出剧本、角色档案、场景档案和分镜，
            并按锁定的风格逐镜出图。每一步都要你确认后才继续。
          </p>

          <div className="ff-strip ff-strip-scroll mt-9 max-w-[560px]">
            <ol className="ff-strip-track">
              {PIPELINE.map((label, index) => (
                <li key={label} className="ff-frame" data-state={index === 0 ? "active" : "ready"}>
                  <span className="ff-frame-no">{String(index + 1).padStart(2, "0")}</span>
                  <span className="ff-frame-name">{label}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>

        <p className="text-xs text-fg-subtle">视频合成与配音在 M2，尚未接入。</p>
      </section>

      {/* 右：表单 */}
      <section className="flex min-w-0 items-center justify-center bg-bg p-6">
        <div className="w-full max-w-[360px]">
          <div className="mb-7 flex items-center gap-2.5 lg:hidden">
            <span className="grid size-8 place-items-center rounded-[2px] border border-border-strong bg-surface-2 text-primary">
              <StudioMarkIcon aria-hidden className="size-5" />
            </span>
            <span className="text-[15px] font-[650] tracking-[-0.03em]">AIGC Studio</span>
          </div>

          <h2 className="text-xl font-semibold tracking-[-0.02em]">
            {isRegister ? "创建账号" : "登录"}
          </h2>
          <p className="mt-1.5 text-sm text-fg-muted">
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
              <p role="alert" className="rounded-[2px] border border-danger/25 bg-danger-soft px-3 py-2 text-sm text-danger">
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
      </section>
    </main>
  );
}

export default function LoginPage() {
  // useSearchParams 要求包在 Suspense 里，否则整页会退化成客户端渲染
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
