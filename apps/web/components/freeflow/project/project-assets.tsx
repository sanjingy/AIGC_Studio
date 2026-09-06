"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { FileText, type LucideIcon } from "lucide-react";

import {
  AssetLibraryIcon,
  CharacterIcon,
  RenderImageIcon,
  SceneIcon,
  VideoIcon,
  VoiceIcon,
} from "@/components/icons/studio-icons";
import { RenderThumb } from "@/components/project/render-slot";
import { ApiRequestError, assets as assetsApi, type Library } from "@/lib/api";
import { cn, formatBytes } from "@/lib/utils";

/**
 * 项目内素材 tab（REQ-030 的项目内切片）。
 *
 * 比全局素材库简单：只列当前项目的东西，不做文件夹、不做上传、不做删除
 * ——那几件事属于全局素材库那一页（Worker A），做两份必然分叉。
 *
 * 项目筛选在**后端**做：`assets.library({ projectId })` 走
 * `/assets/library?project_id=`。以前是拿全量再前端筛，接口 limit 写死 100，
 * 项目多、素材多的时候这一页会漏——改回前端筛就会把那个 bug 一起改回来。
 *
 * 一个仍在的限制：角色/场景是结构化产出（ProfileEntry），不是文件，
 * 不占存储容量（REQ-031）。所以它们没有缩略图，只列条目。
 */

type Kind = "all" | "image" | "video" | "audio" | "character" | "scene";

const KINDS: { key: Kind; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "image", label: "图片" },
  { key: "video", label: "视频" },
  { key: "audio", label: "音频" },
  { key: "character", label: "角色" },
  { key: "scene", label: "场景" },
];

const FILE_ICON: Record<string, LucideIcon> = {
  image: RenderImageIcon,
  video: VideoIcon,
  audio: VoiceIcon,
};

export function ProjectAssets({ projectId }: { projectId: string }) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [kind, setKind] = useState<Kind>("all");

  const load = useCallback(async () => {
    try {
      setLibrary(await assetsApi.library({ projectId }));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "加载失败");
    }
  }, [projectId]);

  useEffect(() => {
    setLoading(true);
    void load().finally(() => setLoading(false));
  }, [load]);

  // 已经是这个项目的了，不再前端筛一遍。仍然过一层 useMemo：下面几个
  // 派生列表以它们为依赖，每次渲染换一个数组引用等于把 memo 白做。
  const files = useMemo(() => library?.assets ?? [], [library]);
  const profiles = useMemo(() => library?.profiles ?? [], [library]);

  // 角色/场景不是文件，选中它们时文件区整块空掉，由下面的档案区接手
  const visibleFiles = useMemo(() => {
    if (kind === "all") return files;
    if (kind === "character" || kind === "scene") return [];
    return files.filter((a) => a.type === kind);
  }, [files, kind]);

  const visibleProfiles = useMemo(() => {
    if (kind === "character") return profiles.filter((p) => p.kind === "characters");
    if (kind === "scene") return profiles.filter((p) => p.kind === "scenes");
    if (kind === "all") return profiles;
    return [];
  }, [profiles, kind]);

  return (
    <div className="mx-auto flex w-full max-w-[960px] flex-col gap-3 p-6">
      <div className="flex items-baseline gap-2">
        <h1 className="text-sm font-semibold text-fg">项目素材</h1>
        <span className="tnum text-xs text-fg-subtle">
          {files.length} 个文件 · {profiles.length} 份档案
        </span>
      </div>

      <div className="flex flex-wrap gap-1">
        {KINDS.map((k) => (
          <button
            key={k.key}
            type="button"
            aria-pressed={kind === k.key}
            onClick={() => setKind(k.key)}
            className={cn(
              "cursor-pointer rounded-lg px-3 py-1.5 text-xs transition-colors duration-150",
              kind === k.key
                ? "bg-primary-soft font-medium text-primary"
                : "text-fg-muted hover:bg-surface-2 hover:text-fg",
            )}
          >
            {k.label}
          </button>
        ))}
      </div>

      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {loading && <ProjectAssetsSkeleton />}

      {!loading && !error && visibleFiles.length === 0 && visibleProfiles.length === 0 && (
        <div className="rf-empty-state rounded-lg border border-dashed border-border-strong px-4 py-10 text-center">
          <span className="rf-empty-icon"><AssetLibraryIcon aria-hidden className="size-6" /></span>
          <p className="mt-2 text-sm text-fg-subtle">这个筛选下还没有素材。出图跑完之后会自动出现在这里。</p>
        </div>
      )}

      {visibleFiles.length > 0 && (
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {visibleFiles.map((a) => {
            const Icon = FILE_ICON[a.type] ?? FileText;
            return (
              <li
                key={a.id}
                className="rf-grid-card group overflow-hidden rounded-md border border-border transition-[border-color,box-shadow] duration-150 hover:border-border-strong hover:shadow-rf-card"
              >
                <div className="flex aspect-square items-center justify-center bg-surface-2">
                  {a.type === "image" ? (
                    <RenderThumb assetId={a.id} alt={a.filename} />
                  ) : (
                    <Icon aria-hidden className="size-7 text-fg-subtle transition-colors duration-150 group-hover:text-primary" />
                  )}
                </div>
                <div className="px-2 py-1.5">
                  <p className="truncate text-xs text-fg" title={a.filename}>
                    {a.filename}
                  </p>
                  <p className="tnum mt-0.5 text-xs text-fg-subtle">
                    {a.size_bytes === null ? "—" : formatBytes(a.size_bytes)}
                    {" · "}
                    {new Date(a.created_at).toLocaleDateString("zh-CN")}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {visibleProfiles.length > 0 && (
        <section className="flex flex-col gap-1.5">
          <h2 className="text-xs font-medium text-fg-subtle">
            结构化产出 · 不占存储容量（REQ-031）
          </h2>
          <ul className="overflow-hidden rounded-lg border border-border bg-surface">
            {visibleProfiles.map((p) => {
              const isChar = p.kind === "characters";
              const Icon = isChar ? CharacterIcon : SceneIcon;
              const count = isChar
                ? (p.output?.characters?.length ?? 0)
                : (p.output?.scenes?.length ?? 0);
              return (
                <li
                  key={p.run_id}
                  className="flex items-center gap-3 border-b border-border px-4 py-2.5 transition-colors duration-150 last:border-0 hover:bg-surface-2/55"
                >
                  <Icon aria-hidden className="size-4 shrink-0 text-fg-subtle" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-fg">{isChar ? "角色档案" : "场景档案"}</div>
                    <div className="tnum mt-0.5 text-xs text-fg-subtle">
                      {p.agent_id} · {count} 条 ·{" "}
                      {new Date(p.created_at).toLocaleDateString("zh-CN")}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}

function ProjectAssetsSkeleton() {
  return (
    <div role="status" aria-label="加载中" className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
      <span className="sr-only">加载中…</span>
      {[0, 1, 2, 3].map((item) => (
        <div key={item} className="overflow-hidden rounded-md border border-border bg-surface">
          <div className="rf-skeleton aspect-square" />
          <div className="space-y-2 p-2">
            <span className="rf-skeleton block h-3 w-3/4 rounded-sm" />
            <span className="rf-skeleton block h-2.5 w-1/2 rounded-sm" />
          </div>
        </div>
      ))}
    </div>
  );
}
