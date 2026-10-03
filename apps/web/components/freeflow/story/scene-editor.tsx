"use client";

import * as React from "react";
import { Eye, Loader2, Pencil, Save, Undo2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { PatchOp } from "@/lib/api";
import { rebaseDraft } from "@/lib/freeflow/draft-rebase";
import type { ContentEdit } from "@/lib/freeflow/use-content-edit";
import {
  FIELD_LABEL,
  beatComplete,
  beatsOf,
  editableSceneFields,
  planSceneDraft,
  sceneDraftOf,
  type BeatDraft,
  type BeatField,
  type SceneDraft,
  type SceneField,
  type SceneLike,
  type ScenePos,
} from "@/lib/freeflow/screenplay-scope";
import { useLeaveGuard } from "@/lib/freeflow/use-leave-guard";
import { cn } from "@/lib/utils";

import { BeatReading, BeatRows } from "./beat-rows";

/**
 * 一场的阅读 / 编辑（ADR-029 字段级编辑）。
 *
 * 三条约束决定了它长这样，与分镜的 `ShotEditor` 同源：
 *
 * 1. **一次保存 = 一个批次。** 改了几个字段就发几条 patch，一个请求，
 *    撤销时一次退回去。
 * 2. **未保存的改动必须显眼，切走要拦一下。** 拦截在外层（切场/切集/回总览
 *    的确认弹窗），这里负责 `beforeunload` 与那条贴底的提示。
 * 3. **保存后用响应里的整块产出更新状态**（在 `useContentEdit` 里做），
 *    不 refetch，否则界面会闪一下旧值。
 *
 * 只读的三样：场号 `id`、所属集号、`node_coverage`。它们是引用键与 QA 的核对
 * 依据，改了会静默打断覆盖关系。
 */

const SCENE_MAXLEN: Record<Exclude<SceneField, "character_refs">, number> = {
  location: 60,
  time_mood: 60,
  hook: 200,
};

export function SceneEditor({
  pos,
  scene,
  edit,
  characterSuggestions,
  onDirtyChange,
  readOnlyChips,
}: {
  /** 这一场在产出里的**数组位置**，写路径用它 */
  pos: ScenePos;
  scene: SceneLike;
  edit: ContentEdit;
  /** 说话人 / 出场角色的候选：来自情节目录的角色表与角色档案，都是真实数据 */
  characterSuggestions: string[];
  onDirtyChange: (dirty: boolean) => void;
  /** 只读信息（场号、覆盖节点）由外层拼，它知道集与覆盖 */
  readOnlyChips: React.ReactNode;
}) {
  const base = React.useMemo(() => sceneDraftOf(scene), [scene]);
  const [draft, setDraft] = React.useState<SceneDraft>(base);
  const [mode, setMode] = React.useState<"read" | "edit">("read");
  const [reason, setReason] = React.useState("");
  const [addRef, setAddRef] = React.useState("");

  // 草稿按**内容**跟着库里的值走，理由同 `FieldsEditor`：父级重渲染会换引用
  // 但不换值，按引用重置会冲掉正在输入的内容。换场由外层按 key 重建组件，
  // 不走这里；这里只处理"同一场的产出变了"（保存、撤销、返工），规则见
  // `rebaseDraft`。节拍的每个格子都是一个字段：条数没变时逐条逐格跟，
  // 条数变了就把整组节拍当一个字段。
  const baseKey = JSON.stringify(base);
  const justSaved = React.useRef(false);
  const prevBase = React.useRef(base);
  React.useEffect(() => {
    const takeAll = justSaved.current;
    const from = prevBase.current;
    setDraft((current) => {
      const next = rebaseDraft(from, base, current, takeAll);
      const n = base.beats.length;
      if (!takeAll && from.beats.length === n && current.beats.length === n) {
        next.beats = base.beats.map((beat, i) => rebaseDraft(from.beats[i]!, beat, current.beats[i]!, false));
      }
      return next;
    });
    if (takeAll) setAddRef("");
    prevBase.current = base;
    justSaved.current = false;
  }, [baseKey]);

  const editable = React.useMemo(() => editableSceneFields(scene), [scene]);
  const incompleteBeats = React.useMemo(() => {
    const out = new Set<number>();
    beatsOf(scene).forEach((beat, at) => {
      if (!beatComplete(beat)) out.add(at);
    });
    return out;
  }, [scene]);

  const plan = React.useMemo(() => planSceneDraft(pos, scene, draft), [pos, scene, draft]);
  const hasChanges = plan.patches.length > 0 || plan.blocked.length > 0;

  useLeaveGuard(hasChanges);
  React.useEffect(() => onDirtyChange(hasChanges), [hasChanges, onDirtyChange]);
  // 卸载时撤掉自己报上去的 dirty，否则父级会一直以为还有草稿
  React.useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  // 有草稿时必须停在编辑模式，否则改动会被阅读视图盖住，看着像没改
  React.useEffect(() => {
    if (hasChanges) setMode("edit");
  }, [hasChanges]);

  const dirtySceneFields = React.useMemo(() => {
    const out = new Set<SceneField>(plan.blocked);
    for (const patch of plan.patches) {
      const m = /\/scenes\/\d+\/(location|time_mood|character_refs|hook)$/.exec(patch.path);
      if (m) out.add(m[1] as SceneField);
    }
    return out;
  }, [plan]);

  const dirtyBeatKeys = React.useMemo(() => {
    const out = new Set<string>();
    for (const patch of plan.patches) {
      const field = /\/beats\/(\d+)\/(kind|character_ref|emotion|text)$/.exec(patch.path);
      if (field) {
        out.add(`${field[1]}.${field[2]}`);
        continue;
      }
      // 整条替换：把这条的 4 个字段都标成改过的，它们确实要一起写回去
      const whole = /\/beats\/(\d+)$/.exec(patch.path);
      if (whole) {
        for (const f of ["kind", "character_ref", "emotion", "text"]) out.add(`${whole[1]}.${f}`);
      }
    }
    return out;
  }, [plan]);

  const setField = <K extends keyof SceneDraft>(field: K, value: SceneDraft[K]) =>
    setDraft((prev) => ({ ...prev, [field]: value }));

  const setBeat = React.useCallback((at: number, field: BeatField, value: string) => {
    setDraft((prev) => {
      const beats: BeatDraft[] = prev.beats.map((beat, i) =>
        i === at ? { ...beat, [field]: value } : beat,
      );
      return { ...prev, beats };
    });
  }, []);

  const discard = () => {
    setDraft(base);
    setReason("");
    setAddRef("");
    edit.clearError();
  };

  const save = async () => {
    if (plan.patches.length === 0) return;
    const patches: PatchOp[] = plan.patches.map((p) => ({ path: p.path, value: p.value }));
    justSaved.current = true;
    const result = await edit.save(patches, reason);
    // 失败时草稿原样留着——清掉的话用户得凭记忆重打一遍
    if (result) setReason("");
    else justSaved.current = false;
  };

  const refsEditable = editable.has("character_refs");

  return (
    <section className="flex min-h-0 flex-col gap-3" aria-label="场次内容">
      <div className="flex flex-wrap items-center gap-2">
        {readOnlyChips}
        <div role="group" aria-label="阅读或编辑" className="ml-auto flex items-center gap-1">
          <ModeTab
            icon={Eye}
            label="阅读"
            active={mode === "read"}
            disabled={hasChanges}
            title={hasChanges ? "有未保存的改动，先保存或放弃再切回阅读" : "只读排版"}
            onClick={() => setMode("read")}
          />
          <ModeTab
            icon={Pencil}
            label="编辑"
            active={mode === "edit"}
            onClick={() => setMode("edit")}
          />
        </div>
      </div>

      {mode === "read" ? (
        <div className="ff-paper overflow-hidden px-4 py-3.5">
          <p className="text-xs text-fg-subtle">
            【{base.location || "未填地点"} - {base.time_mood || "未填时间"}】
          </p>
          {base.character_refs.length > 0 && (
            <p className="mt-1 text-xs text-fg-subtle">出场：{base.character_refs.join("、")}</p>
          )}
          <div className="mt-2.5">
            <BeatReading beats={base.beats} hook={base.hook} />
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <TextField
              field="location"
              value={draft.location}
              base={base.location}
              editable={editable.has("location")}
              dirty={dirtySceneFields.has("location")}
              disabled={edit.saving}
              onChange={(v) => setField("location", v)}
            />
            <TextField
              field="time_mood"
              value={draft.time_mood}
              base={base.time_mood}
              editable={editable.has("time_mood")}
              dirty={dirtySceneFields.has("time_mood")}
              disabled={edit.saving}
              onChange={(v) => setField("time_mood", v)}
            />
          </div>

          <div>
            <p className="text-xs font-medium text-fg">
              {FIELD_LABEL.character_refs}
              {dirtySceneFields.has("character_refs") && (
                <span className="ml-1.5 text-rf-warning">已改</span>
              )}
            </p>
            {refsEditable ? (
              <>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {draft.character_refs.length === 0 && (
                    <span className="text-xs text-fg-subtle">还没有出场角色</span>
                  )}
                  {draft.character_refs.map((ref, i) => (
                    <span
                      key={`${ref}-${i}`}
                      className="inline-flex items-center gap-1 rounded-[2px] bg-surface-2 py-0.5 pr-1 pl-2 text-xs text-fg-muted"
                    >
                      {ref || "（空）"}
                      <button
                        type="button"
                        disabled={edit.saving}
                        aria-label={`移除 ${ref}`}
                        onClick={() =>
                          setField(
                            "character_refs",
                            draft.character_refs.filter((_, j) => j !== i),
                          )
                        }
                        className="grid size-4 place-items-center rounded-sm text-fg-subtle hover:text-danger focus-visible:outline-1 focus-visible:outline-primary disabled:opacity-60"
                      >
                        <X aria-hidden className="size-3" />
                      </button>
                    </span>
                  ))}
                </div>
                <div className="mt-1.5 flex items-center gap-1.5">
                  <input
                    type="text"
                    value={addRef}
                    list="story-character-suggestions"
                    disabled={edit.saving || draft.character_refs.length >= 12}
                    maxLength={32}
                    aria-label="添加出场角色"
                    placeholder="添加角色后回车"
                    onChange={(e) => setAddRef(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key !== "Enter") return;
                      e.preventDefault();
                      const value = addRef.trim();
                      if (!value || draft.character_refs.includes(value)) return;
                      setField("character_refs", [...draft.character_refs, value]);
                      setAddRef("");
                    }}
                    className="h-7 w-40 rounded-md border border-border-strong bg-bg px-2 text-xs text-fg placeholder:text-fg-subtle focus:border-primary focus:outline-none disabled:opacity-60"
                  />
                  <span className="tnum text-[11px] text-fg-subtle">
                    {draft.character_refs.length} / 12
                  </span>
                </div>
                <datalist id="story-character-suggestions">
                  {characterSuggestions.map((name) => (
                    <option key={name} value={name} />
                  ))}
                </datalist>
              </>
            ) : (
              <MissingFieldNote />
            )}
          </div>

          <TextField
            field="hook"
            value={draft.hook}
            base={base.hook}
            editable={editable.has("hook")}
            dirty={dirtySceneFields.has("hook")}
            disabled={edit.saving}
            multiline
            onChange={(v) => setField("hook", v)}
          />

          <div>
            <h4 className="mb-1.5 flex items-baseline gap-2 text-sm font-semibold text-fg">
              节拍
              <span className="tnum text-xs font-normal text-fg-subtle">
                {draft.beats.length} 条 · 条数不可改，加减节拍要返工整段剧本
              </span>
            </h4>
            <BeatRows
              beats={draft.beats}
              dirtyKeys={dirtyBeatKeys}
              incomplete={incompleteBeats}
              disabled={edit.saving}
              characterSuggestions={characterSuggestions}
              onChange={setBeat}
            />
          </div>
        </div>
      )}

      <div
        className={cn(
          // 贴底：长字段滚下去以后保存仍然够得着（390 屏尤其如此）；
          // 必须不透明，否则滚动的内容会从下面透上来
          "sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center gap-2 border-t px-4 py-3 lg:-mx-6 lg:px-6",
          hasChanges ? "border-rf-warning/50 bg-rf-warning-soft" : "border-border bg-bg",
        )}
      >
        <span className={cn("text-xs", hasChanges ? "font-medium text-fg" : "text-fg-subtle")}>
          {hasChanges
            ? `${plan.patches.length} 处改动还没保存，离开这一场会提示你`
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
          disabled={plan.patches.length === 0 || edit.saving}
          title={
            plan.patches.length === 0 && plan.blocked.length > 0
              ? "改动落在这份旧产出没有的字段上，提交不了"
              : "把改过的字段一批写回后端"
          }
          onClick={() => void save()}
        >
          {edit.saving ? (
            <Loader2 aria-hidden className="size-3.5 animate-spin" />
          ) : (
            <Save aria-hidden className="size-3.5" />
          )}
          保存这一场
        </Button>

        {plan.blocked.length > 0 && (
          <p className="w-full text-xs text-rf-warning">
            这份旧产出里没有 {plan.blocked.map((f) => FIELD_LABEL[f] ?? f).join("、")}{" "}
            字段，改了也提交不了——只能返工整段剧本补上。
          </p>
        )}
        {edit.error && (
          <p
            role="alert"
            className="w-full rounded-md bg-danger-soft px-2.5 py-1.5 text-xs leading-5 text-danger"
          >
            {edit.error}
          </p>
        )}
      </div>
    </section>
  );
}

function ModeTab({
  icon: Icon,
  label,
  active,
  disabled,
  title,
  onClick,
}: {
  icon: typeof Eye;
  label: string;
  active: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      disabled={disabled}
      title={title}
      onClick={onClick}
      className={cn(
        "inline-flex h-7 items-center gap-1 rounded-md border px-2 text-xs transition-colors duration-150",
        "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary",
        "disabled:cursor-not-allowed disabled:opacity-40",
        active
          ? "border-primary/60 bg-primary-soft text-primary"
          : "border-border text-fg-muted hover:border-border-strong hover:text-fg",
      )}
    >
      <Icon aria-hidden className="size-3.5" />
      {label}
    </button>
  );
}

function MissingFieldNote() {
  return (
    <p className="mt-1 text-xs text-fg-subtle">
      这份旧产出没有这个字段，不能单独补——只能返工整段剧本。
    </p>
  );
}

function TextField({
  field,
  value,
  base,
  editable,
  dirty,
  disabled,
  multiline,
  onChange,
}: {
  field: Exclude<SceneField, "character_refs">;
  value: string;
  base: string;
  editable: boolean;
  dirty: boolean;
  disabled: boolean;
  multiline?: boolean;
  onChange: (value: string) => void;
}) {
  const id = React.useId();
  const className = cn(
    "w-full rounded-md border bg-bg px-2 py-1.5 text-[13px] leading-6 text-fg placeholder:text-fg-subtle focus:border-primary focus:outline-none disabled:opacity-60",
    dirty ? "border-rf-warning" : "border-border-strong",
  );
  return (
    // label 只包字段名：包住 textarea 会把正文算进可访问名称
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-fg" htmlFor={id}>
        {FIELD_LABEL[field] ?? field}
        {dirty && <span className="ml-1.5 text-rf-warning">已改</span>}
      </label>
      {editable ? (
        multiline ? (
          <textarea
            id={id}
            rows={2}
            value={value}
            disabled={disabled}
            maxLength={SCENE_MAXLEN[field]}
            onChange={(e) => onChange(e.target.value)}
            className={cn(className, "resize-y")}
          />
        ) : (
          <input
            id={id}
            type="text"
            value={value}
            disabled={disabled}
            maxLength={SCENE_MAXLEN[field]}
            onChange={(e) => onChange(e.target.value)}
            className={className}
          />
        )
      ) : (
        <>
          <span className="rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[13px] text-fg-muted">
            {base || "（空）"}
          </span>
          <MissingFieldNote />
        </>
      )}
    </div>
  );
}
