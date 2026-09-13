"use client";

import { useCallback, useEffect, useState } from "react";

import {
  ApiRequestError,
  projects,
  type LockVariables,
  type LockVariablesPatch,
} from "@/lib/api";

/**
 * 项目级锁定变量（门①，ADR-037）的读与写。
 *
 * 全站只有这一份实现——门① 的确认面板和项目设置页读的是同一份数据、
 * 走的是同一条写路径。两处各拼一次接口，必然出现"在门上改了、在设置页
 * 看到的还是旧值"。
 *
 * **门① 之外也能用**：项目还没走到门① 时后端返回空值 + 完整画风目录，
 * 不是 404。所以设置页可以一直展示"这个项目锁了什么"，而
 * `legacy_unconfirmed` 那句「历史项目，未经确认」也才有地方显示——
 * 迁移补出来的项目已经越过门① 的位置，那道门再也不会为它们打开。
 */
export function useLockVariables(projectId: string) {
  const [data, setData] = useState<LockVariables | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const next = await projects.lockVariables(projectId);
    setData(next);
    return next;
  }, [projectId]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    projects
      .lockVariables(projectId)
      .then((next) => {
        if (!alive) return;
        setData(next);
        setError(null);
      })
      .catch((cause) => {
        if (!alive) return;
        setError(
          cause instanceof ApiRequestError ? cause.error.user_message : "读取锁定变量失败",
        );
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [projectId]);

  /**
   * 写回锁定变量。**只传这次真的改了的那几项**——调用方负责算差集，
   * 把整份读出来再传回去，两个标签页同开就会互相覆盖。
   *
   * 返回 `true` 表示写成功（响应里那份新值已经并进本地状态）。失败时
   * 本地值保持不动，报错留在 `saveError` 里给调用方贴到界面上。
   *
   * 报错原文用 `message` 而不是 `user_message`：这条链路上真正要紧的两句
   * ——「未知画风 xxx」和「这个项目的画风档案已经建立，改画风需要重出
   * 全部已生成的画面」——都写在 `message` 里，`user_message` 只有目录里
   * 那句通用的「请求参数有误」「操作冲突，请刷新后重试」。
   */
  const save = useCallback(
    async (patch: LockVariablesPatch): Promise<boolean> => {
      if (Object.keys(patch).length === 0) return true;
      setSaving(true);
      setSaveError(null);
      try {
        setData(await projects.setLockVariables(projectId, patch));
        return true;
      } catch (cause) {
        setSaveError(
          cause instanceof ApiRequestError
            ? cause.error.message || cause.error.user_message
            : "保存失败，请稍后重试",
        );
        return false;
      } finally {
        setSaving(false);
      }
    },
    [projectId],
  );

  return {
    data,
    loading,
    error,
    saving,
    saveError,
    save,
    reload,
    clearSaveError: () => setSaveError(null),
  };
}

export type LockVariablesState = ReturnType<typeof useLockVariables>;
