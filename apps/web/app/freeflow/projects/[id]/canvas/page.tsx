"use client";

import { use, useRef } from "react";

import { ProjectHeader } from "@/components/freeflow/project-header";
import { WorkflowCanvas, type CanvasHandle } from "@/components/freeflow/canvas/workflow-canvas";
import { useProjectOutput } from "@/lib/freeflow/use-project-output";

/**
 * 02 工作流画布（REQ-020~023）。
 *
 * 「保存 / 运行」按钮长在 `ProjectHeader` 上、画布状态长在 `WorkflowCanvas` 里，
 * 两边隔着一层，所以用一个 ref 句柄把动作传下去——为了两个按钮把整份画布
 * 状态提到页面这层，页面就成了第二个真相源。
 */
export default function FreeflowCanvasPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { project } = useProjectOutput(id);
  const canvas = useRef<CanvasHandle | null>(null);

  return (
    <>
      <ProjectHeader
        projectId={id}
        title={project?.title ?? "…"}
        onSave={() => canvas.current?.save()}
        onRun={() => canvas.current?.run()}
      />
      <WorkflowCanvas projectId={id} handleRef={canvas} />
    </>
  );
}
