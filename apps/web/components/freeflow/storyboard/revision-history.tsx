"use client";

import * as React from "react";
import { History, Loader2, Undo2 } from "lucide-react";

import { Dialog, DialogCloseButton } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { auth, type RevisionBatch, type User } from "@/lib/api";
import type { ContentEdit } from "@/lib/freeflow/use-content-edit";
import { cn } from "@/lib/utils";

/**
 * 字段级改动的历史与撤销（ADR-029）。
 *
 * **不进主导航**，是分镜页顶栏的一个抽屉入口。理由：这份历史是按 role
 * 过滤的（`GET /revisions?role=`），它回答的是"我刚才把这一镜改成什么了"，
 * 属于正在编辑的那份产出，不是一个独立的目的地。给它一个顶级位置，
 * 用户得先离开正在改的东西才能看它改了什么。
 *
 * 撤销以**批**为单位，因为一次保存就是一批：用户改 3 个字段点一次保存，
 * 撤销也应该一次全退回去。
 */

/** 值渲染。历史里的 old/new 是任意 JSON，不能假设它是字符串。 */
function ValueText({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-fg-subtle italic">（空）</span>;
  }
  if (Array.isArray(value)) {
    return <>{value.length === 0 ? "（空列表）" : value.map((v) => String(v)).join("、")}</>;
  }
  if (typeof value === "object") return <>{JSON.stringify(value)}</>;
  return <>{String(value)}</>;
}

function actorText(batch: RevisionBatch, me: User | null): string {
  if (!batch.actor_user_id) return "系统";
  if (me && batch.actor_user_id === me.id) return "你";
  // 组织成员目录没有对应的读接口，拿不到别人的名字。显示一截 id 而不是
  // 编一个"某成员"——短 id 至少能把两个不同的人区分开。
  return `其他成员 ${batch.actor_user_id.slice(0, 8)}`;
}

function BatchCard({
  batch,
  me,
  describePath,
  busy,
  onUndo,
}: {
  batch: RevisionBatch;
  me: User | null;
  describePath: (path: string) => string;
  busy: boolean;
  onUndo: () => void;
}) {
  const undone = batch.undone_by_batch_id !== null;
  const isUndo = batch.undoes_batch_id !== null;

  return (
    <li
      className={cn(
        "rounded-[2px] border p-3",
        undone ? "border-border bg-surface-2 opacity-70" : "border-border bg-surface",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-fg">{actorText(batch, me)}</span>
        <span className="tnum text-[11px] text-fg-subtle">
          {new Date(batch.created_at).toLocaleString("zh-CN")}
        </span>
        {isUndo && (
          <span className="rounded-[2px] border border-border bg-surface-2 px-2 py-0.5 text-[10px] text-fg-muted">
            这是一次撤销
          </span>
        )}
        {undone && (
          <span className="rounded-[2px] border border-border bg-surface-2 px-2 py-0.5 text-[10px] text-fg-muted">
            已被撤销
          </span>
        )}
        <Button
          size="sm"
          className="ml-auto"
          // 已经撤过的批再撤一次后端一定 409，所以在这里就禁掉，
          // 不让用户点下去吃一个错误。
          disabled={undone || busy}
          title={undone ? "这一批已经撤销过了" : "把这一批改动整批退回去"}
          onClick={onUndo}
        >
          {busy ? (
            <Loader2 aria-hidden className="size-3.5 animate-spin" />
          ) : (
            <Undo2 aria-hidden className="size-3.5" />
          )}
          撤销
        </Button>
      </div>

      {batch.reason && (
        <p className="mt-1.5 text-xs text-fg-muted">说明：{batch.reason}</p>
      )}

      <ul className="mt-2 flex flex-col gap-1.5 border-t border-border pt-2">
        {batch.changes.map((change) => (
          <li key={change.id} className="text-xs leading-5">
            <span className="text-fg-muted">{describePath(change.field_path)}</span>
            <span className="mx-1 text-fg-subtle">：</span>
            <span className="text-fg-subtle line-through">
              <ValueText value={change.old_value} />
            </span>
            <span className="mx-1 text-fg-subtle">→</span>
            <span className="text-fg">
              <ValueText value={change.new_value} />
            </span>
          </li>
        ))}
      </ul>
    </li>
  );
}

/**
 * 抽屉本体。`edit` 就是页面正在用的那份 `useContentEdit`——历史和编辑
 * 共用一份状态，保存完历史立刻是新的，撤销完编辑区立刻是旧值。
 * 各拿一份 hook 的话，两边要各自记得刷新对方。
 */
export function RevisionHistoryDrawer({
  open,
  onOpenChange,
  edit,
  title,
  describePath,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  edit: ContentEdit;
  title: string;
  describePath: (path: string) => string;
}) {
  const [me, setMe] = React.useState<User | null>(null);
  const headingId = React.useId();

  // 只在抽屉真的打开时问一次"我是谁"：整站没有当前用户的上下文，
  // 而这里只需要它来把"你"和别人分开，不值得为它在全局多发一个请求。
  React.useEffect(() => {
    if (!open || me) return;
    let alive = true;
    auth
      .me()
      .then((u) => alive && setMe(u))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [open, me]);

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      labelledBy={headingId}
      placement="right"
      dismissible={edit.busy === null}
      className="h-full w-full max-w-[34rem] border-l border-border bg-surface shadow-rf-card"
    >
      <header className="flex items-center gap-2 border-b border-border px-4 py-3">
        <History aria-hidden className="size-4 shrink-0 text-fg-muted" />
        <h2 id={headingId} className="min-w-0 truncate text-sm font-semibold text-fg">
          {title}
        </h2>
        <DialogCloseButton
          className="ml-auto"
          disabled={edit.busy !== null}
          onClick={() => onOpenChange(false)}
        />
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {edit.error && (
          <p role="alert" className="mb-3 rounded-md bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">
            {edit.error}
          </p>
        )}

        {edit.batches.length === 0 ? (
          <p className="rounded-[2px] border border-dashed border-border px-4 py-10 text-center text-xs leading-5 text-fg-subtle">
            {edit.loading ? "加载中…" : "还没有字段级改动。在镜头上改一个字段并保存，这里就会留下一条记录。"}
          </p>
        ) : (
          <ol className="flex flex-col gap-2.5">
            {edit.batches.map((batch) => (
              <BatchCard
                key={batch.batch_id}
                batch={batch}
                me={me}
                describePath={describePath}
                busy={edit.busy === "undo"}
                onUndo={() => void edit.undo(batch.batch_id)}
              />
            ))}
          </ol>
        )}

        {edit.hasMore && (
          <div className="mt-3 flex justify-center">
            <Button size="sm" disabled={edit.loading} onClick={edit.loadMore}>
              {edit.loading && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
              加载更早的改动
            </Button>
          </div>
        )}
      </div>

      <footer className="border-t border-border px-4 py-2.5 text-[11px] leading-5 text-fg-subtle">
        这里只有<span className="font-medium text-fg-muted">字段级</span>改动。
        自然语言返工重跑的是整个 Agent，它的记录在返工对话里。
      </footer>
    </Dialog>
  );
}
