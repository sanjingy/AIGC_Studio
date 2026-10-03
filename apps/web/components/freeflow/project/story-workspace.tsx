"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, History, ListTree } from "lucide-react";

import { ConfirmDialog } from "@/components/freeflow/project/feedback";
import { RevisionHistoryDrawer } from "@/components/freeflow/storyboard/revision-history";
import { FieldsEditor, type EditableField } from "@/components/freeflow/story/fields-editor";
import { SceneEditor } from "@/components/freeflow/story/scene-editor";
import { StoryDirectory } from "@/components/freeflow/story/story-directory";
import {
  EpisodeTable,
  SceneTable,
  type SceneCoverage,
} from "@/components/freeflow/story/story-lists";
import { StoryIcon } from "@/components/icons/studio-icons";
import { PlotIndexView, plotIndexMeta } from "@/components/project/plot-index-view";
import { Button } from "@/components/ui/button";
import { Dialog, DialogCloseButton } from "@/components/ui/dialog";
import {
  buildEpisodes,
  coverageIndex,
  describeScreenplayPath,
  episodeLabel,
  episodeTitlePointer,
  hasField,
  headPointer,
  readStoryView,
  sameStoryView,
  sceneAt,
  sceneKey,
  sceneLabel,
  storyViewParams,
  tally,
  visibleScenes,
  type EpisodeGroup,
  type ScenePos,
  type ScreenplayLike,
  type SceneRow,
  type StoryView,
} from "@/lib/freeflow/screenplay-scope";
import { STORY_ANCHORS } from "@/lib/freeflow/stage-links";
import { useContentEdit } from "@/lib/freeflow/use-content-edit";
import { useNavigationGuard } from "@/lib/freeflow/use-navigation-guard";
import type { ProjectState } from "@/lib/freeflow/use-project-state";

import { PlanGate } from "./plan-gate";
import { AdvanceAction, GateActions } from "./production-actions";
import { RevisePanel } from "./revise-panel";

/**
 * 故事 / 剧本工作台（Reelbench P2A 样板）。
 *
 * 目录 + 主区两段，与分镜页同一个壳（本页关掉右栏与胶片阶段带）。四个视图：
 * 故事（情节目录）、完整剧本、一集、一场。
 *
 * 写库的三件事：**逐字段编辑剧本**（ADR-029，不花钱、可整批撤销）、
 * 过门① / 门②、自然语言返工。**没有出图入口**——剧本没有对应的出图契约，
 * 加一颗按钮就是假入口。
 *
 * 两条铁律：
 *
 * 1. **身份是数组位置。** 目录与表格显示集号 `index`、场号 `id`，但点击回调、
 *    JSON Pointer 一律走 `episodes[]` / `scenes[]` 的下标。集号可以不连续，
 *    场号跨集可以重复。
 * 2. **搜索只决定显示。** 它不导航，所以不会丢掉正在编辑的草稿；正在编辑的
 *    那一场会被钉在目录里，即使不匹配搜索词。
 */
export function StoryWorkspace({
  projectId,
  state,
  onGuardChange,
}: {
  /** 路由参数里的 id，不从 `state.project` 上取——加载中它还是 null。 */
  projectId: string;
  state: ProjectState;
  /**
   * 把"有未保存草稿 / 正在写库"报给页面。页面用它锁住顶栏那颗「推进生产」——
   * 关掉右栏与阶段带之后它是唯一的顶栏写入口，不能绕过未保存保护。
   */
  onGuardChange?: (guard: { dirty: boolean; busy: boolean }) => void;
}) {
  const plotIndex = state.output.plot_index;
  const screenplay = (state.output.screenplay ?? null) as ScreenplayLike | null;

  const edit = useContentEdit(projectId, "screenplay", state);

  const groups = useMemo(() => buildEpisodes(screenplay), [screenplay]);
  const counts = useMemo(() => tally(screenplay, groups), [screenplay, groups]);
  const coverage = useMemo(() => coverageIndex(screenplay), [screenplay]);
  const duplicatedIds = useMemo(
    () => new Set(coverage.duplicatedSceneIds),
    [coverage.duplicatedSceneIds],
  );

  /** 说话人 / 出场角色的候选：情节目录的角色表 + 角色档案。都是真实产出。 */
  const characterSuggestions = useMemo(() => {
    const out: string[] = [];
    const seen = new Set<string>();
    const push = (value: unknown) => {
      const name = typeof value === "string" ? value.trim() : "";
      if (!name || seen.has(name)) return;
      seen.add(name);
      out.push(name);
    };
    for (const entry of (plotIndex?.characters ?? []) as { name?: string; aliases?: string[] }[]) {
      push(entry?.name);
      for (const alias of entry?.aliases ?? []) push(alias);
    }
    for (const entry of (state.output.characters?.characters ?? []) as {
      ref?: string;
      name?: string;
    }[]) {
      push(entry?.ref);
      push(entry?.name);
    }
    return out;
  }, [plotIndex, state.output.characters]);

  /* ------------------------------------------------------------ 视图与 URL */

  const [query, setQuery] = useState("");
  const [view, setView] = useState<StoryView>({ kind: "story" });
  const [dirty, setDirty] = useState(false);
  const [pendingView, setPendingView] = useState<StoryView | null>(null);
  /** 站内离开（模块栏、面包屑、后退）被拦下时，确认后要执行的那一步 */
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);
  const [dirOpen, setDirOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const restored = useRef(false);

  // 产出到位之后再按 URL 恢复：集号/场号要回真实数据里查数组位置，
  // 数据还没来的时候查不到，会白白退化成完整剧本。
  useEffect(() => {
    if (restored.current || (!plotIndex && !screenplay)) return;
    restored.current = true;
    setView(readStoryView(window.location.search, window.location.hash, groups));
  }, [plotIndex, screenplay, groups]);

  // 旧入口：阶段条给的还是 `/story#plot-index` / `#screenplay`。已经在这一页时
  // 点它不会重新挂载，只会触发 hashchange，所以这里也听一次。
  useEffect(() => {
    const onHash = () => {
      const anchor = window.location.hash.replace(/^#/, "");
      if (anchor === STORY_ANCHORS.script) goRef.current({ kind: "script" });
      else if (anchor === STORY_ANCHORS.story) goRef.current({ kind: "story" });
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!restored.current) return;
    const params = storyViewParams(view, groups);
    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries(params) as [string, string | null][]) {
      if (value === null) url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    }
    // query 一旦写上就是唯一真相，旧 hash 留着只会在下次刷新时和它打架
    url.hash = "";
    window.history.replaceState(window.history.state, "", url);
  }, [view, groups]);

  useEffect(() => {
    onGuardChange?.({ dirty, busy: edit.busy !== null });
  }, [dirty, edit.busy, onGuardChange]);

  // 站内离开：模块栏 / 面包屑的链接与浏览器后退。beforeunload 只管整页卸载，
  // 这两条它看不见（见 use-navigation-guard.ts）。
  useNavigationGuard(dirty, (proceed) => setPendingLeave(() => proceed));

  const go = useCallback(
    (next: StoryView) => {
      setDirOpen(false);
      if (sameStoryView(next, view)) return;
      // 未保存就切走等于静默丢弃——拦下来问一句
      if (dirty) {
        setPendingView(next);
        return;
      }
      setView(next);
    },
    [view, dirty],
  );
  // hashchange 监听只挂一次，经 ref 拿最新的 go，才能同样走未保存确认
  const goRef = useRef(go);
  goRef.current = go;

  /* ------------------------------------------------------------ 当前对象 */

  const episodeAtOf = (v: StoryView): number | null => {
    const at = v.kind === "episode" ? v.at : v.kind === "scene" ? v.ep : null;
    return at !== null && at < groups.length ? at : null;
  };

  const activeEpisodeAt = episodeAtOf(view);
  const activeGroup: EpisodeGroup | null =
    activeEpisodeAt === null ? null : (groups[activeEpisodeAt] ?? null);
  const activeRow: SceneRow | null =
    view.kind === "scene" && activeGroup ? (activeGroup.scenes[view.at] ?? null) : null;
  const activePos: ScenePos | null =
    view.kind === "scene" && activeGroup && activeRow ? { ep: view.ep, at: view.at } : null;
  const activeScene = activePos ? sceneAt(screenplay, activePos) : null;

  /** 返工后集/场可能变少：指不到的视图逐级退回，不给一个空白页。 */
  const effective: StoryView =
    view.kind === "scene" && !activeScene
      ? activeGroup
        ? { kind: "episode", at: activeGroup.at }
        : { kind: "script" }
      : view.kind === "episode" && !activeGroup
        ? { kind: "script" }
        : view;

  const dirtyScene = dirty && activePos ? activePos : null;

  /* ------------------------------------------------------------ 搜索与序列 */

  const visibleList = useMemo(
    () => visibleScenes(screenplay, groups, query, dirtyScene),
    [screenplay, groups, query, dirtyScene],
  );
  const visible = useMemo(() => new Set(visibleList), [visibleList]);

  const visibleInEpisode = useMemo(() => {
    if (!activeGroup) return null;
    if (!query.trim()) return null;
    const out = new Set<number>();
    for (const row of activeGroup.scenes) {
      if (visible.has(sceneKey({ ep: activeGroup.at, at: row.at }))) out.add(row.at);
    }
    return out;
  }, [activeGroup, query, visible]);

  /** 上一场 / 下一场走当前搜索命中的序列；当前场被筛掉时走完整序列。 */
  const stepList = useMemo(() => {
    if (!activePos) return [] as string[];
    const key = sceneKey(activePos);
    if (visibleList.includes(key)) return visibleList;
    return visibleScenes(screenplay, groups, "", null);
  }, [activePos, visibleList, screenplay, groups]);
  const stepPos = activePos ? stepList.indexOf(sceneKey(activePos)) : -1;

  const goStep = (offset: number) => {
    const key = stepList[stepPos + offset];
    if (!key) return;
    const [ep, at] = key.split(":").map(Number);
    go({ kind: "scene", ep: ep!, at: at! });
  };

  /* ------------------------------------------------------------ 覆盖关系 */

  const coverageForScene = useCallback(
    (row: SceneRow): SceneCoverage => ({
      nodes: coverage.byScene[row.id] ?? [],
      ambiguous: row.id !== "" && duplicatedIds.has(row.id),
    }),
    [coverage.byScene, duplicatedIds],
  );

  const coverageForEpisode = useCallback(
    (group: EpisodeGroup): SceneCoverage => {
      const nodes = new Set<number>();
      let ambiguous = false;
      for (const row of group.scenes) {
        const info = coverageForScene(row);
        if (info.ambiguous) ambiguous = true;
        for (const node of info.nodes) nodes.add(node);
      }
      return { nodes: [...nodes].sort((a, b) => a - b), ambiguous };
    },
    [coverageForScene],
  );

  /* ------------------------------------------------------------ 可编辑字段 */

  const headFields: EditableField[] = useMemo(
    () => [
      {
        path: headPointer("title"),
        label: "剧本标题",
        value: typeof screenplay?.title === "string" ? screenplay.title : "",
        present: hasField(screenplay, "title"),
        maxLength: 80,
      },
      {
        path: headPointer("synopsis"),
        label: "剧本梗概",
        value: typeof screenplay?.synopsis === "string" ? screenplay.synopsis : "",
        present: hasField(screenplay, "synopsis"),
        multiline: true,
        maxLength: 600,
      },
    ],
    [screenplay],
  );

  const episodeFields: EditableField[] = useMemo(() => {
    if (!activeGroup) return [];
    const raw = (screenplay?.episodes ?? [])[activeGroup.at];
    return [
      {
        path: episodeTitlePointer(activeGroup.at),
        label: "集标题",
        value: activeGroup.title,
        present: hasField(raw, "title"),
        maxLength: 60,
      },
    ];
  }, [activeGroup, screenplay]);

  const dirtyReason = dirty ? "有未保存的改动，先保存或放弃再做这件事" : null;

  const describePath = useCallback(
    (path: string) => describeScreenplayPath(screenplay, path),
    [screenplay],
  );

  /* ------------------------------------------------------------ 渲染片段 */

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

  const directoryToggle = (
    <Button
      size="sm"
      className="lg:hidden"
      aria-expanded={dirOpen}
      aria-controls="story-directory-drawer"
      onClick={() => setDirOpen(true)}
    >
      <ListTree aria-hidden className="size-3.5" />
      目录
    </Button>
  );

  const directory = (
    <StoryDirectory
      groups={groups}
      visible={visible}
      query={query}
      onQuery={setQuery}
      totalScenes={counts.scenes}
      view={effective}
      dirtyScene={dirtyScene}
      hasPlotIndex={Boolean(plotIndex)}
      hasScreenplay={Boolean(screenplay)}
      onStory={() => go({ kind: "story" })}
      onScript={() => go({ kind: "script" })}
      onEpisode={(at) => go({ kind: "episode", at })}
      onScene={(pos) => go({ kind: "scene", ep: pos.ep, at: pos.at })}
    />
  );

  const nothingYet = !plotIndex && !screenplay;

  return (
    <div className="flex h-full min-h-0">
      {!nothingYet && (
        <aside
          aria-label="故事与剧本目录"
          className="ff-sb-directory hidden shrink-0 border-r border-border bg-surface lg:block"
        >
          {directory}
        </aside>
      )}

      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1480px] flex-col gap-4 px-4 pt-4 pb-6 lg:px-6">
          {nothingYet ? (
            <div className="mx-auto flex w-full max-w-[720px] flex-col gap-4">
              <div className="rf-empty-state rounded-[2px] border border-dashed border-border-strong px-6 py-8 text-center">
                <span className="rf-empty-icon">
                  <StoryIcon aria-hidden className="size-7" />
                </span>
                <h2 className="mt-3 text-sm font-semibold text-fg">还没有故事产出</h2>
                <p className="mt-1 text-sm leading-6 text-fg-subtle">
                  给一段小说原文或一句创意，然后推进生产：编排器会依次跑路线判断、情节目录，
                  停在「开拍前确认」这道门上——在那里定画风、时代背景与改编模式，然后才跑剧本。
                </p>
              </div>
              <AdvanceAction state={state} />
            </div>
          ) : effective.kind === "story" ? (
            <>
              <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
                {directoryToggle}
                {/* 窄屏：标题块排到按钮行下面独占一行，不和「目录」「改动记录」挤成一条缝 */}
                <div className="min-w-0 flex-1 max-sm:order-last max-sm:basis-full">
                  <h1 className="ff-display truncate text-2xl text-fg">故事与情节目录</h1>
                  <p className="mt-0.5 text-sm text-fg-muted">
                    {plotIndex ? plotIndexMeta(plotIndex) : "情节目录还没产出"}
                  </p>
                </div>
                {screenplay && historyButton}
              </header>

              {state.pendingGate === "plan" && <PlanGate projectId={projectId} state={state} />}

              {!state.pendingGate && <AdvanceAction state={state} />}

              {plotIndex ? (
                /* id 保留：门① 那句「全部 N 条就在下面」以及阶段条的旧链接都落在这里 */
                <section
                  id={STORY_ANCHORS.story}
                  className="scroll-mt-4 overflow-hidden rounded-[2px] border border-border bg-surface"
                >
                  <div className="flex items-baseline justify-between gap-3 border-b border-border px-4 py-2.5">
                    <h3 className="text-sm font-semibold text-fg">情节目录</h3>
                    <span className="tnum text-xs text-fg-subtle">
                      {screenplay
                        ? `剧本已覆盖 ${counts.covered} / ${counts.coverageTotal} 条`
                        : "只读产出"}
                    </span>
                  </div>
                  <PlotIndexView data={plotIndex} />
                </section>
              ) : (
                <EmptyCard text="情节目录还没产出。推进生产会先跑它，然后停在门① 上。" />
              )}

              <RevisePanel state={state} target="plot_index" disabledReason={dirtyReason} />
            </>
          ) : effective.kind === "script" ? (
            <>
              <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
                {directoryToggle}
                <div className="min-w-0 flex-1 max-sm:order-last max-sm:basis-full">
                  <h1 className="ff-display truncate text-2xl text-fg">
                    {screenplay?.title ? `完整剧本《${screenplay.title}》` : "完整剧本"}
                  </h1>
                  <StatLine counts={counts} merged={coverage.merged} />
                </div>
                {historyButton}
              </header>

              <GateActions state={state} gate="setup" blockedReason={dirtyReason} />

              {screenplay ? (
                <>
                  <section id={STORY_ANCHORS.script} className="scroll-mt-4">
                    <FieldsEditor
                      fields={headFields}
                      edit={edit}
                      onDirtyChange={setDirty}
                      heading="剧本标题与梗概"
                      hint="改动只写这两个字段"
                      saveLabel="保存"
                    />
                  </section>

                  <section aria-labelledby="story-episodes">
                    <h3
                      id="story-episodes"
                      className="mb-2 flex items-baseline gap-2 text-sm font-semibold text-fg"
                    >
                      分集
                      <span className="tnum text-xs font-normal text-fg-subtle">
                        {counts.episodes} 集 · 集号与场号由剧本 Agent 决定，只读
                      </span>
                    </h3>
                    <EpisodeTable
                      groups={groups}
                      coverageOf={coverageForEpisode}
                      onEpisode={(at) => go({ kind: "episode", at })}
                    />
                  </section>

                  <RevisePanel state={state} target="screenplay" disabledReason={dirtyReason} />
                </>
              ) : (
                <EmptyCard text="剧本还没产出。过了门① 之后推进生产才会跑它。" />
              )}
            </>
          ) : effective.kind === "episode" && activeGroup ? (
            <>
              <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
                {directoryToggle}
                <Button size="sm" variant="ghost" onClick={() => go({ kind: "script" })}>
                  <ChevronLeft aria-hidden className="size-4" />
                  完整剧本
                </Button>
                <div className="min-w-0 flex-1 max-sm:order-last max-sm:basis-full">
                  <h1 className="ff-display truncate text-xl text-fg">
                    {episodeLabel(activeGroup)}　{activeGroup.title || "未命名"}
                  </h1>
                  <p className="tnum mt-0.5 text-xs text-fg-subtle">
                    {activeGroup.scenes.length} 场 · {activeGroup.beatCount} 节拍 ·
                    对白/旁白 {activeGroup.dialogueCount} 条 · 集号只读
                  </p>
                </div>
                {historyButton}
              </header>

              <FieldsEditor
                fields={episodeFields}
                edit={edit}
                onDirtyChange={setDirty}
                heading="本集标题"
                hint="集号是剧本 Agent 写的引用键，不可改"
                saveLabel="保存标题"
              />

              <section aria-labelledby="story-scenes">
                <h3
                  id="story-scenes"
                  className="mb-2 flex items-baseline gap-2 text-sm font-semibold text-fg"
                >
                  场次
                  <span className="tnum text-xs font-normal text-fg-subtle">
                    {visibleInEpisode ? `${visibleInEpisode.size} / ` : ""}
                    {activeGroup.scenes.length} 场
                    {query.trim() ? `（搜索「${query.trim()}」）` : ""}
                  </span>
                </h3>
                <SceneTable
                  rows={activeGroup.scenes}
                  visible={visibleInEpisode}
                  coverageOf={coverageForScene}
                  dirtyAt={dirtyScene && dirtyScene.ep === activeGroup.at ? dirtyScene.at : null}
                  onScene={(at) => go({ kind: "scene", ep: activeGroup.at, at })}
                  emptyText={
                    activeGroup.scenes.length === 0
                      ? "这一集没有场次。只能返工整段剧本来补。"
                      : "没有符合搜索的场次。"
                  }
                />
              </section>
            </>
          ) : effective.kind === "scene" && activeGroup && activeRow && activeScene && activePos ? (
            <>
              <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
                {directoryToggle}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => go({ kind: "episode", at: activeGroup.at })}
                  title="回到这一集"
                >
                  <ChevronLeft aria-hidden className="size-4" />
                  <span className="max-w-[12rem] truncate">
                    {episodeLabel(activeGroup)}
                    {activeGroup.title ? ` ${activeGroup.title}` : ""}
                  </span>
                </Button>
                {/* 最小宽度让窄屏上标题整行换下去，而不是被挤成竖排 */}
                <div className="flex min-w-[10rem] flex-1 items-baseline gap-2">
                  <span className="tnum shrink-0 whitespace-nowrap text-sm text-primary">
                    {sceneLabel(activeRow)}
                  </span>
                  <h1 className="min-w-0 truncate text-base font-semibold text-fg">
                    {activeRow.location || "（未填地点）"}
                  </h1>
                </div>
                <span className="tnum text-xs text-fg-subtle">
                  第 {stepPos + 1} / {stepList.length} 场
                  {stepList === visibleList && query.trim() ? "（搜索内）" : ""}
                </span>
                <div className="flex items-center gap-1">
                  {historyButton}
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label="上一场"
                    disabled={stepPos <= 0}
                    onClick={() => goStep(-1)}
                  >
                    <ChevronLeft aria-hidden className="size-4" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label="下一场"
                    disabled={stepPos < 0 || stepPos >= stepList.length - 1}
                    onClick={() => goStep(1)}
                  >
                    <ChevronRight aria-hidden className="size-4" />
                  </Button>
                </div>
              </header>

              <SceneEditor
                // 换场时重建组件，草稿随之重置——留着上一场的草稿，用户会在这一场
                // 上看到不属于它的内容。
                key={sceneKey(activePos)}
                pos={activePos}
                scene={activeScene}
                edit={edit}
                characterSuggestions={characterSuggestions}
                onDirtyChange={setDirty}
                readOnlyChips={
                  <>
                    <ReadOnlyChip label="场号" value={activeRow.id || "缺失"} />
                    <ReadOnlyChip label="所属集" value={episodeLabel(activeGroup)} />
                    <ReadOnlyChip
                      label="覆盖情节节点"
                      value={
                        coverageForScene(activeRow).ambiguous
                          ? "场号在多集重复，无法确定"
                          : coverageForScene(activeRow).nodes.join("、") || "无"
                      }
                    />
                  </>
                }
              />
            </>
          ) : (
            <EmptyCard text="这一场已经不在剧本里了（可能刚被返工重写）。从左侧目录重新选一场。" />
          )}
        </div>
      </div>

      <Dialog
        id="story-directory-drawer"
        open={dirOpen}
        onOpenChange={setDirOpen}
        labelledBy="story-directory-title"
        placement="left"
        overlayClassName="bg-rf-overlay"
        className="h-full w-[min(24.375rem,calc(100vw-3.5rem))] border-r border-border bg-surface shadow-rf-card"
      >
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-3">
          <h2 id="story-directory-title" className="text-sm font-semibold text-fg">
            故事与剧本目录
          </h2>
          <DialogCloseButton
            label="关闭目录"
            onClick={() => setDirOpen(false)}
            className="size-9"
          />
        </div>
        <div className="min-h-0 flex-1">{dirOpen && directory}</div>
      </Dialog>

      <RevisionHistoryDrawer
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        edit={edit}
        title="剧本的字段改动"
        describePath={describePath}
        emptyText="还没有字段级改动。改一个剧本字段并保存，这里就会留下一条记录。"
        undoBlockedReason={dirtyReason}
      />

      <ConfirmDialog
        open={pendingView !== null || pendingLeave !== null}
        title="还有未保存的改动"
        description={
          pendingLeave
            ? "离开这一页会丢掉它们。要先回去保存，还是放弃这些改动？"
            : "离开会丢掉它们。要先回去保存，还是放弃这些改动？"
        }
        confirmLabel={pendingLeave ? "放弃改动并离开这一页" : "放弃改动并离开"}
        tone="danger"
        onCancel={() => {
          setPendingView(null);
          setPendingLeave(null);
        }}
        onConfirm={() => {
          if (pendingLeave) {
            // 离开这一页：组件随路由卸载，草稿跟着消失，不用自己清
            const proceed = pendingLeave;
            setPendingLeave(null);
            proceed();
            return;
          }
          // 编辑组件按 key / 字段集合重建，草稿随之丢弃——这正是"放弃"的语义
          if (pendingView !== null) setView(pendingView);
          setPendingView(null);
          setDirty(false);
        }}
      />
    </div>
  );
}

function StatLine({
  counts,
  merged,
}: {
  counts: { episodes: number; scenes: number; beats: number; dialogue: number; covered: number; coverageTotal: number };
  merged: number;
}) {
  return (
    <p className="tnum mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-subtle">
      <span>{counts.episodes} 集</span>
      <span>{counts.scenes} 场</span>
      <span>{counts.beats} 节拍</span>
      <span>对白/旁白 {counts.dialogue} 条</span>
      <span>
        覆盖 {counts.covered} / {counts.coverageTotal} 个情节节点
      </span>
      {merged > 0 && <span>其中 {merged} 条被并进别的节点</span>}
    </p>
  );
}

function ReadOnlyChip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-[2px] border border-border bg-surface-2 px-2 py-1 text-xs">
      <span className="text-fg-subtle">{label}</span>
      <span className="tnum text-fg-muted">{value}</span>
    </span>
  );
}

function EmptyCard({ text }: { text: string }) {
  return (
    <p className="rounded-[2px] border border-dashed border-border-strong px-4 py-10 text-center text-sm leading-6 text-fg-subtle">
      {text}
    </p>
  );
}
