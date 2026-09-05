"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  editErrorText,
  projects,
  type PatchOp,
  type PatchResult,
  type RevisionBatch,
  type ReviseTarget,
} from "@/lib/api";
import type { ProjectState } from "@/lib/freeflow/use-project-state";

/**
 * 字段级编辑（ADR-029）的写路径与变更历史。
 *
 * 与自然语言返工（`RevisePanel` / `POST /revise`）是**两条不同的路径**，
 * 别把它们合并：返工重跑整个 Agent，慢、要花 Credits，而且模型可能顺手
 * 改坏别的字段；这条只改用户点到的那几个字段，确定性的，一分钱不花，
 * 而且可以整批撤销。
 *
 * 三条使用约定：
 *
 * 1. **一次保存 = 一次 `patch` 调用 = 一个批次。** 用户改了 3 个字段点一次
 *    保存，就发一个请求带 3 条 patch。拆成 3 个请求的话，用户眼里的一次
 *    操作要点三次撤销才退得回去。
 * 2. **不 refetch。** 响应里已经带了改完之后的整块产出，直接并回
 *    `useProjectState`（`applyPatchedOutput`）。改完再 GET 一次，中间那段
 *    时间界面显示的还是旧值，看起来像"保存了但没生效"。
 * 3. **不在前端校验字段。** 合法性由后端按该阶段的 Pydantic schema 判，
 *    前端再写一套必然分叉。这里只负责把后端的报错原文显示出来。
 */

/**
 * 每次拉多少批历史。后端上限是 100（`content/service.py` 的
 * `MAX_HISTORY_BATCHES`），这里取一半：抽屉里一屏放不下 50 批，
 * 剩下的按「加载更早」翻页，不为了少一次请求把首屏拖慢。
 */
const HISTORY_PAGE = 50;

function touches(batch: RevisionBatch, prefix: string): boolean {
  return batch.changes.some(
    (c) => c.field_path === prefix || c.field_path.startsWith(`${prefix}/`),
  );
}

export function useContentEdit(
  projectId: string,
  role: ReviseTarget,
  state: ProjectState,
) {
  const [batches, setBatches] = useState<RevisionBatch[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<"save" | "undo" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { applyPatchedOutput } = state;

  const load = useCallback(
    async (from?: string) => {
      setLoading(true);
      try {
        const page = await projects.revisions(projectId, {
          role,
          limit: HISTORY_PAGE,
          cursor: from,
        });
        setBatches((prev) => (from ? [...prev, ...page.items] : page.items));
        setCursor(page.next_cursor);
      } catch {
        // 历史是加分项：拉不到不该让编辑本身用不了，也不该顶掉保存的报错。
        // 真正要紧的写路径失败会走下面 `run` 里的 setError。
      } finally {
        setLoading(false);
      }
    },
    [projectId, role],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const run = useCallback(
    async (kind: "save" | "undo", fn: () => Promise<PatchResult>) => {
      setBusy(kind);
      setError(null);
      try {
        const result = await fn();
        applyPatchedOutput(result.role, result.output, result.stale_roles);
        // 历史重新从第一页拉：新批次在最前面，追加到旧列表上顺序就乱了。
        await load();
        return result;
      } catch (cause) {
        setError(editErrorText(cause));
        return null;
      } finally {
        setBusy(null);
      }
    },
    [applyPatchedOutput, load],
  );

  /** 保存一次编辑。`patches` 要一次带全，理由见文件头第 1 条。 */
  const save = useCallback(
    (patches: PatchOp[], reason?: string) =>
      run("save", () => projects.patchOutput(projectId, role, patches, reason)),
    [projectId, role, run],
  );

  /**
   * 撤销一整批。已经撤过的批（`undone_by_batch_id` 有值）不要调——
   * 后端会 409，按钮应该在那之前就是禁用的。
   */
  const undo = useCallback(
    (batchId: string) => run("undo", () => projects.undoRevision(projectId, batchId)),
    [projectId, run],
  );

  /**
   * 这个路径下最后一次改动的时间（毫秒），没改过就是 null。
   *
   * 用来回答"这张图是不是在这一镜被改之前出的"。**只看时间不看字段**：
   * 判断"哪些字段会影响画面"要照抄后端 `compose_shot` 读了哪几个字段，
   * 那份耦合一旦漂移，漂的方向是**漏标**——用户会以为图还对得上。
   * 多标一次的代价只是多看见一个提示，而且过期只是标记，不会自动重跑
   * （ADR-033 第 4 条）。
   *
   * 只覆盖已经拉回来的那几页历史。分页是按时间倒序的，所以最新的改动
   * 一定在第一页；漏掉的只可能是"很久以前改过、之后再没动过、图比它还老"
   * 这种组合。
   */
  const editedAt = useCallback(
    (pathPrefix: string): number | null => {
      let latest: number | null = null;
      for (const batch of batches) {
        if (!touches(batch, pathPrefix)) continue;
        const at = Date.parse(batch.created_at);
        if (Number.isNaN(at)) continue;
        if (latest === null || at > latest) latest = at;
      }
      return latest;
    },
    [batches],
  );

  return {
    /** 变更历史，最新在前。只含这个 role 的批次。 */
    batches,
    loading,
    /** 还有更早的历史没拉。 */
    hasMore: cursor !== null,
    loadMore: useCallback(() => {
      if (cursor) void load(cursor);
    }, [cursor, load]),
    busy,
    saving: busy === "save",
    error,
    clearError: useCallback(() => setError(null), []),
    save,
    undo,
    editedAt,
    /**
     * **已经拉回来**多少批，不是总数——总数后端没给，游标分页也算不出。
     * 历史入口上显示它时要配合 `hasMore`，否则会读成"一共只改过这么多次"。
     */
    loadedCount: useMemo(() => batches.length, [batches]),
  };
}

export type ContentEdit = ReturnType<typeof useContentEdit>;
