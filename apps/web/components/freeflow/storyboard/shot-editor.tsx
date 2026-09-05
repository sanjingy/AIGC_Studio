"use client";

import * as React from "react";
import { Loader2, Save, Undo2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { PatchOp } from "@/lib/api";
import type { ContentEdit } from "@/lib/freeflow/use-content-edit";
import { cn } from "@/lib/utils";

import type { ShotCardData } from "./shot-card";
import {
  OutdatedNotice,
  ShotDetail,
  type ShotDraft,
  type ShotFieldKey,
  type ShotOptions,
} from "./shot-detail";

/**
 * 一镜的编辑状态机（ADR-029 / FR-WEB-004）。
 *
 * 三条约束决定了这里为什么长这样：
 *
 * 1. **一次保存 = 一个批次。** 改了几个字段就发几条 patch，一个请求。
 *    拆成一字段一请求的话，撤销要点好几次才退得回一次操作。
 * 2. **未保存的改动必须显眼，且切走要拦一下。** 上一轮刚删掉的正是
 *    "改了只存在本地"的假编辑，不能换个形式又长回来。
 * 3. **保存后用响应里的整块产出更新状态**（在 `useContentEdit` 里做），
 *    不 refetch，否则界面会闪一下旧值。
 */

/** 后端 `StoryboardShot` 的一镜。字段名与 schema 一一对应。 */
export type Shot = {
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

const FIELDS: ShotFieldKey[] = [
  "scene_ref",
  "character_refs",
  "shot_size",
  "angle",
  "camera_move",
  "content",
  "speaker_ref",
  "dialogue",
  "sfx",
];

function draftOf(shot: Shot): ShotDraft {
  return {
    scene_ref: shot.scene_ref ?? "",
    character_refs: [...(shot.character_refs ?? [])],
    shot_size: shot.shot_size ?? "",
    angle: shot.angle ?? "",
    camera_move: shot.camera_move ?? "",
    content: shot.content ?? "",
    speaker_ref: shot.speaker_ref ?? "",
    dialogue: shot.dialogue ?? "",
    sfx: shot.sfx ?? "",
  };
}

function same(a: ShotDraft[ShotFieldKey], b: ShotDraft[ShotFieldKey]): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    // 顺序有意义（`request_shot_image` 照抄 character_refs 的顺序），
    // 所以是逐位比，不是集合比。
    return a.length === b.length && a.every((v, i) => v === b[i]);
  }
  return a === b;
}

/**
 * 未保存时拦住整页关闭 / 刷新。
 *
 * 只能拦住浏览器级别的离开（关标签、刷新、后退）。App Router 没有稳定的
 * 路由拦截钩子，站内点侧栏跳走拦不住——所以除了这个之外，界面上还必须
 * 有一条一眼能看见的未保存提示（下面的 `footer`），以及切换镜头时的确认。
 */
function useLeaveGuard(dirty: boolean) {
  React.useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // 现代浏览器只看"有没有阻止默认行为"，文案是浏览器自己的，
      // 但 returnValue 还得赋值，否则 Safari 不弹。
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
}

export function ShotEditor({
  shot,
  shotAt,
  card,
  options,
  edit,
  actions,
  imageCreatedAt,
  onDirtyChange,
}: {
  shot: Shot;
  /**
   * 这一镜在 `storyboard.shots` 里的**数组下标**，不是 `shot.index`。
   *
   * JSON Pointer 走的是数组位置。正常产出里两者恰好差 1，但 Agent 不保证
   * 镜号连续，照镜号算会改到别的镜上去。
   */
  shotAt: number;
  card: ShotCardData;
  options: ShotOptions;
  edit: ContentEdit;
  actions: React.ReactNode;
  /** 这一镜当前那张图是什么时候出的。没有图就是 null。 */
  imageCreatedAt: string | null;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const base = React.useMemo(() => draftOf(shot), [shot]);
  const [draft, setDraft] = React.useState<ShotDraft>(base);

  // 换了一镜、或者保存成功后 `shot` 变成了新产出，草稿都要跟着重置。
  // 依赖写 `base` 而不是 `shot`：`shots` 数组每次 memo 都是新引用，
  // 盯着它会把用户正在输入的内容冲掉。
  React.useEffect(() => setDraft(base), [base]);

  const dirty = React.useMemo(() => {
    const out = new Set<ShotFieldKey>();
    for (const f of FIELDS) if (!same(draft[f], base[f])) out.add(f);
    return out;
  }, [draft, base]);

  const hasChanges = dirty.size > 0;
  useLeaveGuard(hasChanges);
  React.useEffect(() => onDirtyChange(hasChanges), [hasChanges, onDirtyChange]);

  const [reason, setReason] = React.useState("");

  const change = React.useCallback(
    <K extends ShotFieldKey>(field: K, value: ShotDraft[K]) =>
      setDraft((prev) => ({ ...prev, [field]: value })),
    [],
  );

  const discard = () => {
    setDraft(base);
    setReason("");
    edit.clearError();
  };

  const save = async () => {
    const patches: PatchOp[] = [...dirty].map((field) => ({
      path: `/shots/${shotAt}/${field}`,
      value: draft[field],
    }));
    if (patches.length === 0) return;
    const result = await edit.save(patches, reason);
    // 失败时草稿原样留着——把用户刚打的字清掉，他就得凭记忆重打一遍。
    if (result) setReason("");
  };

  const editedAt = edit.editedAt(`/shots/${shotAt}`);
  const outdated =
    imageCreatedAt !== null && editedAt !== null && Date.parse(imageCreatedAt) < editedAt;

  return (
    <ShotDetail
      shot={{ ...card, nodeIndex: shot.node_index }}
      draft={draft}
      dirty={dirty}
      options={options}
      disabled={edit.saving}
      onChange={change}
      actions={actions}
      outdatedNotice={outdated && editedAt !== null ? <OutdatedNotice editedAt={editedAt} /> : null}
      footer={
        <div
          className={cn(
            "flex flex-wrap items-center gap-2 border-t px-5 py-3",
            hasChanges ? "border-rf-warning/40 bg-rf-warning/10" : "border-border bg-surface-2",
          )}
        >
          <span
            className={cn(
              "text-xs",
              hasChanges ? "font-medium text-fg" : "text-fg-subtle",
            )}
          >
            {hasChanges
              ? `${dirty.size} 个字段改过还没保存，离开这一镜会提示你`
              : "改动直接写回后端，刷新还在；不花 Credits，也不会重跑 Agent"}
          </span>

          <input
            type="text"
            value={reason}
            disabled={edit.saving}
            placeholder="改动说明（可选，会记进历史）"
            aria-label="改动说明"
            onChange={(e) => setReason(e.target.value)}
            className="ml-auto h-7 w-full max-w-[16rem] rounded-md border border-border-strong bg-bg px-2 text-xs text-fg focus:border-primary focus:outline-none disabled:opacity-60"
          />
          <Button size="sm" disabled={!hasChanges || edit.saving} onClick={discard}>
            <Undo2 aria-hidden className="size-3.5" />
            放弃改动
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!hasChanges || edit.saving}
            onClick={() => void save()}
          >
            {edit.saving ? (
              <Loader2 aria-hidden className="size-3.5 animate-spin" />
            ) : (
              <Save aria-hidden className="size-3.5" />
            )}
            保存这一镜
          </Button>

          {edit.error && (
            <p role="alert" className="w-full rounded-md bg-danger-soft px-2.5 py-1.5 text-xs leading-5 text-danger">
              {edit.error}
            </p>
          )}
        </div>
      }
    />
  );
}
