"use client";

import { createContext, useContext } from "react";

/**
 * 画布里的子组件（连线上的 × 按钮）要触发的操作。
 *
 * 走 context 而不是塞进 edge.data：这些操作都要先压一帧撤销栈，
 * 撤销栈只有 `WorkflowCanvas` 拿得到，让每处建连线的地方都记得挂回调
 * 迟早会漏一处。
 */
export type CanvasActions = {
  deleteEdge: (edgeId: string) => void;
};

const NOOP: CanvasActions = { deleteEdge: () => {} };

export const CanvasActionsContext = createContext<CanvasActions>(NOOP);

export function useCanvasActions(): CanvasActions {
  return useContext(CanvasActionsContext);
}
