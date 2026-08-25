"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowRight, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { ApiRequestError, projects, type Project } from "@/lib/api";
import { creditsToYuan } from "@/lib/utils";
import { PageScroll } from "@/components/shell/page-scroll";

const STAGE_LABEL: Record<string, string> = {
  draft: "草稿",
  routing: "路线判断",
  producing: "生产中",
  review: "待确认",
  completed: "已完成",
  archived: "已归档",
};

export default function DashboardPage() {
  const router = useRouter();
  const [items, setItems] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    projects
      .list()
      .then((page) => setItems(page.items))
      .catch((e) => {
        // 未登录时直接送去登录页，不要留在这儿显示一个空列表
        if (e instanceof ApiRequestError && e.status === 401) {
          router.push("/login");
          return;
        }
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载失败");
      });
  }, [router]);

  async function onCreate(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const title = String(new FormData(form).get("title") ?? "").trim();
    if (!title) return;

    setCreating(true);
    try {
      const project = await projects.create(title);
      router.push(`/projects/${project.id}`);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.error.user_message : "创建失败");
      setCreating(false);
    }
  }

  return (
    <PageScroll>
      <div className="mx-auto flex max-w-[900px] flex-col gap-4">
        <Panel>
          <PanelHeader title="新建项目" />
          <form onSubmit={onCreate} className="flex items-end gap-3 p-3">
            <div className="flex-1">
              <Field
                label="项目名"
                name="title"
                placeholder="雾港迷案"
                required
                maxLength={200}
              />
            </div>
            <Button type="submit" variant="primary" disabled={creating}>
              <Plus aria-hidden className="size-3.5" />
              {creating ? "创建中…" : "创建"}
            </Button>
          </form>
        </Panel>

        {error && (
          <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}

        <Panel>
          <PanelHeader title="我的项目" meta={items ? `${items.length} 个` : undefined} />

          {items === null ? (
            <p className="px-3 py-8 text-center text-sm text-fg-subtle">加载中…</p>
          ) : items.length === 0 ? (
            <p className="px-3 py-8 text-center text-sm text-fg-subtle">
              还没有项目。上面建一个，然后输入你的创作需求。
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((p) => (
                <li key={p.id}>
                  <Link
                    href={`/projects/${p.id}`}
                    className="flex items-center gap-3 px-3 py-2.5 transition-colors duration-150 hover:bg-surface-2"
                  >
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">
                      {p.title}
                    </span>
                    <span className="shrink-0 text-xs text-fg-subtle">
                      {p.route_type ?? "未定路线"}
                    </span>
                    <span className="w-16 shrink-0 text-xs text-fg-muted">
                      {STAGE_LABEL[p.status] ?? p.status}
                    </span>
                    <span className="tnum w-16 shrink-0 text-right text-xs text-fg-subtle">
                      {p.spent_credits ? creditsToYuan(p.spent_credits) : "—"}
                    </span>
                    <ArrowRight aria-hidden className="size-3.5 shrink-0 text-fg-subtle" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </PageScroll>
  );
}
