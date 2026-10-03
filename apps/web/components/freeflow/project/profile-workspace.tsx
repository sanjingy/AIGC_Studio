"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, History, ListTree } from "lucide-react";

import { ConfirmDialog } from "@/components/freeflow/project/feedback";
import {
  ProfileCard,
  ProfileDirectory,
  type ImageState,
} from "@/components/freeflow/profile/profile-directory";
import { ProfileEditor } from "@/components/freeflow/profile/profile-editor";
import { ProfileMedia } from "@/components/freeflow/profile/profile-media";
import { RevisionHistoryDrawer } from "@/components/freeflow/storyboard/revision-history";
import { CharacterIcon, SceneIcon } from "@/components/icons/studio-icons";
import { Button } from "@/components/ui/button";
import { Dialog, DialogCloseButton } from "@/components/ui/dialog";
import { dropPromptEditedBefore } from "@/lib/freeflow/prepared-prompts";
import {
  buildRows,
  characterFields,
  describeProfilePath,
  itemsOf,
  namedItems,
  profileViewParams,
  readProfileView,
  sceneFields,
  sceneLighting,
  visibleRows,
  type ProfileView,
  type Role,
} from "@/lib/freeflow/profile-scope";
import { useAssetUrls } from "@/lib/freeflow/use-asset-urls";
import { useContentEdit } from "@/lib/freeflow/use-content-edit";
import type { Images } from "@/lib/freeflow/use-images";
import { useNavigationGuard } from "@/lib/freeflow/use-navigation-guard";
import type { ProjectState } from "@/lib/freeflow/use-project-state";

import { AnchorsGate } from "./anchors-gate";
import { AdvanceAction } from "./production-actions";
import { RevisePanel } from "./revise-panel";

const COPY = {
  characters: { heading: "角色", noun: "角色", save: "保存这个角色", empty: "还没有角色档案。推进生产跑到这一步就会出现。" },
  scenes: { heading: "场景", noun: "场景", save: "保存这个场景", empty: "还没有场景档案。推进生产跑到这一步就会出现。" },
} as const;

/**
 * 角色 / 场景工作台（Reelbench P2B）。与分镜、剧本同一个壳：目录 + 主区，
 * 本页关掉右栏与胶片阶段带。两个视图：总览（全部对象 + 门③ + 返工）与单个对象
 * （大图 + 档案编辑）。
 *
 * 身份是 `characters[]` / `scenes[]` 的**数组位置**；搜索只决定目录里显示谁，
 * 不改变编辑、出图、审核的范围。门③ 永远是全部场景一次确认。
 */
export function ProfileWorkspace({
  projectId,
  role,
  state,
  renders,
  onGuardChange,
}: {
  projectId: string;
  role: Role;
  state: ProjectState;
  renders: Images;
  onGuardChange?: (guard: { dirty: boolean; busy: boolean }) => void;
}) {
  const copy = COPY[role];
  const subjectKind = role === "characters" ? ("character" as const) : ("scene" as const);
  const block = (state.output[role] ?? null) as Record<string, unknown> | null;
  const items = useMemo(() => itemsOf(block, role), [block, role]);
  const rows = useMemo(() => buildRows(block, role), [block, role]);
  const edit = useContentEdit(projectId, role, state);

  /* ------------------------------------------------------------ 出图状态 */

  const histories = useMemo(
    () => rows.map((r) => (r.ref ? renders.historyOf({ kind: subjectKind, ref: r.ref }) : [])),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- renders 每次渲染都是新对象，按它的数据源判断
    [rows, subjectKind, renders.historyOf],
  );
  const urlOf = useAssetUrls(histories.flatMap((list) => list.map((h) => h.assetId)));

  const states: ImageState[] = useMemo(
    () =>
      rows.map((r, at) => {
        const list = histories[at] ?? [];
        const latest = list[0];
        if (r.ref && renders.isPending({ kind: subjectKind, ref: r.ref })) return "running";
        if (latest?.status === "queued" || latest?.status === "running") return "running";
        const current = list.find((h) => h.assetId);
        if (latest && !latest.assetId && (latest.status === "failed" || latest.status === "cancelled")) return "failed";
        if (!current) return "missing";
        const editedAt = edit.editedAt(`/${role}/${at}`);
        return editedAt !== null && Date.parse(current.createdAt) < editedAt ? "outdated" : "ready";
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 同上
    [rows, histories, edit.editedAt, role, subjectKind, renders.isPending],
  );

  // 档案改过之后，改动之前准备的提示词不再算数（字段级编辑不碰提示词运行记录）
  useEffect(() => {
    rows.forEach((r, at) => {
      if (r.ref) dropPromptEditedBefore(projectId, subjectKind, r.ref, edit.editedAt(`/${role}/${at}`));
    });
  }, [rows, edit.editedAt, projectId, role, subjectKind]);

  /* ------------------------------------------------------------ 视图与 URL */

  const [query, setQuery] = useState("");
  const [view, setView] = useState<ProfileView>({ kind: "overview" });
  const [dirty, setDirty] = useState(false);
  const [pendingView, setPendingView] = useState<ProfileView | null>(null);
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);
  const [dirOpen, setDirOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const restored = useRef(false);

  useEffect(() => {
    if (restored.current || rows.length === 0) return;
    restored.current = true;
    setView(readProfileView(window.location.search, rows));
  }, [rows]);

  useEffect(() => {
    if (!restored.current) return;
    const params = profileViewParams(view, rows);
    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries(params)) {
      if (value === null) url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    }
    window.history.replaceState(window.history.state, "", url);
  }, [view, rows]);

  useEffect(() => {
    onGuardChange?.({ dirty, busy: edit.busy !== null });
  }, [dirty, edit.busy, onGuardChange]);

  useNavigationGuard(dirty, (proceed) => setPendingLeave(() => proceed));

  const go = useCallback(
    (next: ProfileView) => {
      setDirOpen(false);
      const same = next.kind === view.kind && (next.kind === "overview" || (view.kind === "item" && view.at === next.at));
      if (same) return;
      // 写库中不许切走：保存结果要落回发起它的那个对象
      if (edit.busy !== null) return;
      if (dirty) {
        setPendingView(next);
        return;
      }
      setView(next);
    },
    [view, dirty, edit.busy],
  );

  /** 返工后对象可能变少：指不到就回总览 */
  const activeAt = view.kind === "item" && view.at < items.length ? view.at : null;
  const effective: ProfileView = activeAt === null ? { kind: "overview" } : view;
  const activeRow = activeAt === null ? null : rows[activeAt]!;
  const activeItem = activeAt === null ? null : items[activeAt];
  const dirtyAt = dirty ? activeAt : null;

  const visible = useMemo(() => visibleRows(rows, query, dirtyAt), [rows, query, dirtyAt]);
  const hitCount = useMemo(() => visibleRows(rows, query, null).length, [rows, query]);

  const stepList = activeAt !== null && visible.includes(activeAt) ? visible : rows.map((r) => r.at);
  const stepPos = activeAt === null ? -1 : stepList.indexOf(activeAt);

  const blockedReason = dirty
    ? "有未保存的改动，先保存或放弃再做这件事"
    : edit.busy !== null
      ? "正在写回改动，完成后再做这件事"
      : null;

  const fields = useMemo(
    () => (activeAt === null ? [] : role === "characters" ? characterFields(activeItem, activeAt) : sceneFields(activeItem, activeAt)),
    [activeAt, activeItem, role],
  );

  const describePath = useCallback((path: string) => describeProfilePath(block, role, path), [block, role]);

  /* ------------------------------------------------------------ 片段 */

  const directoryToggle = (
    <Button size="sm" className="lg:hidden" aria-expanded={dirOpen} aria-controls="profile-directory-drawer" onClick={() => setDirOpen(true)}>
      <ListTree aria-hidden className="size-3.5" />
      目录
    </Button>
  );

  const historyButton = (
    <Button size="sm" aria-expanded={historyOpen} onClick={() => setHistoryOpen(true)}>
      <History aria-hidden className="size-3.5" />
      改动记录
      {edit.loadedCount > 0 && (
        <span className="tnum text-fg-subtle">
          {edit.loadedCount}
          {edit.hasMore ? "+" : ""}
        </span>
      )}
    </Button>
  );

  const directory = (
    <ProfileDirectory
      heading={copy.heading}
      noun={copy.noun}
      rows={rows}
      visible={visible}
      hitCount={hitCount}
      states={states}
      query={query}
      onQuery={setQuery}
      view={effective}
      dirtyAt={dirtyAt}
      onOverview={() => go({ kind: "overview" })}
      onItem={(at) => go({ kind: "item", at })}
      lockedReason={edit.busy !== null ? "正在写回改动，完成后才能切换" : null}
    />
  );

  const count = (s: ImageState) => states.filter((x) => x === s).length;
  const Icon = role === "characters" ? CharacterIcon : SceneIcon;

  return (
    <div className="flex h-full min-h-0">
      {rows.length > 0 && (
        <aside aria-label={`${copy.heading}目录`} className="ff-sb-directory hidden shrink-0 border-r border-border bg-surface lg:block">
          {directory}
        </aside>
      )}

      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1480px] flex-col gap-4 px-4 pt-4 pb-6 lg:px-6">
          {rows.length === 0 ? (
            <div className="mx-auto flex w-full max-w-[720px] flex-col gap-4">
              {role === "scenes" && <AnchorsGate state={state} blockedReason={blockedReason} />}
              <div className="rf-empty-state rounded-[2px] border border-dashed border-border-strong px-6 py-8 text-center">
                <span className="rf-empty-icon">
                  <Icon aria-hidden className="size-7" />
                </span>
                <p className="mt-3 text-sm leading-6 text-fg-subtle">{copy.empty}</p>
              </div>
              <AdvanceAction state={state} />
              {block && <RevisePanel state={state} target={role} disabledReason={blockedReason} />}
            </div>
          ) : effective.kind === "overview" ? (
            <>
              <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
                {directoryToggle}
                <div className="min-w-0 flex-1 max-sm:order-last max-sm:basis-full">
                  <h1 className="ff-display truncate text-2xl text-fg">全部{copy.noun}</h1>
                  <p className="tnum mt-0.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-subtle">
                    <span>{rows.length} 个{copy.noun}</span>
                    <span>有图 {count("ready") + count("outdated")}</span>
                    {count("outdated") > 0 && <span className="text-rf-agent">可能过期 {count("outdated")}</span>}
                    {count("running") > 0 && <span className="text-running">生成中 {count("running")}</span>}
                    {count("failed") > 0 && <span className="text-danger">失败 {count("failed")}</span>}
                    <span>未出图 {count("missing")}</span>
                    {role === "scenes" && typeof block?.era === "string" && block.era && <span>时代：{block.era}</span>}
                    {role === "scenes" && typeof block?.global_tone === "string" && block.global_tone && <span>整体色调：{block.global_tone}</span>}
                  </p>
                </div>
                {historyButton}
              </header>

              {role === "scenes" && <AnchorsGate state={state} blockedReason={blockedReason} />}

              <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
                {rows.map((row, at) => {
                  const current = (histories[at] ?? []).find((h) => h.assetId);
                  return (
                    <ProfileCard
                      key={at}
                      row={row}
                      state={states[at]!}
                      src={urlOf(current?.assetId)}
                      square={role === "scenes"}
                      meta={role === "scenes" ? sceneMeta(items[at]) : undefined}
                      onOpen={() => go({ kind: "item", at })}
                    />
                  );
                })}
              </ul>

              <RevisePanel state={state} target={role} disabledReason={blockedReason} />
            </>
          ) : activeRow && activeAt !== null ? (
            <>
              <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
                {directoryToggle}
                <Button size="sm" variant="ghost" disabled={edit.busy !== null} onClick={() => go({ kind: "overview" })}>
                  <ChevronLeft aria-hidden className="size-4" />
                  全部{copy.noun}
                </Button>
                <div className="flex min-w-[10rem] flex-1 items-baseline gap-2">
                  <h1 className="min-w-0 truncate text-base font-semibold text-fg">{activeRow.name || "未命名"}</h1>
                  <span className="shrink-0 text-xs text-fg-subtle">{activeRow.ref}</span>
                </div>
                <span className="tnum text-xs text-fg-subtle">
                  第 {stepPos + 1} / {stepList.length} 个{stepList === visible && query.trim() ? "（搜索内）" : ""}
                </span>
                <div className="flex items-center gap-1">
                  {historyButton}
                  <Button size="sm" variant="ghost" aria-label={`上一个${copy.noun}`} disabled={edit.busy !== null || stepPos <= 0} onClick={() => go({ kind: "item", at: stepList[stepPos - 1]! })}>
                    <ChevronLeft aria-hidden className="size-4" />
                  </Button>
                  <Button size="sm" variant="ghost" aria-label={`下一个${copy.noun}`} disabled={edit.busy !== null || stepPos < 0 || stepPos >= stepList.length - 1} onClick={() => go({ kind: "item", at: stepList[stepPos + 1]! })}>
                    <ChevronRight aria-hidden className="size-4" />
                  </Button>
                </div>
              </header>

              {role === "scenes" && state.pendingGate === "anchors" && (
                <p className="flex flex-wrap items-center gap-2 rounded-md border border-rf-warning/40 bg-rf-warning-soft px-3 py-2 text-xs text-rf-warning">
                  门③ 待确认：确认的是全部场景的锚点与光照，在总览里一次完成。
                  <Button size="sm" variant="ghost" className="ml-auto" disabled={edit.busy !== null} onClick={() => go({ kind: "overview" })}>
                    去总览确认
                  </Button>
                </p>
              )}

              <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <ProfileMedia
                  key={`media:${activeAt}:${activeRow.ref}`}
                  subject={{ kind: subjectKind, ref: activeRow.ref }}
                  name={activeRow.name || activeRow.ref}
                  renders={renders}
                  urlOf={urlOf}
                  editedAt={edit.editedAt(`/${role}/${activeAt}`)}
                  blockedReason={blockedReason}
                />
                <ProfileEditor
                  key={`editor:${activeAt}:${activeRow.ref}`}
                  fields={fields}
                  edit={edit}
                  onDirtyChange={setDirty}
                  saveLabel={copy.save}
                  readOnly={<ReadOnlyFacts role={role} item={activeItem} />}
                />
              </div>
            </>
          ) : null}
        </div>
      </div>

      <Dialog
        id="profile-directory-drawer"
        open={dirOpen}
        onOpenChange={setDirOpen}
        labelledBy="profile-directory-title"
        placement="left"
        overlayClassName="bg-rf-overlay"
        className="h-full w-[min(24.375rem,calc(100vw-3.5rem))] border-r border-border bg-surface shadow-rf-card"
      >
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-3">
          <h2 id="profile-directory-title" className="text-sm font-semibold text-fg">
            {copy.heading}目录
          </h2>
          <DialogCloseButton label="关闭目录" onClick={() => setDirOpen(false)} className="size-9" />
        </div>
        <div className="min-h-0 flex-1">{dirOpen && directory}</div>
      </Dialog>

      <RevisionHistoryDrawer
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        edit={edit}
        title={`${copy.noun}档案的字段改动`}
        describePath={describePath}
        emptyText={`还没有字段改动。改一个${copy.noun}字段并保存，这里就会留下一条记录。`}
        undoBlockedReason={blockedReason}
      />

      <ConfirmDialog
        open={pendingView !== null || pendingLeave !== null}
        title="还有未保存的改动"
        description={pendingLeave ? "离开这一页会丢掉它们。要先回去保存，还是放弃这些改动？" : "离开会丢掉它们。要先回去保存，还是放弃这些改动？"}
        confirmLabel={pendingLeave ? "放弃改动并离开这一页" : "放弃改动并离开"}
        tone="danger"
        onCancel={() => {
          setPendingView(null);
          setPendingLeave(null);
        }}
        onConfirm={() => {
          if (pendingLeave) {
            const proceed = pendingLeave;
            setPendingLeave(null);
            proceed();
            return;
          }
          if (pendingView !== null) setView(pendingView);
          setPendingView(null);
          setDirty(false);
        }}
      />
    </div>
  );
}

function sceneMeta(item: unknown): string {
  const row = (item && typeof item === "object" ? item : {}) as Record<string, unknown>;
  const lighting = sceneLighting(row).items.length;
  const refs = namedItems(row.fixed_references, false).items.length;
  const slot = typeof row.time_slot === "string" ? row.time_slot : "";
  return [slot, `光照 ${lighting}`, `参照物 ${refs}`].filter(Boolean).join(" · ");
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-[2px] border border-border bg-surface-2 px-2 py-1 text-xs">
      <span className="shrink-0 text-fg-subtle">{label}</span>
      <span className="min-w-0 text-fg-muted">{value}</span>
    </span>
  );
}

/** 不能改的事实：引用键、推断字段、旧体型描述 */
function ReadOnlyFacts({ role, item }: { role: Role; item: unknown }) {
  const row = (item && typeof item === "object" ? item : {}) as Record<string, unknown>;
  const text = (v: unknown) => (typeof v === "string" ? v.trim() : "");
  const inferred = Array.isArray(row.inferred) ? row.inferred.map(String).filter(Boolean) : [];
  return (
    <>
      <Chip label="引用键" value={text(row.ref) || "缺失"} />
      {role === "characters" && inferred.length > 0 && <Chip label="推断字段" value={inferred.join("、")} />}
      {role === "characters" && text(row.build) && <Chip label="旧体型描述（只读）" value={text(row.build)} />}
      {role === "scenes" && <Chip label="光照名称" value="被镜头引用，只读" />}
    </>
  );
}
