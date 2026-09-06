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
import { Button } from "@/components/ui/button";
import { credits, type Estimate } from "@/lib/api";
import { useContentEdit } from "@/lib/freeflow/use-content-edit";
import type { Images } from "@/lib/freeflow/use-images";
import type { ProjectState } from "@/lib/freeflow/use-project-state";
import { useAssetUrls } from "@/lib/freeflow/use-asset-urls";

import { AdvanceAction, GateActions } from "./production-actions";
import { RevisePanel } from "./revise-panel";

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

  const options: ShotOptions = useMemo(
    () => ({ scenes: sceneOptions, characters: characterOptions }),
    [sceneOptions, characterOptions],
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
        return {
          index: shot.index,
          code: `S${String(shot.index).padStart(2, "0")}`,
          title: titleOf(shot),
          framing: shot.shot_size,
          camera: shot.camera_move || shot.angle,
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
    [shots, renders, urlOf, edit],
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

        <Button
          size="sm"
          variant="primary"
          disabled={missing.length === 0 || batchRunning}
          title={
            missing.length === 0
              ? "每个镜号都已经有图了"
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
            <Button
              size="sm"
              variant="primary"
              disabled={activeBusy}
              title={activeView?.assetId ? "再出一张，会再扣一次 Credits" : "生成这一镜的首帧图"}
              onClick={() => renders.generate(activeSubject)}
            >
              {activeBusy ? (
                <Loader2 aria-hidden className="size-3.5 animate-spin" />
              ) : (
                <Sparkles aria-hidden className="size-3.5" />
              )}
              {activeView?.assetId ? "再出一张" : "生成首帧图"}
            </Button>
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
        镜头字段可以直接改，保存即写回后端（ADR-029），不花 Credits、不重跑 Agent，
        每次保存都能在「改动记录」里整批撤销。镜号和节点号只读——它们是出图记录和
        节点覆盖核验的关联键，要动只能重跑分镜。整段重写用下面的自然语言返工。
        提示词里的风格词由一致性引擎统一注入，前端传不了也不该传。
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
        estimateCredits={estimate?.estimated_credits}
        estimateRange={estimate ? { low: estimate.range_low, high: estimate.range_high } : undefined}
        onConfirm={async () => {
          setBatchRunning(true);
          try {
            await renders.generateMany(
              missing.map((s) => ({ kind: "shot" as const, index: s.index })),
            );
          } finally {
            setBatchRunning(false);
          }
        }}
      />
    </div>
  );
}
