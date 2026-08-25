"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Grid2x2, Sparkles, Upload, Workflow, type LucideIcon } from "lucide-react";

import { ApiRequestError, projects } from "@/lib/api";
import { QUICK_START_ITEMS } from "@/lib/freeflow/types";

/**
 * 01 首页「快速开始」四卡（REQ-010）。
 *
 * `QUICK_START_ITEMS` 在 `lib/freeflow/types.ts` 里，是公共契约，不改。
 * 图标那份映射放在这儿：它是这一屏的视觉选择，不该塞进类型文件。
 */
const ICON: Record<string, LucideIcon> = {
  director: Sparkles,
  canvas: Workflow,
  import: Upload,
  template: Grid2x2,
};

/**
 * `href` 为 null 的三项（自由画布模式 / 导入已有作品 / 模板中心）
 * 全部走同一条「建项目 → 进画布」的路径。
 *
 * **这是简化，不是设计意图。** 后端没有「项目类型」这个概念：
 * `projects.create()` 只收一个 title，`route_type` 由 Router Agent 在
 * 跑起来之后自己判，前端选不了。所以做不出「导入已有作品」和
 * 「从模板起手」这类差异化的初始流程——真要差异化，得先给 projects
 * 加一列并让编排器认它，那是 ADR 级别的改动。
 */
const FALLBACK_TITLE_PREFIX = "未命名项目";

export function HomeQuickStart() {
  const router = useRouter();
  const [creating, setCreating] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function startBlankProject(itemId: string) {
    if (creating) return;
    setCreating(itemId);
    setError(null);
    try {
      // 名字得当场编一个：这条路径没有让用户输名字的地方，
      // 而 create 必须带 title。带上时间是为了同一天点两次不撞名。
      const stamp = new Date().toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
      const project = await projects.create(`${FALLBACK_TITLE_PREFIX} ${stamp}`);
      router.push(`/freeflow/projects/${project.id}/canvas`);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "创建失败");
      setCreating(null);
    }
  }

  return (
    <section>
      <h2 className="mb-3 text-base font-semibold text-fg">快速开始</h2>

      {error && (
        <p role="alert" className="mb-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {QUICK_START_ITEMS.map((item) => {
          const Icon = ICON[item.id] ?? Sparkles;
          const body = (
            <>
              <Icon
                aria-hidden
                className={
                  item.id === "director"
                    ? "size-4 shrink-0 text-primary"
                    : "size-4 shrink-0 text-fg-muted"
                }
              />
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-fg">{item.title}</span>
                <span className="mt-0.5 block text-xs text-fg-subtle">
                  {creating === item.id ? "创建中…" : item.description}
                </span>
              </span>
            </>
          );

          const className =
            "flex items-start gap-2.5 rounded-lg border border-border bg-surface p-3.5 text-left transition-colors duration-150 hover:border-border-strong hover:bg-surface-2";

          return item.href ? (
            <Link key={item.id} href={item.href} className={className}>
              {body}
            </Link>
          ) : (
            <button
              key={item.id}
              type="button"
              disabled={creating !== null}
              onClick={() => void startBlankProject(item.id)}
              className={`${className} cursor-pointer disabled:pointer-events-none disabled:opacity-45`}
            >
              {body}
            </button>
          );
        })}
      </div>

      {/* 老实交代这三张卡目前是同一条路径，不然用户点完会以为自己选错了 */}
      <p className="mt-2 text-xs text-fg-subtle">
        「自由画布模式 / 导入已有作品 / 模板中心」目前都是新建一个空项目再进画布：
        后端还没有项目类型与预置节点图的概念，做不出差异化的起手流程。
      </p>
    </section>
  );
}
