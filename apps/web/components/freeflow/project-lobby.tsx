"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  CirclePlus,
  Clapperboard,
  Film,
  ShieldAlert,
} from "lucide-react";
import { StartComposer } from "@/components/freeflow/home/start-composer";
import { GatePendingIcon, StaleIcon } from "@/components/icons/studio-icons";
import { ApiRequestError, projects, type Project } from "@/lib/api";
import { formatEditedAt, sortByRecentEdit } from "@/lib/freeflow/project-recency";
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

/** 正在制作中的状态，只用来给卡片上色。 */
const ACTIVE = new Set(["routing", "producing", "review"]);

/** 首页最多列几个近期项目，其余去「我的项目」。 */
const HOME_RECENT = 8;

/**
 * 项目列表，按最近编辑（`updated_at`）倒序。后端按创建时间分页、前端取 50 条，
 * 所以这是「这 50 条里最近编辑的」，见 `project-recency.ts`。
 */
function useRecentProjects() {
  const [items, setItems] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    projects
      .list()
      .then(({ items: rows }) => alive && setItems(sortByRecentEdit(rows)))
      .catch((cause) => {
        if (!alive) return;
        setError(cause instanceof ApiRequestError ? cause.error.user_message : "读取项目失败");
        setItems([]);
      });
    return () => {
      alive = false;
    };
  }, []);
  return { items, error };
}

/** 逐个项目查门与失败的运行。只看最近编辑的前 12 个，免得一次发几十个请求。 */
function useAttention(items: Project[] | null): Attention[] | null {
  const [attention, setAttention] = useState<Attention[] | null>(null);
  useEffect(() => {
    if (items === null) return;
    let alive = true;
    void Promise.all(
      items.slice(0, 12).map(async (project) => {
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
    ).then((rows) => alive && setAttention(rows.flat().slice(0, 6)));
    return () => {
      alive = false;
    };
  }, [items]);
  return attention;
}

/**
 * 创作首页：创作输入 → 继续制作 + 需要你处理 → 近期项目。
 *
 * 「继续制作」就是最近编辑的那一个，与下面的排序同一口径。
 */
export function ProjectHome() {
  const { items, error } = useRecentProjects();
  const attention = useAttention(items);
  const continuing = items?.[0] ?? null;
  const recent = useMemo(() => (items ?? []).slice(1, 1 + HOME_RECENT), [items]);
  const more = (items?.length ?? 0) - 1 - recent.length;

  return (
    <div className="ff-lobby">
      <StartComposer />

      {error && (
        <p role="alert" className="rounded-sm border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger">
          项目列表读取失败：{error}
        </p>
      )}

      {items === null ? (
        <LobbyLoading />
      ) : items.length === 0 ? (
        !error && <p className="text-sm text-fg-subtle">还没有项目。填好上面的项目名就能开始。</p>
      ) : (
        <>
          <div className="ff-lobby-top">
            {continuing && <ContinueCard project={continuing} />}
            <AttentionLedger items={attention} />
          </div>

          {recent.length > 0 && (
            <section aria-labelledby="recent-projects">
              <div className="ff-section-heading">
                <div className="flex items-baseline gap-2.5">
                  <h2 id="recent-projects">近期项目</h2>
                  <span className="text-xs text-fg-subtle">按最近编辑</span>
                </div>
                <Link href="/freeflow/projects" className="text-xs text-fg-muted hover:text-primary">
                  全部项目{more > 0 ? `（还有 ${more} 个）` : ""}
                </Link>
              </div>
              <div className="ff-sheet">
                {recent.map((project) => (
                  <ProjectCard key={project.id} project={project} />
                ))}
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

/** 我的项目：同一排序的完整片单。新建回首页的创作输入，那里才分得清两个动作。 */
export function ProjectList() {
  const { items, error } = useRecentProjects();
  return (
    <div className="ff-lobby">
      <section className="ff-lobby-heading">
        <div>
          <h1>我的项目</h1>
          <p>按最近编辑排序，最近动过的在前。</p>
        </div>
        <Link href="/freeflow#new" className="ff-primary-button">
          <CirclePlus aria-hidden className="size-4" />
          新建项目
        </Link>
      </section>

      {error && (
        <p role="alert" className="rounded-sm border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger">
          项目列表读取失败：{error}
        </p>
      )}

      {items === null ? (
        <LobbyLoading />
      ) : items.length === 0 ? (
        !error && <EmptyProject />
      ) : (
        <section aria-labelledby="all-projects">
          <div className="ff-section-heading">
            <div className="flex items-baseline gap-2.5">
              <h2 id="all-projects">全部项目</h2>
              <span className="ff-count">{items.length}</span>
            </div>
          </div>
          <div className="ff-sheet">
            {items.map((project) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

/**
 * 继续制作：最近编辑的那个项目。全页最大的一块画面，点进去直接到项目总览。
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
          {active ? "制作中" : STAGE[project.status] ?? project.status}
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
              <span className="ff-slate-key">最近编辑</span>
              <span className="ff-slate-value" data-numeric="true">
                <time dateTime={project.updated_at}>{formatEditedAt(project.updated_at)}</time>
              </span>
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
          <span className="code shrink-0" title="最近编辑">
            <span className="sr-only">最近编辑 </span>
            <time dateTime={project.updated_at}>{formatEditedAt(project.updated_at)}</time>
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

function EmptyProject() {
  return (
    <div className="ff-lobby-empty">
      <div className="ff-empty-film" aria-hidden>
        <Clapperboard className="size-8" />
      </div>
      <h3>还没有项目</h3>
      <p>创建一个项目，放入小说原文。系统会依次产出剧本、角色档案、场景档案和分镜。</p>
      <Link href="/freeflow#new" className="ff-primary-button mt-6">
        <CirclePlus aria-hidden className="size-4" />
        新建项目
      </Link>
    </div>
  );
}
