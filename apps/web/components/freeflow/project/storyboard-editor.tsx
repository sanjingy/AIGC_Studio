"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, History, Layers, ListTree, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { StoryboardIcon } from "@/components/icons/studio-icons";
import { ConfirmDialog } from "@/components/freeflow/project/feedback";
import { BatchRenderDialog } from "@/components/freeflow/storyboard/batch-render-dialog";
import { RevisionHistoryDrawer } from "@/components/freeflow/storyboard/revision-history";
import { type ShotCardData } from "@/components/freeflow/storyboard/shot-card";
import { SHOT_FIELD_LABEL, type ShotOptions } from "@/components/freeflow/storyboard/shot-detail";
import { ShotEditor, type Shot } from "@/components/freeflow/storyboard/shot-editor";
import { ShotStage } from "@/components/freeflow/storyboard/shot-stage";
import {
  FILTER_LABEL,
  groupTitle,
  StoryboardDirectory,
  type StoryboardView,
} from "@/components/freeflow/storyboard/storyboard-directory";
import { NodeCoverage, ScopeSheet, StatStrip } from "@/components/freeflow/storyboard/storyboard-overview";
import { ImageSourcePicker } from "@/components/project/image-source-picker";
import { Button } from "@/components/ui/button";
import { Dialog, DialogCloseButton } from "@/components/ui/dialog";
import { credits, type Estimate } from "@/lib/api";
import { defaultLightingOf, lightingStatesOf } from "@/lib/freeflow/scene-shape";
import {
  buildDirectory,
  groupKey,
  planBatch,
  shotCode,
  submitIndexes,
  tally,
  visiblePositions,
  type ShotFact,
  type ShotFilter,
} from "@/lib/freeflow/storyboard-scope";
import { useContentEdit } from "@/lib/freeflow/use-content-edit";
import { useLocalRuntime } from "@/lib/freeflow/use-local-runtime";
import { dropPromptEditedBefore, usePreparedPrompt } from "@/lib/freeflow/prepared-prompts";
import type { Images, RenderView } from "@/lib/freeflow/use-images";
import type { ProjectState } from "@/lib/freeflow/use-project-state";
import { useAssetUrls } from "@/lib/freeflow/use-asset-urls";

import { AdvanceAction, GateActions } from "./production-actions";
import { RevisePanel } from "./revise-panel";
import { PromptPanel } from "./prompt-panel";

/* ------------------------------------------------------------------ 数据形状
 *
 * 分镜数据形状的权威定义在后端 `agents/schemas.py` 的
 * Storyboard / StoryboardShot。这里不另设计一套：分叉之后，同一份
 * output_json 在不同页面上会显示出不同的内容。`Shot` 的 TS 声明放在
 * `shot-editor.tsx`——编辑要按字段名拼 JSON Pointer，形状和它绑得最紧。
 *
 * 身份一律用数组位置 `at`，见 `lib/freeflow/storyboard-scope.ts`。
 */

/** 卡片标题。后端每一镜唯一能当标题的只有 `content`，截一段用，全文在详情里。 */
function titleOf(shot: Shot): string {
  const text = (shot.content ?? "").trim();
  if (!text) return `镜头 ${shot.index}`;
  return text.length > 24 ? `${text.slice(0, 24)}…` : text;
}

/**
 * 卡片状态。说的是**这一镜能不能生成**，不是「有没有图」——有没有图由
 * `imageUrl` 表达。「图可能过期」是另一个维度（`outdated`）。
 */
function statusOf(shot: Shot, latest: RenderView | null, pending: boolean): ShotCardData["status"] {
  if (pending || latest?.status === "running" || latest?.status === "queued") return "rendering";
  if (latest?.status === "failed") return "failed";
  return (shot.content ?? "").trim() ? "ready" : "draft";
}

function sameView(a: StoryboardView, b: StoryboardView): boolean {
  if (a.kind === "shot" && b.kind === "shot") return a.at === b.at;
  if (a.kind === "overview" && b.kind === "overview") return a.node === b.node;
  return false;
}

/** URL 上只记镜号 / 节点，刷新后回到同一处。镜号重复时取第一条。 */
function readViewFromUrl(shots: Shot[]): StoryboardView {
  if (typeof window === "undefined") return { kind: "overview", node: null };
  const params = new URLSearchParams(window.location.search);
  const shot = params.get("shot");
  if (shot !== null) {
    const at = shots.findIndex((s) => String(s.index) === shot);
    if (at >= 0) return { kind: "shot", at };
  }
  const node = params.get("node");
  return { kind: "overview", node: node || null };
}

function writeViewToUrl(view: StoryboardView, shots: Shot[]) {
  const url = new URL(window.location.href);
  url.searchParams.delete("shot");
  url.searchParams.delete("node");
  if (view.kind === "shot" && shots[view.at]) url.searchParams.set("shot", String(shots[view.at]!.index));
  if (view.kind === "overview" && view.node) url.searchParams.set("node", view.node);
  window.history.replaceState(window.history.state, "", url);
}

/**
 * 分镜工作台（Reelbench P1 样板）：目录 + 主区。
 *
 * 四件写库的事：**逐字段编辑分镜**（ADR-029，不花钱、可整批撤销）、
 * 单镜出图、按当前范围补图（逐镜串行）、过「确认分镜」门。视频不在内——
 * M2 才有那条链路，没有假入口。
 *
 * 改分镜有两条路，界面上都在：
 * - 字段级编辑：确定性、即时、一分钱不花，撤销以一次保存为单位。
 * - 自然语言返工（总览底部的 `RevisePanel`）：重跑整个 Agent，会扣 Credits。
 */
export function StoryboardEditor({
  projectId,
  state,
  renders,
}: {
  /**
   * 路由参数里的项目 id，不从 `state.project` 上取：项目是异步来的，
   * 加载中它还是 null，而 `useContentEdit` 需要一个从第一帧就稳定的 id。
   */
  projectId: string;
  state: ProjectState;
  renders: Images;
}) {
  const storyboard = state.output.storyboard;
  const scenes = state.output.scenes;
  const characters = state.output.characters;

  const shots: Shot[] = useMemo(() => (Array.isArray(storyboard?.shots) ? storyboard.shots : []), [storyboard]);
  const nodes = useMemo(() => (Array.isArray(storyboard?.nodes) ? storyboard.nodes : []), [storyboard]);

  const edit = useContentEdit(projectId, "storyboard", state);

  /**
   * 出图来源（平台 API / 本机 Codex）。状态与选择全站一份（`use-local-runtime.ts`），
   * 分镜这里接一次不会多发请求，也不会和角色/场景页的选择打架。
   */
  const runtime = useLocalRuntime(projectId);

  /**
   * 场景 ref 的建议列表：档案里的在前，分镜节点上出现过、档案里还没有的补在后面，
   * 名字用节点的 `summary`（分镜可以跑在场景档案之前）。
   */
  const sceneOptions = useMemo(() => {
    const out: { ref: string; name: string }[] = [];
    const seen = new Set<string>();
    for (const s of scenes?.scenes ?? []) {
      if (typeof s?.ref !== "string" || seen.has(s.ref)) continue;
      seen.add(s.ref);
      out.push({ ref: s.ref, name: String(s.name ?? s.ref) });
    }
    for (const n of nodes) {
      if (typeof n?.scene_ref !== "string" || seen.has(n.scene_ref)) continue;
      seen.add(n.scene_ref);
      out.push({ ref: n.scene_ref, name: String(n.summary ?? n.scene_ref) });
    }
    return out;
  }, [scenes, nodes]);

  const sceneName = useCallback(
    (ref: string) => sceneOptions.find((s) => s.ref === ref)?.name,
    [sceneOptions],
  );

  const characterOptions = useMemo(
    () =>
      (characters?.characters ?? [])
        .filter((c: any) => typeof c?.ref === "string")
        .map((c: any) => ({ ref: c.ref as string, name: String(c.name ?? c.ref) })),
    [characters],
  );

  /**
   * 每个场景各自的光照状态与默认状态。**按 scene_ref 分组，不取并集**：
   * 别的地点的光选了只会让后端记一条 warning 然后回落到默认。
   */
  const lighting = useMemo(() => {
    const byScene: Record<string, { ref: string; name: string }[]> = {};
    const defaults: Record<string, string> = {};
    for (const scene of scenes?.scenes ?? []) {
      if (typeof scene?.ref !== "string") continue;
      byScene[scene.ref] = lightingStatesOf(scene).map((entry) => ({
        ref: entry.name,
        name: entry.description,
      }));
      defaults[scene.ref] = defaultLightingOf(scene);
    }
    return { byScene, defaults };
  }, [scenes]);

  const options: ShotOptions = useMemo(
    () => ({
      scenes: sceneOptions,
      characters: characterOptions,
      lighting: lighting.byScene,
      defaultLighting: lighting.defaults,
    }),
    [sceneOptions, characterOptions, lighting],
  );

  /* ------------------------------------------------------------ 出图事实 */

  /** 每个数组位置的出图历史（新在前）、最新一条、当前版（最新有图的一条）。 */
  const renderInfo = useMemo(
    () =>
      shots.map((shot) => {
        const subject = { kind: "shot" as const, index: shot.index };
        const history = renders.historyOf(subject);
        return {
          history,
          latest: history[0] ?? null,
          current: history.find((h) => h.assetId) ?? null,
          pending: renders.isPending(subject),
        };
      }),
    [shots, renders],
  );

  const facts: ShotFact[] = useMemo(
    () =>
      shots.map((_, at) => {
        const info = renderInfo[at]!;
        const editedAt = edit.editedAt(`/shots/${at}`);
        return {
          hasImage: Boolean(info.current),
          status: info.latest?.status ?? null,
          pending: info.pending,
          // 图比这一镜最后一次改动旧 = 可能对不上。只标记，不自动重跑。
          outdated:
            info.current !== null && editedAt !== null && Date.parse(info.current.createdAt) < editedAt,
        };
      }),
    [shots, renderInfo, edit],
  );

  // 预签名地址成批签一次：当前版 + 全部历史，不让每张卡片各自去签
  const urlOf = useAssetUrls(
    useMemo(() => renderInfo.flatMap((info) => info.history.map((h) => h.assetId)), [renderInfo]),
  );

  const cards: ShotCardData[] = useMemo(
    () =>
      shots.map((shot, at) => {
        const info = renderInfo[at]!;
        const states = lighting.byScene[shot.scene_ref] ?? [];
        const ref = (shot.lighting_ref ?? "").trim();
        const unknown = ref !== "" && !states.some((s) => s.ref === ref);
        return {
          index: shot.index,
          code: shotCode(shot.index),
          title: titleOf(shot),
          framing: shot.shot_size,
          camera: shot.camera_move || shot.angle,
          lighting: ref || lighting.defaults[shot.scene_ref] || undefined,
          lightingUnknown: unknown,
          // 后端 StoryboardShot 没有时长（时长来自 TTS 的真实音频长度），不显示
          durationLabel: undefined,
          status: statusOf(shot, info.latest, info.pending),
          imageUrl: urlOf(info.current?.assetId),
          outdated: facts[at]?.outdated,
        };
      }),
    [shots, renderInfo, urlOf, lighting, facts],
  );

  /* ------------------------------------------------------------ 目录与筛选 */

  const groups = useMemo(() => buildDirectory(shots, nodes), [shots, nodes]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ShotFilter>("all");

  const visibleList = useMemo(
    () => visiblePositions(shots, facts, { query, filter, sceneName }),
    [shots, facts, query, filter, sceneName],
  );
  const visible = useMemo(() => new Set(visibleList), [visibleList]);

  /** 各筛选项的计数按搜索词算，切换筛选时数字不跳 */
  const filterCounts = useMemo(() => {
    const out = {} as Record<ShotFilter, number>;
    for (const key of Object.keys(FILTER_LABEL) as ShotFilter[]) {
      out[key] = visiblePositions(shots, facts, { query, filter: key, sceneName }).length;
    }
    return out;
  }, [shots, facts, query, sceneName]);

  /* ------------------------------------------------------------ 视图与未保存保护 */

  const [view, setView] = useState<StoryboardView>({ kind: "overview", node: null });
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current || shots.length === 0) return;
    restored.current = true;
    setView(readViewFromUrl(shots));
  }, [shots]);
  useEffect(() => {
    if (restored.current) writeViewToUrl(view, shots);
  }, [view, shots]);

  const [shotDirty, setShotDirty] = useState(false);
  /** 有未保存改动时要去的地方，等用户确认 */
  const [pendingView, setPendingView] = useState<StoryboardView | null>(null);
  const [dirOpen, setDirOpen] = useState(false);

  const go = useCallback(
    (next: StoryboardView) => {
      setDirOpen(false);
      if (sameView(next, view)) return;
      // 未保存就切走等于静默丢弃——拦下来问一句
      if (shotDirty) {
        setPendingView(next);
        return;
      }
      setView(next);
    },
    [view, shotDirty],
  );

  // 分镜被返工重跑后镜头可能变少，越界的选择退回总览
  const activeAt = view.kind === "shot" && view.at < shots.length ? view.at : null;
  const activeGroup = useMemo(() => {
    if (view.kind === "overview") return view.node ? groups.find((g) => groupKey(g.nodeIndex) === view.node) ?? null : null;
    return activeAt === null ? null : groups.find((g) => g.positions.includes(activeAt)) ?? null;
  }, [view, groups, activeAt]);

  /* ------------------------------------------------------------ 范围与补图 */

  /** 总览 = 全部；选中节点 = 该节点。再与筛选取交集。 */
  const scopePositions = useMemo(() => {
    const base = activeGroup && view.kind === "overview" ? activeGroup.positions : shots.map((_, at) => at);
    return base.filter((at) => visible.has(at));
  }, [activeGroup, view.kind, shots, visible]);

  const scopeLabel = useMemo(() => {
    const where = activeGroup && view.kind === "overview" ? groupTitle(activeGroup) : "全部镜头";
    const narrowed = [filter !== "all" ? FILTER_LABEL[filter] : "", query.trim() ? `搜索「${query.trim()}」` : ""]
      .filter(Boolean)
      .join("，");
    return narrowed ? `${where}（${narrowed}）` : where;
  }, [activeGroup, view.kind, filter, query]);

  const plan = useMemo(() => planBatch(scopePositions, facts), [scopePositions, facts]);
  const submitList = useMemo(() => submitIndexes(plan.submit, shots), [plan, shots]);
  const scopeTally = useMemo(() => tally(scopePositions, facts), [scopePositions, facts]);

  const [historyOpen, setHistoryOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  /** `null` = 还没估 / 估失败。失败整行不显示（Lead 2026-09-03 裁决）。 */
  const [estimate, setEstimate] = useState<Estimate | null>(null);

  // 打开弹窗时才估价，张数变了要重估——单价乘张数，差一张就差一笔
  useEffect(() => {
    if (!batchOpen || submitList.length === 0) return;
    let alive = true;
    setEstimate(null);
    credits
      .estimate("image.generate", { n: submitList.length })
      .then((e) => alive && setEstimate(e))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [batchOpen, submitList.length]);

  /**
   * `/shots/3/content` → `S04 · 画面内容`。路径里的下标是**数组位置**，
   * 镜号要回 `shots` 里查——Agent 不保证镜号连续。
   */
  const describePath = useCallback(
    (path: string): string => {
      const m = /^\/shots\/(\d+)\/(.+)$/.exec(path);
      if (!m) return path;
      const at = Number(m[1]);
      const field = m[2] ?? "";
      const shot = shots[at];
      const code = shot ? shotCode(shot.index) : `第 ${at + 1} 条`;
      return `${code} · ${SHOT_FIELD_LABEL[field] ?? field}`;
    },
    [shots],
  );

  /* ------------------------------------------------------------ 当前镜头 */

  const activeShot = activeAt === null ? null : shots[activeAt]!;
  const activeInfo = activeAt === null ? null : renderInfo[activeAt]!;
  const activeSubject = activeShot ? { kind: "shot" as const, index: activeShot.index } : null;
  const activeBusy =
    activeAt !== null && (activeInfo!.pending || activeInfo!.latest?.status === "queued" || activeInfo!.latest?.status === "running");
  const activeError = activeSubject ? renders.errorOf(activeSubject) : null;

  /**
   * 准备好、且还没被后来的编辑作废的那份首帧提示词。字段级编辑不碰提示词
   * 运行记录，"这份词还算不算数"只能靠时间戳比，比出来旧了就当场丢掉。
   */
  const activePromptKey = activeShot ? String(activeShot.index) : "";
  const activeEditedAt = activeAt === null ? null : edit.editedAt(`/shots/${activeAt}`);
  useEffect(() => {
    if (!activePromptKey) return;
    dropPromptEditedBefore(projectId, "shot_image", activePromptKey, activeEditedAt);
    dropPromptEditedBefore(projectId, "shot_video", activePromptKey, activeEditedAt);
  }, [projectId, activePromptKey, activeEditedAt]);
  const preparedShotPrompt = usePreparedPrompt(projectId, "shot_image", activePromptKey);

  /** 在当前筛选的可见序列里前后移动；当前镜被筛掉时退回完整序列 */
  const stepList = activeAt !== null && visible.has(activeAt) ? visibleList : shots.map((_, at) => at);
  const stepPos = activeAt === null ? -1 : stepList.indexOf(activeAt);

  /* ------------------------------------------------------------ 渲染 */

  if (shots.length === 0) {
    return (
      <div className="mx-auto flex w-full max-w-[720px] flex-col gap-4 p-4 lg:p-6">
        <div className="rf-empty-state rounded-md border border-dashed border-border-strong px-6 py-9 text-center">
          <span className="rf-empty-icon"><StoryboardIcon aria-hidden className="size-7" /></span>
          <h2 className="mt-3 text-sm font-semibold text-fg">还没有分镜产出</h2>
          <p className="mx-auto mt-1 max-w-xl text-sm leading-6 text-fg-subtle">
            推进生产把「确认剧本 → 角色档案 → 场景档案 → 分镜」这条链路跑到分镜这一步。
          </p>
        </div>
        <AdvanceAction state={state} />
      </div>
    );
  }

  /** 有未保存改动时不许准备提示词、不许出图——两件事都会把草稿悄悄丢掉。 */
  const dirtyReason = shotDirty ? "这一镜有未保存的改动，先保存再准备提示词或出图" : undefined;

  const directory = (
    <StoryboardDirectory
      groups={groups}
      shots={shots}
      facts={facts}
      visible={visible}
      query={query}
      onQuery={setQuery}
      filter={filter}
      onFilter={setFilter}
      filterCounts={filterCounts}
      view={activeAt === null && view.kind === "shot" ? { kind: "overview", node: null } : view}
      onOverview={() => go({ kind: "overview", node: null })}
      onNode={(node) => go({ kind: "overview", node })}
      onShot={(at) => go({ kind: "shot", at })}
      totalShots={shots.length}
    />
  );

  const batchButton = (
    <Button
      size="sm"
      variant="primary"
      disabled={submitList.length === 0 || batchRunning || shotDirty}
      title={
        shotDirty
          ? "当前镜头有未保存的改动，先保存再补图"
          : submitList.length === 0
            ? "这个范围里没有需要补图的镜头（已有图和生成中的不会重复提交）"
            : `为「${scopeLabel}」里缺图的 ${submitList.length} 镜逐张出图`
      }
      onClick={() => setBatchOpen(true)}
    >
      {batchRunning ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : <Layers aria-hidden className="size-3.5" />}
      补图 {submitList.length} 镜
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

  const directoryToggle = (
    <Button size="sm" className="lg:hidden" aria-expanded={dirOpen} aria-controls="sb-directory-drawer" onClick={() => setDirOpen(true)}>
      <ListTree aria-hidden className="size-3.5" />
      目录
    </Button>
  );

  return (
    <div className="flex h-full min-h-0">
      <aside aria-label="分镜目录" className="ff-sb-directory hidden shrink-0 border-r border-border bg-surface lg:block">
        {directory}
      </aside>

      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1480px] flex-col gap-4 px-4 pt-4 pb-6 lg:px-6">
          {view.kind === "overview" || activeAt === null || !activeShot || !activeInfo ? (
            <>
              <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
                {directoryToggle}
                <div className="min-w-0 flex-1">
                  <h1 className="ff-display truncate text-2xl text-fg" title={scopeLabel}>
                    {activeGroup ? groupTitle(activeGroup) : "全部镜头"}
                  </h1>
                  {activeGroup?.sceneRef && (
                    <p className="mt-0.5 truncate text-sm text-fg-muted">
                      场景：{sceneName(activeGroup.sceneRef) ?? activeGroup.sceneRef}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {historyButton}
                  {/* 来源选择必须和补图按钮在同一屏：它改变的正是按下去会发生什么 */}
                  <ImageSourcePicker runtime={runtime} disabled={batchRunning} className="w-36" />
                  {batchButton}
                </div>
              </header>

              {renders.error && (
                <p role="alert" className="flex flex-wrap items-center gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
                  {renders.error}
                  <span className="text-fg-muted">已提交的镜头照常生成；再点一次补图只会提交仍缺图的镜头。</span>
                </p>
              )}

              <StatStrip tally={scopeTally} label={`${scopeLabel}的出图统计`} />

              <GateActions state={state} gate="storyboard" />

              {!activeGroup && <NodeCoverage groups={groups} facts={facts} sceneName={sceneName} onNode={(node) => go({ kind: "overview", node })} />}

              <section aria-labelledby="sb-sheet">
                <h3 id="sb-sheet" className="mb-2 flex items-baseline gap-2 text-sm font-semibold text-fg">
                  镜头
                  <span className="tnum text-xs font-normal text-fg-subtle">
                    {scopePositions.length} 镜{filter !== "all" || query.trim() ? `（已筛选：${scopeLabel}）` : ""}
                  </span>
                </h3>
                <ScopeSheet
                  positions={scopePositions}
                  cards={cards}
                  onShot={(at) => go({ kind: "shot", at })}
                  emptyText={activeGroup && activeGroup.positions.length === 0 ? "这个节点下还没有镜头。只能返工整段分镜来补。" : "没有符合筛选的镜头。"}
                />
              </section>

              {!activeGroup && <RevisePanel state={state} target="storyboard" />}
            </>
          ) : (
            <>
              <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
                {directoryToggle}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => go({ kind: "overview", node: activeGroup ? groupKey(activeGroup.nodeIndex) : null })}
                  title="回到这个节点的总览"
                >
                  <ChevronLeft aria-hidden className="size-4" />
                  <span className="max-w-[14rem] truncate">{activeGroup ? groupTitle(activeGroup) : "全部镜头"}</span>
                </Button>
                <div className="flex min-w-0 flex-1 items-baseline gap-2">
                  <span className="ff-shot-no text-sm text-primary">{shotCode(activeShot.index)}</span>
                  <h1 className="min-w-0 truncate text-base font-semibold text-fg">{titleOf(activeShot)}</h1>
                </div>
                <span className="tnum text-xs text-fg-subtle">
                  第 {stepPos + 1} / {stepList.length} 镜{stepList === visibleList && (filter !== "all" || query.trim()) ? "（筛选内）" : ""}
                </span>
                <div className="flex items-center gap-1">
                  {historyButton}
                  <Button size="sm" variant="ghost" aria-label="上一镜" disabled={stepPos <= 0} onClick={() => go({ kind: "shot", at: stepList[stepPos - 1]! })}>
                    <ChevronLeft aria-hidden className="size-4" />
                  </Button>
                  <Button size="sm" variant="ghost" aria-label="下一镜" disabled={stepPos < 0 || stepPos >= stepList.length - 1} onClick={() => go({ kind: "shot", at: stepList[stepPos + 1]! })}>
                    <ChevronRight aria-hidden className="size-4" />
                  </Button>
                </div>
              </header>

              <ShotEditor
                // 换镜头时重建组件，草稿状态跟着一起重置——留着上一镜的草稿，
                // 用户会在这一镜上看到不属于它的内容。
                key={activeAt}
                shot={activeShot}
                shotAt={activeAt}
                card={cards[activeAt]!}
                options={options}
                edit={edit}
                imageCreatedAt={activeInfo.current?.createdAt ?? null}
                onDirtyChange={setShotDirty}
                media={
                  <ShotStage
                    code={shotCode(activeShot.index)}
                    title={titleOf(activeShot)}
                    current={activeInfo.current}
                    history={activeInfo.history}
                    urlOf={urlOf}
                    actions={
                      <>
                        <ImageSourcePicker runtime={runtime} disabled={activeBusy} className="w-36" />
                        <Button
                          size="sm"
                          variant="primary"
                          disabled={activeBusy || shotDirty}
                          title={
                            dirtyReason
                              ? dirtyReason
                              : runtime.source === "local"
                                ? "用你电脑上的 Codex 出图：消耗你自己的订阅额度，平台 Credits 仍按同价计费"
                                : activeInfo.current
                                  ? "再出一张，会再扣一次 Credits；旧图保留在记录里"
                                  : "生成这一镜的首帧图"
                          }
                          onClick={() => renders.generate(activeSubject!, runtime.source)}
                        >
                          {activeBusy ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : <Sparkles aria-hidden className="size-3.5" />}
                          {activeBusy ? "生成中" : activeInfo.current ? "再出一张" : "生成首帧图"}
                        </Button>
                        {activeInfo.latest?.status === "failed" && activeInfo.latest.taskId && (
                          <Button size="sm" disabled={activeBusy} onClick={() => renders.retry(activeSubject!, activeInfo.latest!.taskId as string)}>
                            <RefreshCw aria-hidden className="size-3.5" />
                            重试这次任务
                          </Button>
                        )}
                        <PromptPanel projectId={projectId} kind="shot_image" subjectKey={activePromptKey} disabled={activeBusy || shotDirty} disabledReason={dirtyReason} />
                        <PromptPanel projectId={projectId} kind="shot_video" subjectKey={activePromptKey} disabled={activeBusy || shotDirty} disabledReason={dirtyReason} />
                      </>
                    }
                    status={
                      <>
                        {preparedShotPrompt && !shotDirty && (
                          <p className="text-xs text-fg-subtle">出图将使用你已准备的提示词</p>
                        )}
                        {activeError && (
                          <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
                            {activeError}
                          </p>
                        )}
                        {activeInfo.latest?.status === "failed" && activeInfo.latest.errorCode && (
                          <p className="text-xs text-danger">上次出图失败，错误码：{activeInfo.latest.errorCode}。可以重试这次任务，或再出一张。</p>
                        )}
                      </>
                    }
                  />
                }
              />
            </>
          )}
        </div>
      </div>

      <Dialog
        id="sb-directory-drawer"
        open={dirOpen}
        onOpenChange={setDirOpen}
        labelledBy="sb-directory-title"
        placement="left"
        overlayClassName="bg-rf-overlay"
        className="h-full w-[min(20rem,calc(100vw-3.5rem))] border-r border-border bg-surface shadow-rf-card"
      >
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-3">
          <h2 id="sb-directory-title" className="text-sm font-semibold text-fg">
            分镜目录
          </h2>
          <DialogCloseButton label="关闭目录" onClick={() => setDirOpen(false)} className="size-9" />
        </div>
        <div className="min-h-0 flex-1">{dirOpen && directory}</div>
      </Dialog>

      <RevisionHistoryDrawer
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        edit={edit}
        title="分镜的字段改动"
        describePath={describePath}
      />

      <ConfirmDialog
        open={pendingView !== null}
        title="这一镜还有未保存的改动"
        description="离开会丢掉它们。要先回去保存，还是放弃这些改动？"
        confirmLabel="放弃改动并离开"
        tone="danger"
        onCancel={() => setPendingView(null)}
        onConfirm={() => {
          // ShotEditor 按 key 重建，草稿随之丢弃——这正是"放弃"的语义。
          if (pendingView !== null) setView(pendingView);
          setPendingView(null);
          setShotDirty(false);
        }}
      />

      {/* 估算失败时两项都是 undefined，弹窗按契约整行不显示 */}
      <BatchRenderDialog
        open={batchOpen}
        onOpenChange={setBatchOpen}
        shotCount={submitList.length}
        scopeLabel={scopeLabel}
        skippedHasImage={plan.skippedHasImage}
        skippedInFlight={plan.skippedInFlight}
        retryingFailed={plan.retryingFailed}
        imageSource={runtime.source}
        runnerLabel={runtime.provider}
        estimateCredits={estimate?.estimated_credits}
        estimateRange={estimate ? { low: estimate.range_low, high: estimate.range_high } : undefined}
        onConfirm={async () => {
          setBatchRunning(true);
          try {
            // 提交那一刻的范围：弹窗开着时有镜头完成，submitList 已经随之收缩
            await renders.generateMany(
              submitList.map((index) => ({ kind: "shot" as const, index })),
              runtime.source,
            );
          } finally {
            setBatchRunning(false);
          }
        }}
      />
    </div>
  );
}
