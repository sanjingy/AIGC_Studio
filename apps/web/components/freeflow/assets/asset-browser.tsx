"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { FileText, Loader2, Puzzle, Search, Upload, type LucideIcon } from "lucide-react";

import { AssetPreview } from "@/components/freeflow/assets/asset-preview";
import { ProfileDetail, type ProfileTarget } from "@/components/freeflow/assets/profile-detail";
import {
  AssetLibraryIcon,
  CharacterIcon,
  RenderImageIcon,
  SceneIcon,
  VideoIcon,
  VoiceIcon,
} from "@/components/icons/studio-icons";
import { SKILLS_CHANGED, useSkillUpload } from "@/components/project/skill-upload";
import { Button } from "@/components/ui/button";
import {
  ApiRequestError,
  assets as assetsApi,
  orgSkills,
  projects,
  UPLOAD_ACCEPT,
  type CharacterEntry,
  type Library,
  type LibraryAsset,
  type OrgSkill,
  type ProfileEntry,
} from "@/lib/api";
import {
  FILTER_HINT,
  FILTER_LABEL,
  appendPage,
  filtersFor,
  matchesQuery,
  normalizeLibrary,
  profileNames,
  serverType,
  showsCharacters,
  showsFiles,
  showsScenes,
  sourceLabel,
  type AssetFilter,
  type LibraryData,
} from "@/lib/freeflow/asset-scope";
import { cn, formatBytes } from "@/lib/utils";

/**
 * 资产浏览：全局资产库与项目内资产共用这一个组件，只是范围不同。
 *
 * - 不传 `projectId` = 全局：可在「全部项目 / 某个项目」之间切换，含独立角色档案与 Skill。
 * - 传 `projectId` = 项目内：范围固定，不列 Skill（组织级）与独立角色档案（不挂项目）。
 *
 * 筛选、搜索、预览的规则在 `lib/freeflow/asset-scope.ts`。数据只走
 * `/assets/library`（文件 + 每个项目最近一次的角色/场景档案 + 独立角色档案）与 `/skills`。
 * 文件类型筛选交给后端 `type`；搜索只在已载入的结果里找，翻页用后端 `cursor`。
 */

/** 用量到这个百分比就开始视觉预警。 */
const WARN_AT = 90;

const TYPE_ICON: Record<string, LucideIcon> = {
  image: RenderImageIcon,
  video: VideoIcon,
  audio: VoiceIcon,
};

type UploadState =
  | { phase: "running"; done: number; total: number; current: string }
  | { phase: "done"; count: number }
  | { phase: "error"; done: number; total: number; name: string; message: string };

export function AssetBrowser({ projectId }: { projectId?: string }) {
  const fixed = projectId !== undefined;
  const [filter, setFilter] = useState<AssetFilter>("all");
  const [scopeProject, setScopeProject] = useState<string | null>(projectId ?? null);
  const [query, setQuery] = useState("");
  const [data, setData] = useState<LibraryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [skills, setSkills] = useState<OrgSkill[] | null>(null);
  const [projectRows, setProjectRows] = useState<{ id: string; title: string }[] | null>(null);
  const [preview, setPreview] = useState<LibraryAsset | null>(null);
  const [detail, setDetail] = useState<ProfileTarget | null>(null);
  const skillUpload = useSkillUpload();

  // 项目名：给「所属项目」和范围下拉用。读不到时只是退回「不在项目列表中」，不挡资产列表。
  useEffect(() => {
    let alive = true;
    projects
      .list()
      .then(({ items }) => alive && setProjectRows(items.map((p) => ({ id: p.id, title: p.title }))))
      .catch(() => alive && setProjectRows([]));
    return () => {
      alive = false;
    };
  }, []);
  const titles = useMemo(() => new Map((projectRows ?? []).map((p) => [p.id, p.title])), [projectRows]);

  const type = serverType(filter);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    assetsApi
      .library({ type, projectId: scopeProject ?? undefined })
      .then((d) => {
        if (!alive) return;
        setData(normalizeLibrary(d));
        setError(null);
      })
      .catch((e) => {
        if (!alive) return;
        setData(null);
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载资产失败");
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [type, scopeProject, reloadKey]);

  useEffect(() => {
    if (filter !== "skill" || skills !== null) return;
    let alive = true;
    orgSkills
      .list()
      .then((r) => alive && setSkills(r.items))
      .catch((e) => {
        if (!alive) return;
        setSkills([]);
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载 Skill 列表失败");
      });
    return () => {
      alive = false;
    };
  }, [filter, skills]);

  useEffect(() => {
    const onChanged = () => setSkills(null);
    window.addEventListener(SKILLS_CHANGED, onChanged);
    return () => window.removeEventListener(SKILLS_CHANGED, onChanged);
  }, []);

  async function loadMore() {
    if (!data?.next_cursor) return;
    setLoadingMore(true);
    try {
      const next = normalizeLibrary(
        await assetsApi.library({
          type,
          projectId: scopeProject ?? undefined,
          cursor: data.next_cursor,
        }),
      );
      setData((prev) =>
        prev ? { ...prev, assets: appendPage(prev.assets, next.assets), next_cursor: next.next_cursor } : next,
      );
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "加载更多失败");
    } finally {
      setLoadingMore(false);
    }
  }

  const files: LibraryAsset[] = useMemo(
    () =>
      data && showsFiles(filter)
        ? data.assets.filter((a) =>
            matchesQuery(query, a.filename, sourceLabel(a.project_id, titles), FILTER_LABEL[a.type as AssetFilter]),
          )
        : [],
    [data, filter, query, titles],
  );
  const entries: CharacterEntry[] = useMemo(
    () =>
      data && showsCharacters(filter) && !scopeProject
        ? data.characters.filter((c) =>
            matchesQuery(query, c.title, "独立角色档案", ...profileNames(c.output, "characters")),
          )
        : [],
    [data, filter, query, scopeProject],
  );
  const profiles: ProfileEntry[] = useMemo(
    () =>
      data
        ? data.profiles.filter(
            (p) =>
              ((p.kind === "characters" && showsCharacters(filter)) || (p.kind === "scenes" && showsScenes(filter))) &&
              matchesQuery(
                query,
                p.project_title,
                p.kind === "characters" ? "角色档案" : "场景档案",
                ...profileNames(p.output, p.kind),
              ),
          )
        : [],
    [data, filter, query],
  );
  const skillItems: OrgSkill[] = filter === "skill" ? (skills ?? []).filter((k) => matchesQuery(query, k.name)) : [];

  const plates = files.filter((a) => a.type === "image");
  const otherFiles = files.filter((a) => a.type !== "image");
  const dockets = otherFiles.length + entries.length + profiles.length + skillItems.length;
  const total = files.length + entries.length + profiles.length + skillItems.length;
  const searching = query.trim() !== "";
  const scopeTitle = scopeProject ? (titles.get(scopeProject) ?? "这个项目") : null;
  const showMore = showsFiles(filter) && Boolean(data?.next_cursor);

  return (
    <div className={cn("flex w-full flex-col", fixed ? "mx-auto max-w-[1200px] p-4 sm:p-6" : "ff-page max-w-[1200px]")}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-base font-semibold text-fg">{fixed ? "项目素材" : "资产库"}</h1>
          <p className="mt-1 text-xs leading-5 text-fg-subtle">
            {scopeProject
              ? `只列「${scopeTitle}」的文件与档案。独立角色档案和 Skill 不属于任何项目，在资产库「全部项目」里看。`
              : "你在各项目里上传和生成的文件、没挂项目的上传、各项目的角色与场景档案，以及独立角色档案。"}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {fixed && (
            <Link href="/freeflow/assets" className="ff-quiet-button">
              <AssetLibraryIcon aria-hidden className="size-4" />
              去资产库看全部
            </Link>
          )}
          {filter === "skill" ? (
            <Button size="sm" disabled={skillUpload.busy} onClick={skillUpload.pick}>
              {skillUpload.busy ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : <Upload aria-hidden className="size-3.5" />}
              上传 Skill
            </Button>
          ) : (
            <UploadControl
              projectId={scopeProject}
              label={scopeProject ? (fixed ? "上传到本项目" : `上传到「${scopeTitle}」`) : "上传到资产库"}
              onUploaded={() => setReloadKey((n) => n + 1)}
            />
          )}
        </div>
      </div>
      {skillUpload.input}

      {data?.usage && <UsageLine usage={data.usage} />}

      <div className="mt-3 flex flex-col gap-2 md:flex-row md:items-center">
        {!fixed && (
          <label className="flex min-w-0 items-center gap-2 text-xs text-fg-muted md:w-60">
            <span className="shrink-0">范围</span>
            <select
              value={scopeProject ?? ""}
              onChange={(e) => {
                setScopeProject(e.target.value || null);
                if (e.target.value && filter === "skill") setFilter("all");
              }}
              className="h-8 min-w-0 flex-1 cursor-pointer rounded-md border border-border-strong bg-surface px-2 text-xs text-fg"
            >
              <option value="">全部项目</option>
              {(projectRows ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="relative flex min-w-0 flex-1 items-center">
          <span className="sr-only">搜索资产</span>
          <Search aria-hidden className="pointer-events-none absolute left-2.5 size-3.5 text-fg-subtle" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="按文件名、项目名、角色或场景名搜索"
            className="h-8 w-full min-w-0 rounded-md border border-border-strong bg-surface pr-2 pl-8 text-xs text-fg placeholder:text-fg-subtle focus:border-primary"
          />
        </label>
      </div>

      <div role="group" aria-label="按类型筛选" className="mt-2 flex flex-wrap gap-1.5">
        {filtersFor(scopeProject ? "project" : "global").map((k) => (
          <button
            key={k}
            type="button"
            aria-pressed={filter === k}
            title={FILTER_HINT[k]}
            onClick={() => setFilter(k)}
            className={cn(
              "inline-flex h-7 cursor-pointer items-center gap-1 rounded-[2px] px-2.5 text-xs transition-colors duration-150",
              filter === k
                ? "bg-primary font-medium text-primary-fg"
                : "bg-surface-2 text-fg-muted hover:bg-surface-3 hover:text-fg",
            )}
          >
            {FILTER_LABEL[k]}
            {FILTER_HINT[k] && filter === k && <span className="font-normal opacity-80">（{FILTER_HINT[k]}）</span>}
          </button>
        ))}
      </div>

      {error && (
        <p role="alert" className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {filter === "skill" && skillUpload.notice && <div className="mt-3">{skillUpload.notice}</div>}

      {(loading && !data) || (filter === "skill" && skills === null) ? (
        <AssetSkeleton />
      ) : !data && error ? null : total === 0 ? (
        filter === "skill" && !searching ? (
          <SkillEmptyState onPick={skillUpload.pick} busy={skillUpload.busy} />
        ) : (
          <EmptyState>
            {searching
              ? `没有找到和「${query.trim()}」相关的${filter === "all" ? "资产" : FILTER_LABEL[filter]}。`
              : emptyText(filter, scopeProject !== null)}
            {searching && showMore && " 还有更早的文件没有载入，可以先加载更多再搜。"}
          </EmptyState>
        )
      ) : (
        <>
          <p className="tnum mt-4 mb-2 text-xs text-fg-subtle" aria-live="polite">
            {[
              files.length > 0 && `${files.length} 个文件`,
              profiles.length > 0 && `${profiles.length} 份项目档案`,
              entries.length > 0 && `${entries.length} 份独立角色档案`,
              skillItems.length > 0 && `${skillItems.length} 份 Skill`,
            ]
              .filter(Boolean)
              .join("，")}
            {searching && "（搜索结果）"}
            {loading && "，正在刷新…"}
          </p>
          {filter === "skill" && skillItems.some((k) => !k.runtime_wired) && (
            <p className="mb-2 rounded-[2px] bg-surface-2 px-3 py-2 text-xs text-fg-subtle">
              这些 Skill 已存入技能库，但<span className="text-fg-muted">运行时尚未接线</span>
              ——当前生产流程仍走内置阶段图，它们暂时不会生效。
            </p>
          )}

          {plates.length > 0 && (
            <div className="ff-lighttable">
              {plates.map((a) => (
                <FilePlate key={a.id} asset={a} source={scopeProject ? null : sourceLabel(a.project_id, titles)} onOpen={() => setPreview(a)} />
              ))}
            </div>
          )}

          {dockets > 0 && (
            <div className={cn("ff-dockets", plates.length > 0 && "mt-4")}>
              {otherFiles.map((a) => (
                <Docket
                  key={a.id}
                  kind="file"
                  icon={TYPE_ICON[a.type] ?? FileText}
                  title={a.filename}
                  meta={[
                    FILTER_LABEL[a.type as AssetFilter] ?? a.type,
                    a.size_bytes === null ? "大小未知" : formatBytes(a.size_bytes),
                    // 范围已经是某个项目时不再逐条重复项目名
                    scopeProject ? null : sourceLabel(a.project_id, titles),
                  ]
                    .filter(Boolean)
                    .join("，")}
                  date={a.created_at}
                  onOpen={() => setPreview(a)}
                />
              ))}
              {profiles.map((p) => (
                <Docket
                  key={p.run_id}
                  kind={p.kind === "characters" ? "character" : "scene"}
                  icon={p.kind === "characters" ? CharacterIcon : SceneIcon}
                  title={scopeProject ? (p.kind === "characters" ? "角色档案" : "场景档案") : `${p.project_title}：${p.kind === "characters" ? "角色档案" : "场景档案"}`}
                  meta={countText(p.output, p.kind)}
                  date={p.created_at}
                  onOpen={() => setDetail({ kind: "profile", profile: p })}
                />
              ))}
              {entries.map((c) => (
                <Docket
                  key={c.id}
                  kind="character"
                  icon={CharacterIcon}
                  title={c.title}
                  meta={`独立角色档案，${countText(c.output, "characters")}`}
                  date={c.created_at}
                  onOpen={() => setDetail({ kind: "entry", entry: c })}
                />
              ))}
              {skillItems.map((k) => (
                <Docket
                  key={k.id}
                  kind="skill"
                  icon={Puzzle}
                  title={k.name}
                  meta={`${k.version === "-" ? "版本未知" : k.version}，${k.status !== "valid" ? "校验未通过" : `${k.stage_count} 个阶段`}`}
                  date={k.created_at}
                />
              ))}
            </div>
          )}
        </>
      )}

      {showMore && data && (
        <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-fg-subtle">
          <span>已载入 {data.assets.length} 个文件，还有更早的。{searching && "搜索只覆盖已载入的部分。"}</span>
          <Button size="sm" disabled={loadingMore} onClick={() => void loadMore()}>
            {loadingMore && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
            加载更多
          </Button>
        </div>
      )}

      <AssetPreview
        asset={preview}
        source={preview ? sourceLabel(preview.project_id, titles) : ""}
        onClose={() => setPreview(null)}
      />
      <ProfileDetail target={detail} onClose={() => setDetail(null)} />
    </div>
  );
}

function countText(output: unknown, kind: "characters" | "scenes"): string {
  const list = (output as Record<string, unknown> | null)?.[kind];
  const n = Array.isArray(list) ? list.length : 0;
  return kind === "characters" ? `${n} 个角色` : `${n} 个场景`;
}

function emptyText(filter: AssetFilter, inProject: boolean): string {
  if (filter === "character")
    return inProject ? "这个项目还没有角色档案。跑到「角色档案」那一步之后会出现在这里。" : "还没有角色档案。";
  if (filter === "scene") return "还没有场景档案。项目跑到「场景档案」那一步之后会出现在这里。";
  if (filter === "all") return inProject ? "这个项目还没有素材。出图或上传之后会出现在这里。" : "资产库还是空的。出图或上传之后会出现在这里。";
  return `还没有${FILTER_LABEL[filter]}文件。`;
}

// ---------------------------------------------------------------- 上传

/**
 * 三段式直传（`assets.upload`）。MIME、大小、配额只由后端判，`accept` 只给选择器过滤。
 * 多选串行：失败时说得清是第几个、哪一步。`projectId` 为 null 时不挂项目。
 */
function UploadControl({
  projectId,
  label,
  onUploaded,
}: {
  projectId: string | null;
  label: string;
  onUploaded: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<UploadState | null>(null);
  const busy = state?.phase === "running";

  const pick = useCallback(
    async (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return;
      const list = Array.from(fileList);
      let done = 0;
      for (const file of list) {
        setState({ phase: "running", done, total: list.length, current: file.name });
        try {
          await assetsApi.upload(file, projectId ?? undefined);
          done += 1;
        } catch (e) {
          setState({
            phase: "error",
            done,
            total: list.length,
            name: file.name,
            message: e instanceof ApiRequestError ? e.error.user_message : "上传失败，请重试",
          });
          if (done > 0) onUploaded();
          if (inputRef.current) inputRef.current.value = "";
          return;
        }
      }
      setState({ phase: "done", count: done });
      onUploaded();
      if (inputRef.current) inputRef.current.value = "";
    },
    [onUploaded, projectId],
  );

  return (
    <div className="flex flex-col items-end gap-1.5">
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={UPLOAD_ACCEPT}
        aria-hidden
        data-upload="asset"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => void pick(e.target.files)}
      />
      <Button
        size="sm"
        variant="primary"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
        title="图片、视频、音频、文本、PDF、EPUB、JSON；类型与大小由服务端校验"
      >
        {busy ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : <Upload aria-hidden className="size-3.5" />}
        {label}
      </Button>
      {state?.phase === "running" && (
        <p role="status" className="max-w-xs truncate text-xs text-fg-muted" title={state.current}>
          上传中 {state.done + 1}/{state.total}：{state.current}
        </p>
      )}
      {state?.phase === "done" && (
        <p role="status" className="text-xs text-success">
          已上传 {state.count} 个文件
        </p>
      )}
      {state?.phase === "error" && (
        <p role="alert" className="max-w-sm text-right text-xs leading-5 text-danger">
          {state.done > 0 && `已上传 ${state.done} 个；`}
          {state.total > 1 ? `第 ${state.done + 1} 个「${state.name}」` : `「${state.name}」`}
          {state.message}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 容量

function UsageLine({ usage }: { usage: Library["usage"] }) {
  const { used_bytes, quota_bytes, percent_used } = usage;
  const nearFull = quota_bytes !== null && percent_used >= WARN_AT;
  return (
    <div className="mt-3 rounded-[2px] border border-border bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-fg-muted">容量按账号计，只算文件；档案不占容量。</span>
        <span className="tnum text-xs text-fg-subtle">
          {quota_bytes === null
            ? `已用 ${formatBytes(used_bytes)}，未设上限`
            : `${formatBytes(used_bytes)} / ${formatBytes(quota_bytes)}，已用 ${percent_used}%`}
        </span>
      </div>
      {quota_bytes !== null && (
        <div className="ff-meter mt-1.5" data-tone={nearFull ? "danger" : undefined}>
          <div
            role="progressbar"
            aria-valuenow={percent_used}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="资产库已用容量"
            className={cn("h-full transition-[width] duration-300", nearFull ? "bg-danger" : "bg-primary")}
            style={{ width: `${Math.min(Math.max(percent_used, used_bytes ? 2 : 0), 100)}%` }}
          />
        </div>
      )}
      {nearFull && (
        <p role="status" className="mt-1.5 text-xs text-danger">
          容量快满了，满了之后新的上传和生成都会被拒绝。
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 卡片

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rf-empty-state mt-6 rounded-[2px] border border-dashed border-border-strong px-4 py-10 text-center">
      <span className="rf-empty-icon">
        <AssetLibraryIcon aria-hidden className="size-6" />
      </span>
      <p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-fg-subtle">{children}</p>
    </div>
  );
}

function AssetSkeleton() {
  return (
    <div role="status" aria-label="加载中" className="ff-lighttable mt-6">
      <span className="sr-only">加载中…</span>
      {[1.78, 0.75, 1.5, 1.78].map((ar, i) => (
        <span key={i} className="rf-skeleton block" style={{ ["--ar" as string]: String(ar), aspectRatio: String(ar) }} />
      ))}
    </div>
  );
}

function SkillEmptyState({ onPick, busy }: { onPick: () => void; busy: boolean }) {
  return (
    <div className="mt-6 rounded-md border border-dashed border-border-strong px-4 py-10 text-center">
      <Puzzle aria-hidden className="mx-auto size-6 text-fg-subtle" />
      <p className="mt-2 text-sm text-fg-subtle">
        技能库里还没有 Skill。Skill 是一份生产模板（.yaml / .yml），上传后会校验并存进本组织的技能库。
      </p>
      <Button className="mt-3" size="sm" disabled={busy} onClick={onPick}>
        {busy ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : <Upload aria-hidden className="size-3.5" />}
        上传 Skill
      </Button>
      <p className="mt-3 text-xs text-fg-subtle">
        校验不通过的也会存进来并显示原因；但<span className="text-fg-muted">运行时尚未接线</span>
        ——当前生产流程仍走内置阶段图，传上来的 Skill 暂时不会生效。
      </p>
    </div>
  );
}

/**
 * 光桌上的一格：真实宽高比，点开预览。缩略图就是原图，预签名链接现签现用。
 * `source` 为 null（已在某个项目范围内）时，下沿写尺寸而不是重复项目名。
 */
function FilePlate({ asset, source, onOpen }: { asset: LibraryAsset; source: string | null; onOpen: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    assetsApi
      .downloadUrl(asset.id)
      .then((r) => alive && setUrl(r.url))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [asset.id]);

  const ratio = asset.width && asset.height ? Math.max(0.4, Math.min(3, asset.width / asset.height)) : 16 / 9;
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={source ? `预览 ${asset.filename}，${source}` : `预览 ${asset.filename}`}
      className="ff-plate cursor-pointer text-left focus-visible:outline-2 focus-visible:outline-primary"
      data-empty={url ? undefined : "true"}
      style={{ ["--ar" as string]: String(ratio) }}
    >
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element -- 预签名 URL 是运行时才知道的外部地址
        <img src={url} alt="" loading="lazy" />
      ) : (
        <RenderImageIcon aria-hidden className="size-6" />
      )}
      <span className="ff-plate-burn">
        <span title={asset.filename}>{asset.filename}</span>
        <span>{source ?? (asset.width && asset.height ? `${asset.width}×${asset.height}` : "")}</span>
      </span>
    </button>
  );
}

/** 档案列里的一条：书脊颜色标类别。有 `onOpen` 时可点开详情。 */
function Docket({
  kind,
  icon: Icon,
  title,
  meta,
  date,
  onOpen,
}: {
  kind: "file" | "character" | "scene" | "skill";
  icon: LucideIcon;
  title: string;
  meta: string;
  date: string;
  onOpen?: () => void;
}) {
  const body = (
    <>
      <Icon aria-hidden className="ff-docket-icon size-4" />
      <span className="ff-docket-body">
        <span className="ff-docket-title block" title={title}>
          {title}
        </span>
        <span className="ff-docket-meta">
          <span title={meta}>{meta}</span>
          <span className="code shrink-0">{new Date(date).toLocaleDateString("zh-CN")}</span>
        </span>
      </span>
    </>
  );
  return onOpen ? (
    <button
      type="button"
      onClick={onOpen}
      className="ff-docket w-full cursor-pointer text-left focus-visible:outline-2 focus-visible:outline-primary"
      data-kind={kind}
    >
      {body}
    </button>
  ) : (
    <article className="ff-docket" data-kind={kind}>
      {body}
    </article>
  );
}
