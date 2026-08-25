"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Film, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiRequestError, projects, type Project } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 01 首页「最近项目」网格（REQ-010）。
 *
 * 数据走真实接口 `projects.list()`，取回的就是 `app/(app)/dashboard`
 * 那一份——两处显示同一批项目，不另开一套形状。
 */

/** 与 `app/(app)/dashboard/page.tsx` 的 STAGE_LABEL 一致。
 *  两处各写一份文案迟早分叉，但这是分支 B 的独立原型壳，
 *  抽公共文件要动 `lib/` 下的公共契约，本轮不碰。 */
const STAGE_LABEL: Record<string, string> = {
  draft: "草稿",
  routing: "路线判断",
  producing: "生产中",
  review: "待确认",
  completed: "已完成",
  archived: "已归档",
};

/** 六个后端状态归三档颜色：灰=还没开始，amber=在跑，green=收尾了。
 *  状态色只有这三档，多一档用户就得记颜色表了。 */
type Tone = "idle" | "running" | "done";

const STAGE_TONE: Record<string, Tone> = {
  draft: "idle",
  routing: "running",
  producing: "running",
  review: "running",
  completed: "done",
  archived: "done",
};

const TONE_CLASS: Record<Tone, string> = {
  idle: "border-border bg-surface-2 text-fg-subtle",
  running: "border-running/25 bg-running-soft text-running",
  done: "border-success/25 bg-success-soft text-success",
};

/** 首页只放最近这些，其余去「项目」页看。接口本身按 created_at 倒序返回。 */
const RECENT_LIMIT = 9;

export function HomeProjectGrid() {
  const router = useRouter();
  const [items, setItems] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    projects
      .list()
      .then((page) => setItems(page.items))
      .catch((e) => {
        // 未登录直接送去登录页，别留在这儿显示一个空网格
        if (e instanceof ApiRequestError && e.status === 401) {
          router.push("/login");
          return;
        }
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载失败");
      });
  }, [router]);

  return (
    <section>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-base font-semibold text-fg">最近项目</h2>
        <Link
          href="/freeflow/projects"
          className="text-xs text-primary transition-colors duration-150 hover:text-primary-hover"
        >
          全部项目 ›
        </Link>
      </div>

      {error && (
        <p role="alert" className="mb-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {/* align-items:start 是必须的：不设的话整行会被最矮的「新建项目」空卡
          拉齐，进行中卡片底部的进度条会被裁掉。 */}
      <div className="grid grid-cols-2 items-start gap-3 sm:grid-cols-3 xl:grid-cols-5">
        <NewProjectCard onError={setError} />

        {items === null
          ? null
          : items
              .slice(0, RECENT_LIMIT)
              .map((p) => <ProjectCard key={p.id} project={p} />)}
      </div>

      {items !== null && items.length === 0 && (
        <p className="mt-3 text-sm text-fg-subtle">
          还没有项目。用左边那张卡建一个，进去就是空画布。
        </p>
      )}
      {items === null && !error && <p className="mt-3 text-sm text-fg-subtle">加载中…</p>}
    </section>
  );
}

function ProjectCard({ project }: { project: Project }) {
  const tone = STAGE_TONE[project.status] ?? "idle";

  return (
    <Link
      href={`/freeflow/projects/${project.id}/canvas`}
      className="flex flex-col overflow-hidden rounded-lg border border-border bg-surface transition-colors duration-150 hover:border-border-strong"
    >
      {/* 封面占位：项目还没有封面图这个概念，纯色块比一张假缩略图诚实 */}
      <div className="flex aspect-[4/3] items-center justify-center bg-surface-3">
        <Film aria-hidden className="size-6 text-fg-subtle" />
      </div>

      <div className="px-2.5 py-2">
        <div className="truncate text-xs font-semibold text-fg" title={project.title}>
          {project.title}
        </div>
        {/* 后端只有 created_at，没有 updated_at。标「更新于」是编的。 */}
        <div className="tnum mt-0.5 text-xs text-fg-subtle">
          创建于 {new Date(project.created_at).toLocaleDateString("zh-CN")}
        </div>

        <div className="mt-1.5 flex items-center gap-1.5">
          <span
            className={cn(
              "inline-flex shrink-0 items-center rounded-full border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
              TONE_CLASS[tone],
            )}
          >
            {STAGE_LABEL[project.status] ?? project.status}
          </span>
          {tone === "running" && <StageProgressPlaceholder />}
        </div>
      </div>
    </Link>
  );
}

/**
 * 阶段进度条占位。
 *
 * **这里的宽度是写死的 40%，不是任何计算结果。** 后端 `projects` 接口
 * 没有阶段进度百分比字段，`orchestrator._NEXT` 的阶段序号也不等于进度
 * （每个阶段耗时差一个数量级）。真接线时要后端给一个百分比，
 * 不要在前端按阶段下标除以总阶段数编一个出来。
 *
 * 因为不表示真实进度，所以不给 role="progressbar"——读屏念出一个假的
 * 40% 比没有更糟。
 */
function StageProgressPlaceholder() {
  return (
    <div
      aria-hidden
      title="进度百分比后端还没有，这条是固定宽度的视觉占位"
      className="h-[3px] min-w-0 flex-1 overflow-hidden rounded-full bg-surface-2"
    >
      <div className="h-full w-2/5 rounded-full bg-primary" />
    </div>
  );
}

/**
 * 「新建项目」卡。空态是一张虚线卡，点开才展开表单——首页第一屏
 * 摆一个常驻输入框会把「最近项目」挤下去。
 *
 * 表单本身复刻 `app/(app)/dashboard/page.tsx`：同一个 `projects.create()`，
 * 同一套错误处理，只是建完跳到分支 B 的画布而不是主线项目页。
 */
function NewProjectCard({ onError }: { onError: (msg: string | null) => void }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);

  async function onCreate(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const title = String(new FormData(e.currentTarget).get("title") ?? "").trim();
    if (!title) return;

    setCreating(true);
    onError(null);
    try {
      const project = await projects.create(title);
      router.push(`/freeflow/projects/${project.id}/canvas`);
    } catch (err) {
      onError(err instanceof ApiRequestError ? err.error.user_message : "创建失败");
      setCreating(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex aspect-[4/3] cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-border-strong bg-surface text-fg-subtle transition-colors duration-150 hover:border-primary hover:text-primary"
      >
        <Plus aria-hidden className="size-5" />
        <span className="text-xs">新建项目</span>
      </button>
    );
  }

  return (
    <form
      onSubmit={onCreate}
      className="flex flex-col gap-2 rounded-lg border border-border-strong bg-surface p-2.5"
    >
      <label htmlFor="freeflow-new-project" className="text-xs font-medium text-fg">
        项目名
      </label>
      <input
        id="freeflow-new-project"
        name="title"
        autoFocus
        required
        maxLength={200}
        placeholder="雾港迷案"
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
        className="h-8 rounded-md border border-border-strong bg-surface px-2 text-sm text-fg transition-colors duration-150 placeholder:text-fg-subtle hover:border-fg-subtle"
      />
      <div className="flex items-center gap-1.5">
        <Button type="submit" size="sm" variant="primary" disabled={creating}>
          {creating ? "创建中…" : "创建"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)} disabled={creating}>
          取消
        </Button>
      </div>
    </form>
  );
}
