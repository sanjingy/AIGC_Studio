"use client";

import { use } from "react";

import { ProjectHeader } from "@/components/freeflow/project-header";
import { StoryboardEditor } from "@/components/freeflow/project/storyboard-editor";
import { useProjectOutput } from "@/lib/freeflow/use-project-output";
import { useRenders } from "@/lib/useRenders";

/**
 * 04 分镜编辑（需求文档「屏幕 04」、REQ-040/041）。
 *
 * 数据全部来自真实产出：`visual.storyboard.v1` 的分镜、`visual.scene.v1`
 * 的场景名、`visual.character.v1` 的角色名，出图状态走 `useRenders`
 * ——和主线四栏工作台看到的是同一份状态，不另起一套。
 *
 * 三个动作按钮（生成视频/重新生成/换模型）本轮**不接**真实调用：
 * 视频生成是 M2 待办，后端根本没有这条链路。点了会明说，不假装成功。
 */
export default function FreeflowStoryboardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { project, output, loading, error } = useProjectOutput(id);
  const renders = useRenders(id);

  return (
    <>
      <ProjectHeader projectId={id} title={project?.title ?? "…"} />
      {error && (
        <p role="alert" className="m-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {!error && loading && <p className="p-6 text-sm text-fg-subtle">加载中…</p>}
      {!error && !loading && (
        <StoryboardEditor
          storyboard={output.storyboard}
          scenes={output.scenes}
          characters={output.characters}
          renders={renders}
        />
      )}
    </>
  );
}
