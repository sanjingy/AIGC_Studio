"use client";

import { useCallback, useReducer, useRef } from "react";

/**
 * 一个朴素的快照式撤销栈。
 *
 * 粒度是**一次操作 = 一帧快照**，不做操作级 diff：需求只要求撑住节点增删和
 * 位置变化，diff 化会把复杂度翻几倍却换不来可感知的差别。调用方在改动
 * **之前**调 `commit()` 把当前状态压栈，所以拖拽只在 dragStart 压一次，
 * 不会被拖拽过程中的几十次位置变更刷爆。
 *
 * 栈放在 ref 里、用 forceUpdate 驱动按钮可用态：只在事件处理里改，
 * StrictMode 的双调用不会重复压栈。
 */
const MAX_DEPTH = 50;

export function useGraphHistory<S>(read: () => S, apply: (snapshot: S) => void) {
  const stacks = useRef<{ past: S[]; future: S[] }>({ past: [], future: [] });
  const [, forceUpdate] = useReducer((n: number) => n + 1, 0);

  const commit = useCallback(() => {
    const { past } = stacks.current;
    past.push(read());
    if (past.length > MAX_DEPTH) past.shift();
    stacks.current.future = [];
    forceUpdate();
  }, [read]);

  const undo = useCallback(() => {
    const prev = stacks.current.past.pop();
    if (!prev) return;
    stacks.current.future.unshift(read());
    apply(prev);
    forceUpdate();
  }, [read, apply]);

  const redo = useCallback(() => {
    const next = stacks.current.future.shift();
    if (!next) return;
    stacks.current.past.push(read());
    apply(next);
    forceUpdate();
  }, [read, apply]);

  const reset = useCallback(() => {
    stacks.current = { past: [], future: [] };
    forceUpdate();
  }, []);

  return {
    commit,
    undo,
    redo,
    reset,
    canUndo: stacks.current.past.length > 0,
    canRedo: stacks.current.future.length > 0,
  };
}
