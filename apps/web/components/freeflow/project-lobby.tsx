"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  CirclePlus,
  Clapperboard,
  Film,
  Loader2,
  ShieldAlert,
} from "lucide-react";
import { GatePendingIcon, StaleIcon } from "@/components/icons/studio-icons";
import { ApiRequestError, projects, type Project } from "@/lib/api";
import { cn } from "@/lib/utils";

type Attention = { project: Project; kind: "approval" | "failed" | "stale"; label: string; href: string };

const STAGE: Record<string, string> = {
  draft: "草稿",
  routing: "规划中",
  producing: "生成中",
  review: "待确认",
  completed: "已完成",
  archived: "已归档",
};

/** 正在制作中的状态。首页的「继续制作」优先挑这几个。 */
const ACTIVE = new Set(["routing", "producing", "review"]);

export function ProjectLobby() {
  const [items, setItems] = useState<Project[] | null>(null);
  const [attention, setAttention] = useState<Attention[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let active = true;
    projects
      .list()
      .then(async ({ items: rows }) => {
        if (!active) return;
        setItems(rows);
        const examined = await Promise.all(
          rows.slice(0, 12).map(async (project) => {
            const [approvals, runs] = await Promise.all([
              projects.approvals(project.id).catch(() => []),
              projects.runs(project.id).catch(() => []),
            ]);
            const result: Attention[] = [];
            if (approvals.some((x) => x.status === "pending"))
              result.push({ project, kind: "approval", label: "等待审核确认", href: `/freeflow/projects/${project.id}/overview` });
            if (runs.some((x) => x.status === "failed"))
              result.push({ project, kind: "failed", label: "有失败的运行", href: `/freeflow/projects/${project.id}/tasks` });
            if (project.stale_roles.length)
              result.push({ project, kind: "stale", label: `${project.stale_roles.length} 项上游已变`, href: `/freeflow/projects/${project.id}/overview` });
            return result;
          }),
        );
        if (active) setAttention(examined.flat().slice(0, 6));
      })
      .catch((cause) => {
        if (!active) return;
        setError(cause instanceof ApiRequestError ? cause.error.user_message : "读取项目失败");
        setAttention([]);
      });
    return () => {
      active = false;
    };
  }, []);

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const title = String(new FormData(event.currentTarget).get("title") ?? "").trim();
    if (!title) return;
    setCreating(true);
    try {
      const project = await projects.create(title);
      window.location.assign(`/freeflow/projects/${project.id}/overview`);
    } catch (cause) {
      setError(cause instanceof ApiRequestError ? cause.error.user_message : "创建项目失败");
      setCreating(false);
    }
  }

  /**
   * 「继续制作」挑的是真在制作中的那一个；都不在制作中就退回最近建的一个。
   * 接口只给 `created_at`，没有 `updated_at`，所以这里不假装按"最后编辑"排序。
   */
  const continuing = useMemo(() => {
    if (!items || items.length === 0) return null;
    return items.find((p) => ACTIVE.has(p.status)) ?? items[0];
  }, [items]);

  const rest = useMemo(() => {
    if (!items) return [];
    return items.filter((p) => p.id !== continuing?.id);
  }, [items, continuing]);

  const hasProjects = (items?.length ?? 0) > 0;

  return (
    <div className="ff-lobby">
      <section className="ff-lobby-heading">
        <h1>{hasProjects ? "继续制作" : "开始第一个项目"}</h1>
        <button type="button" onClick={() => setOpen(true)} className="ff-primary-button">
          <CirclePlus aria-hidden className="size-4" />
          新建项目
        </button>
      </section>

      {error && (
        <p role="alert" className="rounded-sm border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger">
          {error}
        </p>
      )}

      {open && (
        <form onSubmit={create} className="ff-create-form" aria-label="新建项目">
          <label className="flex min-w-0 flex-col gap-2 text-sm font-medium text-fg">
            项目名称
            <input
              name="title"
              autoFocus
              required
              maxLength={200}
              placeholder="例如：第七夜"
              className="h-10 rounded-md border border-border-strong bg-bg px-3 text-sm font-normal text-fg placeholder:text-fg-subtle focus:border-primary"
            />
          </label>
          <button type="submit" disabled={creating} className="ff-primary-button mt-auto disabled:opacity-60">
            {creating && <Loader2 aria-hidden className="size-4 animate-spin" />}
            {creating ? "创建中" : "创建并进入"}
          </button>
          <button type="button" onClick={() => setOpen(false)} className="ff-quiet-button mt-auto">
            取消
          </button>
          <p className="text-xs text-fg-subtle sm:col-span-3">先起名字，进入项目后再放入小说原文。</p>
        </form>
      )}

      {items === null ? (
        <LobbyLoading />
      ) : !hasProjects ? (
        <EmptyProject onCreate={() => setOpen(true)} />
      ) : (
        <>
          <div className="ff-lobby-top">
            {continuing && <ContinueCard project={continuing} />}
            <AttentionLedger items={attention} />
          </div>

          {rest.length > 0 && (
            <section aria-labelledby="all-projects">
              <div className="ff-section-heading">
                <div className="flex items-baseline gap-2.5">
                  <h2 id="all-projects">项目片单</h2>
                  <span className="ff-count">{items.length}</span>
                </div>
              </div>
              <div className="ff-sheet">
                {rest.map((project) => (
                  <ProjectCard key={project.id} project={project} />
                ))}
                <button type="button" onClick={() => setOpen(true)} className="ff-new-project">
                  <span>
                    <CirclePlus aria-hidden className="size-5" />
                  </span>
                  <strong>新建项目</strong>
                  <p>从一份小说原文开始</p>
                </button>
              </div>
            </section>
          )}
        </>
      )}

      <footer className="ff-lobby-footer">
        <Film aria-hidden className="size-3.5" />
        <span>AIGC Studio 影像创作工作台</span>
      </footer>
    </div>
  );
}

/**
 * 继续制作。全页最大的一块画面，点进去直接到项目总览。
 *
 * 画框里现在放的是取景网格占位：项目列表接口不返回封面图，编一张缩略图
 * 就是假素材。等有了真实封面再往这里塞 `<img>`。
 */
function ContinueCard({ project }: { project: Project }) {
  const active = ACTIVE.has(project.status);
  return (
    <Link href={`/freeflow/projects/${project.id}/overview`} className="ff-continue">
      <div className="ff-continue-frame">
        <Clapperboard aria-hidden className="size-9" />
        <span className="ff-continue-badge">
          <span
            aria-hidden
            className={cn("size-1.5 rounded-full", active ? "bg-running" : "bg-fg-subtle")}
          />
          {active ? "制作中" : "最近打开"}
        </span>
      </div>
      <div className="ff-continue-body">
        <div className="min-w-0">
          <h3 className="truncate" title={project.title}>
            {project.title}
          </h3>
          <div className="ff-slate mt-2.5">
            <div className="ff-slate-field">
              <span className="ff-slate-key">阶段</span>
              <span className="ff-slate-value">{STAGE[project.status] ?? project.status}</span>
            </div>
            <div className="ff-slate-field">
              <span className="ff-slate-key">路线</span>
              <span className="ff-slate-value">{project.route_type ?? "等待判断"}</span>
            </div>
            <div className="ff-slate-field">
              <span className="ff-slate-key">已用 Credits</span>
              <span className="ff-slate-value" data-numeric="true">
                {project.spent_credits.toLocaleString("zh-CN")}
              </span>
            </div>
          </div>
        </div>
        <span className="ff-primary-button shrink-0">
          进入项目
          <ArrowRight aria-hidden className="size-4" />
        </span>
      </div>
    </Link>
  );
}

/** 需要处理。账式行，一行一件事，点进去就是那件事发生的地方。 */
function AttentionLedger({ items }: { items: Attention[] | null }) {
  return (
    <section className="ff-attention" aria-labelledby="attention">
      <h2 id="attention" className="ff-attention-head">
        <ShieldAlert aria-hidden className="size-4 text-running" />
        需要你处理
      </h2>
      {items === null ? (
        <p className="ff-attention-empty">正在检查…</p>
      ) : items.length === 0 ? (
        <p className="ff-attention-empty">没有待确认的门，也没有失败的运行。</p>
      ) : (
        items.map((item) => (
          <Link key={`${item.project.id}-${item.kind}`} href={item.href} className="ff-attention-item group">
            {item.kind === "stale" ? (
              <StaleIcon aria-hidden className="size-4 shrink-0 text-rf-agent" />
            ) : item.kind === "approval" ? (
              <GatePendingIcon aria-hidden className="size-4 shrink-0 text-running" />
            ) : (
              <AlertTriangle aria-hidden className="size-4 shrink-0 text-danger" />
            )}
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-fg">{item.project.title}</span>
              <span className="mt-0.5 block text-xs text-fg-subtle">{item.label}</span>
            </span>
            <ArrowRight aria-hidden className="size-3.5 shrink-0 text-fg-subtle group-hover:text-primary" />
          </Link>
        ))
      )}
    </section>
  );
}

function ProjectCard({ project }: { project: Project }) {
  const active = ACTIVE.has(project.status);
  return (
    <Link href={`/freeflow/projects/${project.id}/overview`} className="ff-project-card group">
      <div className="ff-project-cover">
        <Clapperboard aria-hidden className="size-7" />
        <span className={cn("ff-project-status", active ? "text-running" : "text-fg-muted")}>
          <span
            aria-hidden
            className={cn("size-1.5 rounded-full", active ? "bg-running" : "bg-fg-subtle")}
          />
          {STAGE[project.status] ?? project.status}
        </span>
      </div>
      <div className="ff-project-info">
        <h3 title={project.title}>{project.title}</h3>
        <div className="ff-project-meta">
          <span>{project.route_type ?? "等待路线判断"}</span>
          <span className="code shrink-0">
            {new Date(project.created_at).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })}
          </span>
        </div>
      </div>
    </Link>
  );
}

function LobbyLoading() {
  return (
    <div role="status" aria-label="正在加载项目" className="ff-lobby-top">
      <div className="ff-project-skeleton">
        <div className="rf-skeleton aspect-[16/7]" />
        <div className="p-4">
          <div className="rf-skeleton h-4 w-2/5 rounded-sm" />
          <div className="rf-skeleton mt-3 h-3 w-1/4 rounded-sm" />
        </div>
      </div>
      <div className="ff-project-skeleton">
        <div className="rf-skeleton m-4 h-4 w-1/3 rounded-sm" />
        <div className="rf-skeleton mx-4 mb-4 h-3 w-2/3 rounded-sm" />
      </div>
    </div>
  );
}

function EmptyProject({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="ff-lobby-empty">
      <div className="ff-empty-film" aria-hidden>
        <Clapperboard className="size-8" />
      </div>
      <h3>还没有项目</h3>
      <p>创建一个项目，放入小说原文。系统会依次产出剧本、角色档案、场景档案和分镜。</p>
      <button type="button" onClick={onCreate} className="ff-primary-button mt-6">
        <CirclePlus aria-hidden className="size-4" />
        新建项目
      </button>
    </div>
  );
}
