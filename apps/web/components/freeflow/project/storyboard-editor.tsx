"use client";

import { useEffect, useMemo, useState } from "react";
import { Image as ImageIcon, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { assets as assetsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Renders } from "@/lib/useRenders";

import { ConfirmDialog, Notice, useNotice } from "./feedback";

/* ------------------------------------------------------------------ 数据形状
 *
 * 分镜数据形状**照抄** `components/project/storyboard-view.tsx` 的读法，
 * 后端权威定义在 `agents/schemas.py` 的 Storyboard / StoryboardShot。
 * 这里不重新设计一套：两边分叉之后，同一份 output_json 在两个页面上
 * 会显示出不同的内容。
 *
 * 后端**没有**的字段（本文件里出现的）：时长、情绪/氛围、参考图。
 * StoryboardShot 的注释写得很清楚——时长来自 TTS 的真实音频长度，
 * 是确定性计算，不让模型填。所以这三项在本轮里只是本地草稿。
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

/** 与 agents/schemas.py 的 STORYBOARD_SHOT_SIZES 一一对应。改那边要同步这里。 */
const SHOT_SIZES = [
  "极近特写",
  "特写",
  "近景胸像",
  "中景腰部",
  "中全景",
  "全景",
  "大全景",
  "航拍俯瞰",
] as const;

/** camera_move 后端是自由文本（max 40），所以这只是常用值；
 *  渲染时会把产出里已有的值并进来，避免下拉框把真实数据吃掉。 */
const CAMERA_MOVES = ["固定", "推进", "拉远", "横移", "跟随", "摇摄", "环绕", "手持"];

/** 情绪/氛围完全是前端概念——StoryboardShot 里没有这个字段。 */
const MOODS = ["紧张", "压抑", "克制", "悬疑", "温柔", "孤寂", "激烈", "希望"];

/** 示例视频模型列表。后端还没有视频模型目录（M2 待办），
 *  接上之后要换成 model_pricing 里的真实条目，不要把名字写死在这里。 */
const VIDEO_MODELS = ["Seedance 1.0 · 1080P", "万相 2.5 · 720P", "可灵 1.6 · 1080P"];

const NOT_WIRED_VIDEO =
  "视频生成后端未接入：M2 才做 TTS → 时间线 → ffmpeg 合成（见 CLAUDE.md「M2 待办」）。这里不会真的发起调用，也不会扣 Credits。";

type ShotDraft = {
  shotSize: string;
  cameraMove: string;
  durationSec: string;
  content: string;
  characterRefs: string[];
  moods: string[];
};

function draftFrom(shot: Shot): ShotDraft {
  return {
    shotSize: shot.shot_size ?? "",
    cameraMove: shot.camera_move ?? "",
    // 后端不存时长，给一个约定的默认值，不是从数据里读出来的
    durationSec: "3",
    content: shot.content ?? "",
    characterRefs: shot.character_refs ?? [],
    moods: [],
  };
}

export function StoryboardEditor({
  storyboard,
  scenes,
  characters,
  renders,
}: {
  /** output.storyboard，即 visual.storyboard.v1 的 output_json */
  storyboard: any;
  /** output.scenes（SceneSheets），只用来把 scene_ref 翻成人看得懂的名字 */
  scenes: any;
  /** output.characters（CharacterSheets），同上，把 character_ref 翻成人名 */
  characters: any;
  renders: Renders;
}) {
  const shots: Shot[] = useMemo(() => storyboard?.shots ?? [], [storyboard]);

  const sceneName = useMemo(() => {
    const map = new Map<string, string>();
    for (const s of scenes?.scenes ?? []) map.set(s.ref, s.name);
    // 场景档案还没跑时退回分镜节点的 summary——它至少是真实产出，
    // 比直接显示 ref 可读
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

  /** 按镜头里出现的顺序分组，不按场景档案的顺序——分镜表才是这一页的真相 */
  const sceneGroups = useMemo(() => {
    const order: string[] = [];
    const byRef = new Map<string, Shot[]>();
    for (const s of shots) {
      let bucket = byRef.get(s.scene_ref);
      if (!bucket) {
        bucket = [];
        byRef.set(s.scene_ref, bucket);
        order.push(s.scene_ref);
      }
      bucket.push(s);
    }
    return order.map((ref, i) => ({
      ref,
      label: `Scene ${String(i + 1).padStart(2, "0")}`,
      name: sceneName.get(ref) ?? ref,
      shots: byRef.get(ref) ?? [],
    }));
  }, [shots, sceneName]);

  const [sceneRef, setSceneRef] = useState<string | null>(null);
  const [shotIndex, setShotIndex] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<number, ShotDraft>>({});
  const [confirmRegen, setConfirmRegen] = useState(false);
  const [modelPicker, setModelPicker] = useState(false);
  const [model, setModel] = useState(VIDEO_MODELS[0]);
  const { notice, say, clear } = useNotice();

  const activeScene = sceneGroups.find((g) => g.ref === sceneRef) ?? sceneGroups[0] ?? null;
  const activeShot =
    activeScene?.shots.find((s) => s.index === shotIndex) ?? activeScene?.shots[0] ?? null;

  const draft = activeShot ? (drafts[activeShot.index] ?? draftFrom(activeShot)) : null;

  const view = activeShot ? renders.renderOf({ kind: "shot", index: activeShot.index }) : null;
  // 视频产出后端还没有，所以这里用「该镜头是否已经出过分镜图」代替
  // REQ-040 里的"是否已有产出"，好让三个按钮的语义差别能被看见。
  // 接上 M2 的视频接口后，这个判据要换成视频产出。
  const hasOutput = !!view?.assetId;

  if (sceneGroups.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-6">
        <p className="max-w-sm text-center text-sm text-fg-subtle">
          还没有分镜产出。先在工作台把「确认剧本 → 角色档案 → 场景档案 → 分镜」这条链路跑到分镜这一步。
        </p>
      </div>
    );
  }

  const patch = (p: Partial<ShotDraft>) => {
    if (!activeShot || !draft) return;
    setDrafts((d) => ({ ...d, [activeShot.index]: { ...draft, ...p } }));
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-col gap-2 border-b border-border bg-surface px-4 py-2">
        <div className="flex items-baseline gap-2">
          <h1 className="text-sm font-semibold text-fg">分镜编辑 · {activeScene?.label}</h1>
          <span className="truncate text-xs text-fg-subtle">
            {activeScene?.name} · 共 {shots.length} 个镜号
          </span>
        </div>
        <p className="text-xs text-fg-subtle">
          原型阶段：右栏字段改动只存在本地，不写回后端——分镜写回目前只有{" "}
          <code className="rounded-sm bg-surface-2 px-1">/projects/&#123;id&#125;/revise</code>{" "}
          一条路，它收的是整段自然语言指令，不是单镜字段。
        </p>
        <Notice notice={notice} onClose={clear} />
      </div>

      <div className="flex min-h-0 flex-1">
        {/* 左栏：场景列表 */}
        <aside className="flex w-[150px] shrink-0 flex-col gap-1.5 overflow-y-auto border-r border-border bg-surface p-2.5">
          {sceneGroups.map((g) => {
            const active = g.ref === activeScene?.ref;
            return (
              <button
                key={g.ref}
                type="button"
                aria-current={active ? "true" : undefined}
                onClick={() => {
                  setSceneRef(g.ref);
                  setShotIndex(g.shots[0]?.index ?? null);
                }}
                className={cn(
                  "cursor-pointer rounded-lg border px-2.5 py-2 text-left transition-colors duration-150",
                  active
                    ? "border-primary/30 bg-primary-soft"
                    : "border-border bg-surface hover:bg-surface-2",
                )}
              >
                <div className={cn("text-xs font-medium", active ? "text-primary" : "text-fg")}>
                  {g.label}
                </div>
                <div className="mt-0.5 truncate text-xs text-fg-subtle" title={g.name}>
                  {g.name}
                </div>
                <div className="tnum mt-0.5 text-xs text-fg-subtle">{g.shots.length} 镜头</div>
              </button>
            );
          })}
        </aside>

        {/* 中栏：选中场景下的镜头卡片 */}
        <div className="flex w-[280px] shrink-0 flex-col gap-1.5 overflow-y-auto border-r border-border bg-bg p-2.5">
          {activeScene?.shots.map((s) => {
            const active = s.index === activeShot?.index;
            const thumb = renders.renderOf({ kind: "shot", index: s.index });
            return (
              <button
                key={s.index}
                type="button"
                aria-current={active ? "true" : undefined}
                onClick={() => setShotIndex(s.index)}
                className={cn(
                  "flex cursor-pointer items-start gap-2.5 rounded-lg border p-2 text-left transition-colors duration-150",
                  active
                    ? "border-primary/40 bg-primary-soft"
                    : "border-border bg-surface hover:bg-surface-2",
                )}
              >
                <div className="flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-md bg-surface-2">
                  {thumb?.assetId ? (
                    <ShotThumb assetId={thumb.assetId} alt={`第 ${s.index} 镜`} />
                  ) : (
                    <ImageIcon aria-hidden className="size-4 text-fg-subtle" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="tnum text-xs text-fg-subtle">
                    镜头 {s.index} · {s.shot_size}
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-xs leading-4 text-fg-muted">
                    {s.content}
                  </p>
                </div>
              </button>
            );
          })}

          <button
            type="button"
            onClick={() =>
              say(
                "原型阶段未接：增删镜头要写回 visual.storyboard.v1 的产出，后端目前只能整段重生成（/revise），没有单镜增删接口。",
              )
            }
            className="flex cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-dashed border-border-strong px-2 py-2 text-xs text-fg-subtle hover:bg-surface-2 hover:text-fg"
          >
            <Plus aria-hidden className="size-3.5" />
            添加镜头
          </button>
        </div>

        {/* 右栏：镜头参数 */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {activeShot && draft && (
            <div className="flex max-w-[420px] flex-col gap-3.5 p-4">
              <div className="flex items-baseline gap-2">
                <h2 className="tnum text-sm font-semibold text-fg">镜头 {activeShot.index}</h2>
                <span className="text-xs text-fg-subtle">
                  节点 {activeShot.node_index}
                  {activeShot.angle ? ` · ${activeShot.angle}` : ""}
                </span>
              </div>

              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-fg">镜头类型（景别）</span>
                <select
                  value={draft.shotSize}
                  onChange={(e) => patch({ shotSize: e.target.value })}
                  className="h-8 cursor-pointer rounded-md border border-border-strong bg-surface px-2 text-sm text-fg"
                >
                  {/* 产出里的值有可能不在枚举里（旧版本产出），并进来免得被吃掉 */}
                  {Array.from(new Set([...SHOT_SIZES, draft.shotSize].filter(Boolean))).map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-fg">运镜方式</span>
                <select
                  value={draft.cameraMove}
                  onChange={(e) => patch({ cameraMove: e.target.value })}
                  className="h-8 cursor-pointer rounded-md border border-border-strong bg-surface px-2 text-sm text-fg"
                >
                  <option value="">未指定</option>
                  {Array.from(new Set([...CAMERA_MOVES, draft.cameraMove].filter(Boolean))).map(
                    (v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ),
                  )}
                </select>
              </label>

              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-fg">时长（秒）</span>
                <input
                  type="number"
                  min={1}
                  max={15}
                  value={draft.durationSec}
                  onChange={(e) => patch({ durationSec: e.target.value })}
                  className="h-8 rounded-md border border-border-strong bg-surface px-2 text-sm text-fg"
                />
                <span className="text-xs text-fg-subtle">
                  后端 StoryboardShot 不含时长——真实时长由 TTS 的音频长度确定性算出。这里只是本地草稿。
                </span>
              </label>

              {/* 受控 textarea：value 必须绑在属性上。写成标签内容
                  （&lt;textarea&gt;{x}&lt;/textarea&gt;）在 React 里等于 defaultValue，
                  用户一输入就和 state 脱钩。 */}
              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-fg">画面描述</span>
                <textarea
                  rows={4}
                  value={draft.content}
                  onChange={(e) => patch({ content: e.target.value })}
                  className="resize-none rounded-md border border-border-strong bg-surface px-2 py-1.5 text-sm leading-5 text-fg"
                />
                <span className="text-xs text-fg-subtle">
                  只写画面内容。风格词由一致性引擎统一注入，前端传不了也不该传。
                </span>
              </label>

              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-fg">出场角色</span>
                <div className="flex flex-wrap gap-1.5">
                  {draft.characterRefs.map((ref) => (
                    <span
                      key={ref}
                      className="inline-flex items-center gap-1 rounded-full bg-surface-2 py-1 pr-1.5 pl-2.5 text-xs text-fg-muted"
                    >
                      {characterName.get(ref) ?? ref}
                      <button
                        type="button"
                        aria-label={`移除 ${characterName.get(ref) ?? ref}`}
                        onClick={() =>
                          patch({
                            characterRefs: draft.characterRefs.filter((r) => r !== ref),
                          })
                        }
                        className="cursor-pointer rounded-full p-0.5 hover:bg-surface-3"
                      >
                        <X aria-hidden className="size-3" />
                      </button>
                    </span>
                  ))}
                  <AddCharacter
                    candidates={(characters?.characters ?? [])
                      .map((c: any) => c.ref as string)
                      .filter((r: string) => !draft.characterRefs.includes(r))}
                    nameOf={(r) => characterName.get(r) ?? r}
                    onPick={(r) => patch({ characterRefs: [...draft.characterRefs, r] })}
                    onEmpty={() => say("角色档案还没跑，或者这一集的角色都已经在镜头里了。")}
                  />
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-fg">情绪 / 氛围</span>
                <div className="flex flex-wrap gap-1.5">
                  {MOODS.map((m) => {
                    const on = draft.moods.includes(m);
                    return (
                      <button
                        key={m}
                        type="button"
                        aria-pressed={on}
                        onClick={() =>
                          patch({
                            moods: on ? draft.moods.filter((x) => x !== m) : [...draft.moods, m],
                          })
                        }
                        className={cn(
                          "cursor-pointer rounded-full px-2.5 py-1 text-xs transition-colors duration-150",
                          on
                            ? "bg-primary-soft font-medium text-primary"
                            : "bg-surface-2 text-fg-muted hover:bg-surface-3",
                        )}
                      >
                        {m}
                      </button>
                    );
                  })}
                </div>
                <span className="text-xs text-fg-subtle">
                  后端分镜 schema 里没有情绪字段，本地多选只是原型演示。
                </span>
              </div>

              {/* REQ-041 待产品确认是否需要首尾帧两张：如果视频生成走首尾帧插值，
                  这里要拆成「首帧参考图 / 尾帧参考图」两个槽。确认之前只做单张，
                  免得把一个还没定的交互先冻进代码。 */}
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-fg">参考图</span>
                <div className="flex gap-2">
                  <div className="flex size-13 items-center justify-center rounded-md bg-surface-2 text-xs text-fg-subtle">
                    无
                  </div>
                  <button
                    type="button"
                    aria-label="添加参考图"
                    onClick={() =>
                      say(
                        "参考图上传未接入：后端 advance / revise 只收文本，镜头级参考图既没有字段也没有上传通道。",
                      )
                    }
                    className="flex size-13 cursor-pointer items-center justify-center rounded-md border border-dashed border-border-strong text-fg-subtle hover:bg-surface-2 hover:text-fg"
                  >
                    <Plus aria-hidden className="size-4" />
                  </button>
                </div>
              </div>

              <div className="rounded-md border border-border bg-surface-2 px-2.5 py-2 text-xs text-fg-muted">
                产出状态：
                {hasOutput
                  ? "已有一版分镜出图。视频产出要等 M2，本轮用出图代替判断三个按钮的可用性。"
                  : "还没有产出。"}
              </div>

              {modelPicker && (
                <div className="flex flex-col gap-1.5 rounded-md border border-border bg-surface p-2.5">
                  <span className="text-xs font-medium text-fg">选择视频模型</span>
                  {VIDEO_MODELS.map((m) => (
                    <label
                      key={m}
                      className="flex cursor-pointer items-center gap-2 text-xs text-fg-muted"
                    >
                      <input
                        type="radio"
                        name="video-model"
                        checked={model === m}
                        onChange={() => setModel(m)}
                        className="accent-primary"
                      />
                      {m}
                    </label>
                  ))}
                  <span className="text-xs text-fg-subtle">
                    示例列表：后端还没有视频模型目录，接上之后要从 model_pricing 取。
                  </span>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={() => {
                        setModelPicker(false);
                        say(`已选「${model}」。${NOT_WIRED_VIDEO}换模型本身不单独扣费。`);
                      }}
                    >
                      用该模型生成
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setModelPicker(false)}>
                      取消
                    </Button>
                  </div>
                </div>
              )}

              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="primary"
                  className="flex-1"
                  disabled={hasOutput}
                  title={hasOutput ? "已有产出，请用「重新生成」覆盖" : "首次提交"}
                  onClick={() => say(NOT_WIRED_VIDEO)}
                >
                  生成视频
                </Button>
                <Button
                  size="sm"
                  disabled={!hasOutput}
                  title={
                    hasOutput ? "覆盖已有产出，会再扣一次 Credits" : "还没有产出，先用「生成视频」"
                  }
                  onClick={() => setConfirmRegen(true)}
                >
                  重新生成
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setModelPicker((v) => !v)}>
                  换模型
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmRegen}
        title={`重新生成镜头 ${activeShot?.index ?? ""}`}
        description="重新生成会覆盖已有产出，并再扣一次 Credits。确定要重来吗？"
        confirmLabel="确认重新生成"
        onCancel={() => setConfirmRegen(false)}
        onConfirm={() => {
          setConfirmRegen(false);
          say(NOT_WIRED_VIDEO);
        }}
      />
    </div>
  );
}

/**
 * 镜头缩略图。
 *
 * 没有直接用 `components/project/render-slot.tsx` 的 `RenderThumb`：那个把图
 * 包在 `<a>` 里，而这里整张卡片本身就是一个 `<button>`——`<button>` 里嵌
 * `<a>` 是非法 HTML，交互也会打架（点图变成开新标签页，而不是选中镜头）。
 * 取图方式与它一致：预签名 URL 有有效期，只能渲染时现签，不能缓存进列表。
 */
function ShotThumb({ assetId, alt }: { assetId: string; alt: string }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    assetsApi
      .downloadUrl(assetId)
      .then((r) => alive && setUrl(r.url))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [assetId]);

  if (!url) return <div className="size-full animate-pulse bg-surface-3" />;
  // eslint-disable-next-line @next/next/no-img-element -- 预签名 URL 是运行时才知道的外部地址，用不了 next/image 的构建期优化
  return <img src={url} alt={alt} className="size-full object-cover" />;
}

/** 「+ 添加」角色。展开成一列可选 ref，选完即收——不做弹窗，
 *  这一栏本来就窄，弹窗会盖住正在编辑的字段。 */
function AddCharacter({
  candidates,
  nameOf,
  onPick,
  onEmpty,
}: {
  candidates: string[];
  nameOf: (ref: string) => string;
  onPick: (ref: string) => void;
  onEmpty: () => void;
}) {
  const [open, setOpen] = useState(false);

  if (open && candidates.length > 0) {
    return (
      <div className="flex w-full flex-wrap gap-1.5 rounded-md border border-border bg-surface p-2">
        {candidates.map((ref) => (
          <button
            key={ref}
            type="button"
            onClick={() => {
              onPick(ref);
              setOpen(false);
            }}
            className="cursor-pointer rounded-full bg-surface-2 px-2.5 py-1 text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
          >
            {nameOf(ref)}
          </button>
        ))}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => (candidates.length === 0 ? onEmpty() : setOpen(true))}
      className="cursor-pointer rounded-full border border-dashed border-border-strong px-2.5 py-1 text-xs text-fg-subtle hover:bg-surface-2 hover:text-fg"
    >
      + 添加
    </button>
  );
}
