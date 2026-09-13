"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { History, Layers, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { StoryboardIcon } from "@/components/icons/studio-icons";
import { ConfirmDialog } from "@/components/freeflow/project/feedback";
import { BatchRenderDialog } from "@/components/freeflow/storyboard/batch-render-dialog";
import { RevisionHistoryDrawer } from "@/components/freeflow/storyboard/revision-history";
import { ShotGrid, type ShotCardData } from "@/components/freeflow/storyboard/shot-card";
import { SHOT_FIELD_LABEL, type ShotOptions } from "@/components/freeflow/storyboard/shot-detail";
import { ShotEditor, type Shot } from "@/components/freeflow/storyboard/shot-editor";
import { ImageSourcePicker } from "@/components/project/image-source-picker";
import { Button } from "@/components/ui/button";
import { credits, type Estimate } from "@/lib/api";
import { defaultLightingOf, lightingStatesOf } from "@/lib/freeflow/scene-shape";
import { useContentEdit } from "@/lib/freeflow/use-content-edit";
import { useLocalRuntime } from "@/lib/freeflow/use-local-runtime";
import { dropPromptEditedBefore, usePreparedPrompt } from "@/lib/freeflow/prepared-prompts";
import type { Images } from "@/lib/freeflow/use-images";
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
 */

/** 卡片标题。后端每一镜唯一能当标题的只有 `content`，截一段用，全文在详情里。 */
function titleOf(shot: Shot): string {
  const text = (shot.content ?? "").trim();
  if (!text) return `镜头 ${shot.index}`;
  return text.length > 24 ? `${text.slice(0, 24)}…` : text;
}

/**
 * 卡片状态。契约里这四个值说的是**这一镜能不能生成**，不是「有没有图」——
 * 有没有图由 `imageUrl` 表达（卡片直接把图画出来）。
 *
 *   rendering 生成中 / failed 失败  ← 出图任务的实时状态
 *   draft     待完善               ← 画面内容是空的，出图只会得到一张废图
 *   ready     可生成               ← 其余
 *
 * 「图可能过期」不在这四个值里，它是另一个维度（`outdated`）：一张过期的图
 * 仍然是一张成功出来的图，把它算成 failed 会让重试按钮出现在不该出现的地方。
 */
function statusOf(shot: Shot, view: ReturnType<Images["renderOf"]>): ShotCardData["status"] {
  if (view?.status === "running" || view?.status === "queued") return "rendering";
  if (view?.status === "failed") return "failed";
  return (shot.content ?? "").trim() ? "ready" : "draft";
}

/**
 * 镜头工作台。
 *
 * 四件写库的事：**逐字段编辑分镜**（ADR-029，不花钱、可整批撤销）、
 * 单镜出图、批量（逐镜循环）出图、过「确认分镜」门。视频不在内——
 * M2 才有那条链路，没有假入口。
 *
 * 改分镜有两条路，界面上都在：
 * - 字段级编辑（这里）：确定性、即时、一分钱不花，撤销以一次保存为单位。
 * - 自然语言返工（下面的 `RevisePanel`）：重跑整个 Agent，会扣 Credits，
 *   适合"整段重写"这种说不清改哪几个字段的需求。
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

  const shots: Shot[] = useMemo(() => storyboard?.shots ?? [], [storyboard]);

  const edit = useContentEdit(projectId, "storyboard", state);

  /**
   * 出图来源（平台 API / 本机 Codex）。
   *
   * 分镜以前**根本没有**这个入口：`ImageSourcePicker` 只挂在 `RenderSlot`
   * 上，而分镜页面不用 `RenderSlot`（镜头没有"基准图"那组动作）。于是
   * 用户在角色卡上选了「本机」，界面上到处显示本机选中，一到分镜出图就
   * 悄悄走回平台 API——26 镜的批量出图正是花钱最多的那一次。
   * 状态与选择都是全站一份（见 `use-local-runtime.ts`），这里接一次不会
   * 多发请求，也不会和角色/场景页的选择打架。
   */
  const runtime = useLocalRuntime(projectId);

  /**
   * 场景 ref 的建议列表。
   *
   * 场景档案里的排在前面；分镜节点上出现过、但档案里还没有的补在后面，
   * 名字用节点的 `summary`。分镜可以跑在场景档案之前，那时候档案是空的，
   * 只列档案会让建议框整个是空的，用户只能凭记忆敲 ref。
   */
  const sceneOptions = useMemo(() => {
    const out: { ref: string; name: string }[] = [];
    const seen = new Set<string>();
    for (const s of scenes?.scenes ?? []) {
      if (typeof s?.ref !== "string" || seen.has(s.ref)) continue;
      seen.add(s.ref);
      out.push({ ref: s.ref, name: String(s.name ?? s.ref) });
    }
    for (const n of storyboard?.nodes ?? []) {
      if (typeof n?.scene_ref !== "string" || seen.has(n.scene_ref)) continue;
      seen.add(n.scene_ref);
      out.push({ ref: n.scene_ref, name: String(n.summary ?? n.scene_ref) });
    }
    return out;
  }, [scenes, storyboard]);

  const characterOptions = useMemo(
    () =>
      (characters?.characters ?? [])
        .filter((c: any) => typeof c?.ref === "string")
        .map((c: any) => ({ ref: c.ref as string, name: String(c.name ?? c.ref) })),
    [characters],
  );

  /**
   * 每个场景各自的光照状态，以及它的默认状态。
   *
   * **按 scene_ref 分组，不取并集**：光照是「这个地点出现过的几种光」，
   * 把渡口的晨雾放进审讯室的下拉里，用户选了之后后端只会记一条 warning
   * 然后回落到默认——一个选了必然出错、且错在界面上看不见的选项。
   */
  const lighting = useMemo(() => {
    const byScene: Record<string, { ref: string; name: string }[]> = {};
    const defaults: Record<string, string> = {};
    for (const scene of scenes?.scenes ?? []) {
      if (typeof scene?.ref !== "string") continue;
      // `ref` 放状态名本身——后端 `lighting_ref` 存的就是名字，不是 id。
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

  const [selected, setSelected] = useState(0);
  /** 有未保存改动时点了别的镜头，先把目标记在这里等用户确认。 */
  const [pendingSelect, setPendingSelect] = useState<number | null>(null);
  const [shotDirty, setShotDirty] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  /** `null` = 还没估 / 估失败。失败整行不显示（Lead 2026-09-03 裁决）。 */
  const [estimate, setEstimate] = useState<Estimate | null>(null);

  // 预签名地址成批签一次，不让每张卡片各自去签
  const urlOf = useAssetUrls(
    useMemo(
      () => shots.map((s) => renders.renderOf({ kind: "shot", index: s.index })?.assetId),
      [shots, renders],
    ),
  );

  const cards: ShotCardData[] = useMemo(
    () =>
      shots.map((shot, at) => {
        const view = renders.renderOf({ kind: "shot", index: shot.index });
        const editedAt = edit.editedAt(`/shots/${at}`);
        const states = lighting.byScene[shot.scene_ref] ?? [];
        const ref = (shot.lighting_ref ?? "").trim();
        // 留空是合法的"跟随默认"，卡片上显示解析后的那一个；
        // 引用了一个该场景没有的名字才是要标出来的错。
        const unknown = ref !== "" && !states.some((s) => s.ref === ref);
        return {
          index: shot.index,
          code: `S${String(shot.index).padStart(2, "0")}`,
          title: titleOf(shot),
          framing: shot.shot_size,
          camera: shot.camera_move || shot.angle,
          lighting: ref || lighting.defaults[shot.scene_ref] || undefined,
          lightingUnknown: unknown,
          // 后端 StoryboardShot 没有时长（schema 注释：时长来自 TTS 的真实音频
          // 长度）。不传这个字段，详情里那一格就不会渲染出来。
          durationLabel: undefined,
          status: statusOf(shot, view),
          imageUrl: urlOf(view?.assetId),
          // 图比这一镜最后一次改动旧 = 可能对不上。只标记，不自动重跑。
          outdated:
            Boolean(view?.assetId) &&
            editedAt !== null &&
            Date.parse(view?.createdAt ?? "") < editedAt,
        };
      }),
    [shots, renders, urlOf, edit, lighting],
  );

  /** 还没出过图的镜号。批量只跑这些，不重出已有的——重出是再花一次钱。 */
  const missing = useMemo(
    () => shots.filter((s) => !renders.renderOf({ kind: "shot", index: s.index })?.assetId),
    [shots, renders],
  );

  const outdatedCount = useMemo(() => cards.filter((c) => c.outdated).length, [cards]);

  /**
   * `/shots/3/content` → `S04 · 画面内容`。
   *
   * 路径里的下标是**数组位置**，镜号要回 `shots` 里查——两者在正常产出里
   * 差 1，但 Agent 不保证镜号连续，直接 +1 显示会指到另一镜上。
   */
  const describePath = useCallback(
    (path: string): string => {
      const m = /^\/shots\/(\d+)\/(.+)$/.exec(path);
      if (!m) return path;
      const at = Number(m[1]);
      const field = m[2] ?? "";
      const shot = shots[at];
      const code = shot ? `S${String(shot.index).padStart(2, "0")}` : `第 ${at + 1} 条`;
      return `${code} · ${SHOT_FIELD_LABEL[field] ?? field}`;
    },
    [shots],
  );

  const selectShot = useCallback(
    (next: number) => {
      if (next === selected) return;
      // 未保存就切走等于静默丢弃——上一轮刚删掉的就是这种假编辑。
      if (shotDirty) {
        setPendingSelect(next);
        return;
      }
      setSelected(next);
    },
    [selected, shotDirty],
  );

  // 打开弹窗时才估价：这一条只是算一下（不建任务、不预扣），没必要页面一加载就发。
  // 张数变了要重估——单价乘张数，差一张就差一笔。
  useEffect(() => {
    if (!batchOpen || missing.length === 0) return;
    let alive = true;
    setEstimate(null);
    credits
      .estimate("image.generate", { n: missing.length })
      .then((e) => alive && setEstimate(e))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [batchOpen, missing.length]);

  if (shots.length === 0) {
    return (
      <div className="mx-auto flex w-full max-w-[720px] flex-col gap-4 p-4 lg:p-6">
        <div className="rf-empty-state rounded-2xl border border-dashed border-border-strong px-6 py-9 text-center">
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

  // `shots.length === 0` 在上面已经返回了，`activeAt` 又钳在 [0, length)，
  // 所以这两个取不到 undefined；`!` 是给 noUncheckedIndexedAccess 看的。
  const activeAt = selected < shots.length ? selected : 0;
  const activeShot = shots[activeAt]!;
  const activeCard = cards[activeAt]!;
  const activeSubject = { kind: "shot" as const, index: activeShot.index };
  const activeView = renders.renderOf(activeSubject);
  const activeBusy =
    renders.isPending(activeSubject) ||
    activeView?.status === "queued" ||
    activeView?.status === "running";
  const activeError = renders.errorOf(activeSubject);

  /**
   * 这一镜已经准备好、且还没被后来的编辑作废的那份首帧提示词。
   *
   * 字段级编辑（ADR-029）只改 `current_state_json`，不会碰任何一条提示词
   * 运行记录，所以"这份词还算不算数"只能靠时间戳比——和上面卡片判断
   * 「图是不是过期」用的是同一招。比出来旧了就当场丢掉，免得出图时
   * 拿一个后端一定会拒的 run_id 去撞一次。
   */
  const activePromptKey = String(activeShot.index);
  const activeEditedAt = edit.editedAt(`/shots/${activeAt}`);
  useEffect(() => {
    dropPromptEditedBefore(projectId, "shot_image", activePromptKey, activeEditedAt);
    dropPromptEditedBefore(projectId, "shot_video", activePromptKey, activeEditedAt);
  }, [projectId, activePromptKey, activeEditedAt]);
  const preparedShotPrompt = usePreparedPrompt(projectId, "shot_image", activePromptKey);

  /** 有未保存改动时不许准备提示词、不许出图——两件事都会把草稿悄悄丢掉。 */
  const dirtyReason = shotDirty ? "这一镜有未保存的改动，先保存再准备提示词或出图" : undefined;

  return (
    <div className="mx-auto flex w-full max-w-[1360px] flex-col gap-4 p-4 lg:p-6">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-sm font-semibold text-fg">镜头工作台</h1>
        <span className="tnum text-xs text-fg-subtle">
          共 {shots.length} 镜 · {shots.length - missing.length} 镜已有首帧图
          {outdatedCount > 0 && ` · ${outdatedCount} 张图可能过期`}
        </span>

        <Button
          size="sm"
          className="ml-auto"
          aria-expanded={historyOpen}
          onClick={() => setHistoryOpen(true)}
        >
          <History aria-hidden className="size-3.5" />
          改动记录
          {edit.loadedCount > 0 && (
            <span className="tnum text-fg-subtle">
              {edit.loadedCount}
              {edit.hasMore ? "+" : ""}
            </span>
          )}
        </Button>

        {/* 来源选择必须和出图按钮在同一屏里：它改变的正是这两颗按钮按下去
            会发生什么。没配这个试点的项目里它不渲染任何东西。 */}
        <ImageSourcePicker runtime={runtime} disabled={batchRunning} className="w-36" />

        <Button
          size="sm"
          variant="primary"
          disabled={missing.length === 0 || batchRunning || shotDirty}
          title={
            shotDirty
              ? "当前镜头有未保存的改动，先保存再批量出图"
              : missing.length === 0
                ? "每个镜号都已经有图了"
                : runtime.source === "local"
                  ? `逐镜生成 ${missing.length} 张，走你电脑上的 Codex；平台 Credits 仍按同价各扣一次`
                  : `逐镜生成 ${missing.length} 张，每张各扣一次 Credits`
          }
          onClick={() => setBatchOpen(true)}
        >
          {batchRunning ? (
            <Loader2 aria-hidden className="size-3.5 animate-spin" />
          ) : (
            <Layers aria-hidden className="size-3.5" />
          )}
          批量出图（{missing.length}）
        </Button>
      </div>

      {renders.error && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
          {renders.error}
        </p>
      )}

      <GateActions state={state} gate="storyboard" />

      <ShotEditor
        // 换镜头时重建组件，草稿状态跟着一起重置——留着上一镜的草稿，
        // 用户会在这一镜上看到不属于它的内容。
        key={activeAt}
        shot={activeShot}
        shotAt={activeAt}
        card={activeCard}
        options={options}
        edit={edit}
        imageCreatedAt={activeView?.assetId ? (activeView.createdAt ?? null) : null}
        onDirtyChange={setShotDirty}
        actions={
          <>
            <PromptPanel projectId={projectId} kind="shot_image" subjectKey={activePromptKey} disabled={activeBusy || shotDirty} disabledReason={dirtyReason} />
            <PromptPanel projectId={projectId} kind="shot_video" subjectKey={activePromptKey} disabled={activeBusy || shotDirty} disabledReason={dirtyReason} />
            <Button
              size="sm"
              variant="primary"
              disabled={activeBusy || shotDirty}
              title={
                dirtyReason
                  ? dirtyReason
                  : runtime.source === "local"
                    ? "用你电脑上的 Codex 出图：消耗你自己的订阅额度，平台 Credits 仍按同价计费"
                    : activeView?.assetId
                      ? "再出一张，会再扣一次 Credits"
                      : "生成这一镜的首帧图"
              }
              onClick={() => renders.generate(activeSubject, runtime.source)}
            >
              {activeBusy ? (
                <Loader2 aria-hidden className="size-3.5 animate-spin" />
              ) : (
                <Sparkles aria-hidden className="size-3.5" />
              )}
              {activeView?.assetId ? "再出一张" : "生成首帧图"}
            </Button>
            {preparedShotPrompt && !shotDirty && (
              <span className="text-xs text-fg-subtle">出图将使用你已准备的提示词</span>
            )}
            {activeView?.status === "failed" && activeView.taskId && (
              <Button
                size="sm"
                disabled={activeBusy}
                onClick={() => renders.retry(activeSubject, activeView.taskId as string)}
              >
                <RefreshCw aria-hidden className="size-3.5" />
                重试这次任务
              </Button>
            )}
          </>
        }
      />

      {activeError && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
          {activeError}
        </p>
      )}
      {activeView?.status === "failed" && activeView.errorCode && (
        <p className="text-xs text-danger">失败错误码：{activeView.errorCode}</p>
      )}

      <ShotGrid shots={cards} selectedIndex={activeAt} onSelect={selectShot} />

      <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-xs leading-5 text-fg-subtle">
        先保存镜头修改，再准备提示词或出图。提示词会结合景别、角度、角色和场景设定，
        沿用已确认的画风。已有图片会保留；整段重写可使用下面的返工操作。
      </p>

      <RevisePanel state={state} target="storyboard" />

      <RevisionHistoryDrawer
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        edit={edit}
        title="分镜的字段改动"
        describePath={describePath}
      />

      <ConfirmDialog
        open={pendingSelect !== null}
        title="这一镜还有未保存的改动"
        description="切到别的镜头会丢掉它们。要先回去保存，还是放弃这些改动？"
        confirmLabel="放弃改动并切换"
        tone="danger"
        onCancel={() => setPendingSelect(null)}
        onConfirm={() => {
          // ShotEditor 按 key 重建，草稿随之丢弃——这正是"放弃"的语义。
          if (pendingSelect !== null) setSelected(pendingSelect);
          setPendingSelect(null);
        }}
      />

      {/* 估算失败时 estimateCredits / estimateRange 都是 undefined，
          弹窗按契约整行不显示——不给用户看一条「估算失败」的红条。 */}
      <BatchRenderDialog
        open={batchOpen}
        onOpenChange={setBatchOpen}
        shotCount={missing.length}
        imageSource={runtime.source}
        runnerLabel={runtime.provider}
        estimateCredits={estimate?.estimated_credits}
        estimateRange={estimate ? { low: estimate.range_low, high: estimate.range_high } : undefined}
        onConfirm={async () => {
          setBatchRunning(true);
          try {
            await renders.generateMany(
              missing.map((s) => ({ kind: "shot" as const, index: s.index })),
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
