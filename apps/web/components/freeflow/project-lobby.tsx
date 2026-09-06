"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, CirclePlus, Clapperboard, Film, Loader2, PlayCircle, ShieldAlert } from "lucide-react";
import { GatePendingIcon, StaleIcon } from "@/components/icons/studio-icons";
import { ApiRequestError, projects, type Project } from "@/lib/api";
import { cn } from "@/lib/utils";

type Attention = { project: Project; kind: "approval" | "failed" | "stale"; label: string; href: string };
const STAGE: Record<string, string> = { draft: "草稿", routing: "规划中", producing: "生成中", review: "待确认", completed: "已完成", archived: "已归档" };

export function ProjectLobby() {
  const [items, setItems] = useState<Project[] | null>(null);
  const [attention, setAttention] = useState<Attention[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let active = true;
    projects.list().then(async ({ items: rows }) => {
      if (!active) return;
      setItems(rows);
      const examined = await Promise.all(rows.slice(0, 12).map(async (project) => {
        const [approvals, runs] = await Promise.all([projects.approvals(project.id).catch(() => []), projects.runs(project.id).catch(() => [])]);
        const result: Attention[] = [];
        if (approvals.some((x) => x.status === "pending")) result.push({ project, kind: "approval", label: "等待审核确认", href: `/freeflow/projects/${project.id}/overview` });
        if (runs.some((x) => x.status === "failed")) result.push({ project, kind: "failed", label: "有失败的运行", href: `/freeflow/projects/${project.id}/tasks` });
        if (project.stale_roles.length) result.push({ project, kind: "stale", label: `${project.stale_roles.length} 项上游已变`, href: `/freeflow/projects/${project.id}/overview` });
        return result;
      }));
      if (active) setAttention(examined.flat().slice(0, 6));
    }).catch((cause) => active && setError(cause instanceof ApiRequestError ? cause.error.user_message : "读取项目失败"));
    return () => { active = false; };
  }, []);

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const title = String(new FormData(event.currentTarget).get("title") ?? "").trim();
    if (!title) return;
    setCreating(true);
    try { const project = await projects.create(title); window.location.assign(`/freeflow/projects/${project.id}/overview`); }
    catch (cause) { setError(cause instanceof ApiRequestError ? cause.error.user_message : "创建项目失败"); setCreating(false); }
  }

  const recent = useMemo(() => items?.slice(0, 6) ?? [], [items]);
  return <div className="ff-lobby">
    <section className="ff-lobby-heading">
      <div><h1>继续你的创作</h1><p>从一个灵感，到每一帧画面。在这里展开你的故事。</p></div>
      <button type="button" onClick={() => setOpen(true)} className="ff-primary-button"><CirclePlus aria-hidden className="size-4" />新建项目</button>
    </section>
    {error && <p role="alert" className="rounded-lg border border-danger/20 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</p>}
    {open && <form onSubmit={create} className="ff-create-form" aria-label="新建项目">
      <label className="flex min-w-0 flex-col gap-2 text-sm font-medium text-fg">项目名称<input name="title" autoFocus required maxLength={200} placeholder="为你的新故事命名" className="h-11 rounded-lg border border-border-strong bg-bg px-3 text-sm font-normal text-fg placeholder:text-fg-subtle focus:border-primary" /></label>
      <button type="submit" disabled={creating} className="ff-primary-button mt-auto disabled:opacity-60">{creating && <Loader2 aria-hidden className="size-4 animate-spin" />}{creating ? "创建中" : "创建并进入项目"}</button>
      <button type="button" onClick={() => setOpen(false)} className="ff-quiet-button mt-auto">取消</button>
      <p className="text-xs text-fg-subtle sm:col-span-3">先起一个名字，进入项目后添加故事素材。</p>
    </form>}
    <section className="ff-project-section" aria-labelledby="recent-projects">
      <div className="ff-section-heading"><div className="flex items-center gap-3"><h2 id="recent-projects">最近项目</h2><span className="ff-count tnum">{items?.length ?? 0}</span></div><span className="hidden text-xs text-fg-subtle sm:inline">故事的下一帧，等你继续</span></div>
      {items === null ? <LobbyLoading /> : recent.length === 0 ? <EmptyProject onCreate={() => setOpen(true)} /> : <div className="ff-project-grid">{recent.map((project, index) => <ProjectCard key={project.id} project={project} index={index} />)}<button type="button" onClick={() => setOpen(true)} className="ff-new-project"><span><CirclePlus aria-hidden className="size-6" /></span><strong>开启新的故事</strong><p>创建一个新项目</p></button></div>}
    </section>
    {attention.length > 0 && <section aria-labelledby="attention" className="ff-attention"><div className="ff-section-heading"><div className="flex items-center gap-2"><ShieldAlert aria-hidden className="size-4 text-running" /><h2 id="attention">需要你处理</h2></div><span className="text-xs text-fg-subtle">继续之前，先看一眼</span></div><div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{attention.map((item) => <Link key={`${item.project.id}-${item.kind}`} href={item.href} className="ff-attention-item group">{item.kind === "stale" ? <StaleIcon aria-hidden className="size-5 shrink-0 text-rf-agent" /> : item.kind === "approval" ? <GatePendingIcon aria-hidden className="size-5 shrink-0 text-running" /> : <AlertTriangle aria-hidden className="size-5 shrink-0 text-danger" />}<span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-fg">{item.project.title}</span><span className="mt-1 block text-xs text-fg-subtle">{item.label}</span></span><ArrowRight aria-hidden className="size-4 text-fg-subtle group-hover:text-primary" /></Link>)}</div></section>}
    <footer className="ff-lobby-footer"><Film aria-hidden className="size-4" /><span>故事 · 角色 · 场景 · 分镜</span><span className="ml-auto hidden sm:inline">AIGC Studio</span></footer>
  </div>;
}

function ProjectCard({ project, index }: { project: Project; index: number }) {
  const active = ["routing", "producing", "review"].includes(project.status);
  return <Link href={`/freeflow/projects/${project.id}/overview`} className="ff-project-card group">
    <div className={cn("ff-project-cover", `ff-cover-${index % 3}`)}>
      <div className="ff-cover-frame" aria-hidden><Clapperboard className="size-8" /><span>{project.title.slice(0, 1)}</span></div>
      <span className="ff-cover-label"><Film aria-hidden className="size-3.5" />创作项目</span>
      <span className={cn("ff-project-status", active ? "text-running" : "text-fg-muted")}><PlayCircle aria-hidden className="size-3" />{STAGE[project.status] ?? project.status}</span>
    </div>
    <div className="ff-project-info"><h3 title={project.title}>{project.title}</h3><p>创建于 {new Date(project.created_at).toLocaleDateString("zh-CN")}</p><div className="ff-project-meta"><span>{project.route_type ?? "等待路线判断"}</span><span className="ff-project-enter">进入项目<ArrowRight aria-hidden className="size-3.5" /></span></div></div>
  </Link>;
}
function LobbyLoading() { return <div role="status" aria-label="正在加载项目" className="ff-project-grid">{[0, 1, 2].map((index) => <div key={index} className="ff-project-skeleton"><div className="rf-skeleton h-40" /><div className="p-5"><div className="rf-skeleton h-4 w-2/3 rounded" /><div className="rf-skeleton mt-3 h-3 w-1/3 rounded" /></div></div>)}</div>; }
function EmptyProject({ onCreate }: { onCreate: () => void }) { return <div className="ff-lobby-empty"><div className="ff-empty-film" aria-hidden><Clapperboard className="size-10" /></div><h3>你的第一部作品，从这里开始</h3><p>写下故事，设定角色，把想象变成镜头。</p><button type="button" onClick={onCreate} className="ff-primary-button mt-6"><CirclePlus aria-hidden className="size-4" />新建项目</button></div>; }
