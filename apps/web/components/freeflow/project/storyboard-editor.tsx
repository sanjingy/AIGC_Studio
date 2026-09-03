"use client";

import { useEffect, useMemo, useState } from "react";
import { Layers, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { BatchRenderDialog } from "@/components/freeflow/storyboard/batch-render-dialog";
import { ShotGrid, type ShotCardData } from "@/components/freeflow/storyboard/shot-card";
import { ShotDetail } from "@/components/freeflow/storyboard/shot-detail";
import { Button } from "@/components/ui/button";
import { credits, type Estimate } from "@/lib/api";
import type { Images } from "@/lib/freeflow/use-images";
import type { ProjectState } from "@/lib/freeflow/use-project-state";
import { useAssetUrls } from "@/lib/freeflow/use-asset-urls";

import { AdvanceAction, GateActions } from "./production-actions";
import { RevisePanel } from "./revise-panel";

/* ------------------------------------------------------------------ 数据形状
 *
 * 分镜数据形状的权威定义在后端 `agents/schemas.py` 的
 * Storyboard / StoryboardShot。这里不另设计一套：分叉之后，同一份
 * output_json 在不同页面上会显示出不同的内容。
 * （读法最早抄自旧壳的 `components/project/storyboard-view.tsx`，
 * 那个文件已随旧壳删除。）
 */
type Shot = {
  index: number;
  node_index: number;
  scene_ref: string;
  character_refs: string[];
  shot_size: string;
  angle: string;
  camera_move: string;
  content: string;
  speaker_ref: string;
  dialogue: string;
  sfx: string;
};

type DetailShot = ShotCardData & {
  description: string;
  characters: string[];
  scene?: string;
  dialogue?: string;
};

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
 */
function statusOf(shot: Shot, view: ReturnType<Images["renderOf"]>): ShotCardData["status"] {
  if (view?.status === "running" || view?.status === "queued") return "rendering";
  if (view?.status === "failed") return "failed";
  return (shot.content ?? "").trim() ? "ready" : "draft";
}

/**
 * 镜头工作台。
 *
 * 能做三件写库的事：单镜出图、批量（逐镜循环）出图、过「确认分镜」门。
 * 视频不在内——M2 才有那条链路，没有假入口。
 *
 * 为什么**没有**逐字段编辑：字段级 Patch 的后端（ADR-029 的 content 模块）
 * 今天没有提交，那条写路径还不存在。改分镜目前只有一条真路径——下面的
 * 自然语言返工（`/revise`），它重跑同一个 Agent、同一个 schema。
 */
export function StoryboardEditor({ state, renders }: { state: ProjectState; renders: Images }) {
  const storyboard = state.output.storyboard;
  const scenes = state.output.scenes;
  const characters = state.output.characters;

  const shots: Shot[] = useMemo(() => storyboard?.shots ?? [], [storyboard]);

  const sceneName = useMemo(() => {
    const map = new Map<string, string>();
    for (const s of scenes?.scenes ?? []) map.set(s.ref, s.name);
    // 场景档案还没跑时退回分镜节点的 summary——它至少是真实产出，比 ref 可读
    for (const n of storyboard?.nodes ?? []) {
      if (!map.has(n.scene_ref) && n.summary) map.set(n.scene_ref, n.summary);
    }
    return map;
  }, [scenes, storyboard]);

  const characterName = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of characters?.characters ?? []) map.set(c.ref, c.name);
    return map;
  }, [characters]);

  const [selected, setSelected] = useState(0);
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

  const cards: DetailShot[] = useMemo(
    () =>
      shots.map((shot) => {
        const view = renders.renderOf({ kind: "shot", index: shot.index });
        const speaker = shot.speaker_ref ? characterName.get(shot.speaker_ref) : undefined;
        const spoken = shot.dialogue
          ? `${speaker ? speaker + "：" : ""}${shot.dialogue}`
          : "";
        const sound = shot.sfx ? `音效：${shot.sfx}` : "";
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
          description: shot.content,
          characters: shot.character_refs.map((ref) => characterName.get(ref) ?? ref),
          scene: sceneName.get(shot.scene_ref) ?? shot.scene_ref,
          dialogue: [spoken, sound].filter(Boolean).join("\n") || undefined,
        };
      }),
    [shots, renders, urlOf, characterName, sceneName],
  );

  /** 还没出过图的镜号。批量只跑这些，不重出已有的——重出是再花一次钱。 */
  const missing = useMemo(
    () => shots.filter((s) => !renders.renderOf({ kind: "shot", index: s.index })?.assetId),
    [shots, renders],
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
        <p className="rounded-2xl border border-border bg-surface px-6 py-8 text-center text-sm leading-6 text-fg-subtle">
          还没有分镜产出。推进生产把「确认剧本 → 角色档案 → 场景档案 → 分镜」这条链路跑到分镜这一步。
        </p>
        <AdvanceAction state={state} />
      </div>
    );
  }

  const activeShot = cards[selected] ?? cards[0];
  const activeSubject = activeShot ? { kind: "shot" as const, index: activeShot.index } : null;
  const activeView = activeSubject ? renders.renderOf(activeSubject) : null;
  const activeBusy =
    (activeSubject ? renders.isPending(activeSubject) : false) ||
    activeView?.status === "queued" ||
    activeView?.status === "running";
  const activeError = activeSubject ? renders.errorOf(activeSubject) : null;

  return (
    <div className="mx-auto flex w-full max-w-[1360px] flex-col gap-4 p-4 lg:p-6">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-sm font-semibold text-fg">镜头工作台</h1>
        <span className="tnum text-xs text-fg-subtle">
          共 {shots.length} 镜 · {shots.length - missing.length} 镜已有首帧图
        </span>
        <Button
          size="sm"
          variant="primary"
          className="ml-auto"
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

      {activeShot && (
        <ShotDetail
          shot={activeShot}
          actions={
            <>
              <Button
                size="sm"
                variant="primary"
                disabled={activeBusy}
                title={activeView?.assetId ? "再出一张，会再扣一次 Credits" : "生成这一镜的首帧图"}
                onClick={() => activeSubject && renders.generate(activeSubject)}
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
                  onClick={() =>
                    activeSubject && renders.retry(activeSubject, activeView.taskId as string)
                  }
                >
                  <RefreshCw aria-hidden className="size-3.5" />
                  重试这次任务
                </Button>
              )}
            </>
          }
        />
      )}

      {activeError && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
          {activeError}
        </p>
      )}
      {activeView?.status === "failed" && activeView.errorCode && (
        <p className="text-xs text-danger">失败错误码：{activeView.errorCode}</p>
      )}

      <ShotGrid shots={cards} selectedIndex={selected} onSelect={setSelected} />

      <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-xs leading-5 text-fg-subtle">
        镜头参数是只读的。分镜的写路径目前只有下面的自然语言返工——字段级 Patch
        的后端（ADR-029）今天没有提交。提示词里的风格词由一致性引擎统一注入，前端传不了也不该传。
      </p>

      <RevisePanel state={state} target="storyboard" />

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
