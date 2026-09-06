"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, CirclePlus, Loader2, PlayCircle, ShieldAlert } from "lucide-react";
import { GatePendingIcon, ProjectIcon, StaleIcon } from "@/components/icons/studio-icons";
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
  return <div className="mx-auto flex w-full max-w-[1560px] flex-col gap-6 p-4 lg:p-6 2xl:p-8">
    <section className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-end sm:justify-between">
      <div><p className="text-[10px] font-semibold tracking-[0.16em] text-fg-subtle uppercase">Workspace</p><h1 className="mt-1 text-xl font-semibold tracking-tight text-fg">继续创作</h1><p className="mt-1 text-sm text-fg-subtle">项目、运行和审核都保持在同一条真实工作流中。</p></div>
      <button type="button" onClick={() => setOpen(true)} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-fg transition-colors duration-150 hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"><CirclePlus aria-hidden className="size-4" />新建项目</button>
    </section>
    {error && <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
    {open && <form onSubmit={create} className="grid gap-3 border border-border bg-surface p-4 sm:grid-cols-[1fr_auto_auto]" aria-label="新建项目"><label className="flex flex-col gap-1 text-sm font-medium text-fg">项目名称<input name="title" autoFocus required maxLength={200} placeholder="为项目命名" className="h-10 rounded-md border border-border bg-bg px-3 text-sm font-normal text-fg outline-none placeholder:text-fg-subtle focus:border-primary" /></label><button type="submit" disabled={creating} className="mt-auto inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-fg disabled:opacity-60">{creating && <Loader2 aria-hidden className="size-4 animate-spin" />}{creating ? "创建中" : "创建并进入概览"}</button><button type="button" onClick={() => setOpen(false)} className="mt-auto h-10 rounded-md px-3 text-sm text-fg-muted hover:bg-surface-2">取消</button><p className="sm:col-span-3 text-xs text-fg-subtle">当前仅保存项目名称；来源、画幅和模板将在对应数据契约可用后加入。</p></form>}
    <section aria-labelledby="recent-projects"><div className="mb-3 flex items-center justify-between"><h2 id="recent-projects" className="text-base font-semibold text-fg">最近项目</h2><span className="tnum text-xs text-fg-subtle">{items?.length ?? 0} 个项目</span></div>{items === null ? <LobbyLoading /> : recent.length === 0 ? <EmptyProject onCreate={() => setOpen(true)} /> : <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">{recent.map((project) => <ProjectCard key={project.id} project={project} />)}</div>}</section>
    {attention.length > 0 && <section aria-labelledby="attention" className="border-t border-border pt-5"><div className="mb-3 flex items-center gap-2"><ShieldAlert aria-hidden className="size-4 text-running" /><h2 id="attention" className="text-base font-semibold text-fg">需要你处理</h2></div><div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-2 xl:grid-cols-3">{attention.map((item) => <Link key={`${item.project.id}-${item.kind}`} href={item.href} className="group flex min-h-20 items-center gap-3 bg-surface p-3 transition-colors duration-150 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-primary">{item.kind === "stale" ? <StaleIcon aria-hidden className="size-4 shrink-0 text-rf-agent" /> : item.kind === "approval" ? <GatePendingIcon aria-hidden className="size-4 shrink-0 text-running" /> : <AlertTriangle aria-hidden className="size-4 shrink-0 text-danger" />}<span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-fg">{item.project.title}</span><span className="block text-xs text-fg-subtle">{item.label}</span></span><ArrowRight aria-hidden className="size-4 text-fg-subtle group-hover:text-primary" /></Link>)}</div></section>}
  </div>;
}

function ProjectCard({ project }: { project: Project }) { const active = ["routing", "producing", "review"].includes(project.status); return <Link href={`/freeflow/projects/${project.id}/overview`} className="group flex min-h-40 flex-col rounded-lg border border-border bg-surface p-4 shadow-sm transition-[color,background-color,border-color,box-shadow] duration-150 hover:border-border-strong hover:bg-surface-2 hover:shadow-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"><div className="flex items-start justify-between gap-3"><span className="grid size-9 place-items-center rounded-md border border-primary/20 bg-primary-soft text-primary"><ProjectIcon aria-hidden className="size-5" /></span><span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs", active ? "border-running/20 bg-running-soft text-running" : "border-border bg-surface-2 text-fg-subtle")}><PlayCircle aria-hidden className="size-3" />{STAGE[project.status] ?? project.status}</span></div><h3 className="mt-auto truncate text-base font-semibold text-fg">{project.title}</h3><p className="mt-1 text-xs text-fg-subtle">创建于 {new Date(project.created_at).toLocaleDateString("zh-CN")}</p><div className="mt-3 flex items-center justify-between border-t border-border pt-2 text-xs text-fg-subtle"><span>{project.route_type ?? "等待路线判断"}</span><ArrowRight aria-hidden className="size-3.5 transition-transform duration-150 group-hover:translate-x-0.5 group-hover:text-primary motion-reduce:transform-none" /></div></Link>; }
function LobbyLoading() { return <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"><div className="h-40 border border-border bg-surface-2" /><div className="h-40 border border-border bg-surface-2" /><div className="h-40 border border-border bg-surface-2" /></div>; }
function EmptyProject({ onCreate }: { onCreate: () => void }) { return <div className="rf-empty-state flex min-h-56 flex-col items-center justify-center rounded-lg border border-dashed border-border-strong p-6 text-center"><span className="rf-empty-icon"><ProjectIcon aria-hidden className="size-6" /></span><h3 className="mt-3 text-base font-semibold text-fg">从一个项目开始</h3><p className="mt-1 max-w-sm text-sm text-fg-subtle">创建项目后，工作台会在这里显示真实的故事、审核和出图进度。</p><button type="button" onClick={onCreate} className="mt-4 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-fg">新建项目</button></div>; }
