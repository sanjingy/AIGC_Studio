"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiRequestError, projects, type AgentRun, type Project } from "@/lib/api";

/**
 * 项目层「脚本/角色/场景/分镜」几个 tab 复用的数据获取——原样照抄
 * `app/(app)/projects/[id]/page.tsx` 里 `outputOf` 的取法（按 agent_id
 * 取最新一版 output_json），不重新设计一套。两边分叉迟早对不上。
 */
const AGENT_ID_OF = {
  plot_index: "story.plot_index.v1",
  screenplay: "story.screenplay.v1",
  characters: "visual.character.v1",
  scenes: "visual.scene.v1",
  storyboard: "visual.storyboard.v1",
} as const;

export type FreeflowOutputKey = keyof typeof AGENT_ID_OF;

export function useProjectOutput(projectId: string) {
  const [project, setProject] = useState<Project | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const [p, r] = await Promise.all([projects.get(projectId), projects.runs(projectId)]);
    setProject(p);
    setRuns(r);
  }, [projectId]);

  useEffect(() => {
    setLoading(true);
    reload()
      .catch((e) => setError(e instanceof ApiRequestError ? e.error.user_message : "加载失败"))
      .finally(() => setLoading(false));
  }, [reload]);

  const outputOf = (agentId: string) =>
    runs.find((r) => r.agent_id === agentId && r.output_json)?.output_json;

  const output: Record<FreeflowOutputKey, any> = {
    plot_index: outputOf(AGENT_ID_OF.plot_index),
    screenplay: outputOf(AGENT_ID_OF.screenplay),
    characters: outputOf(AGENT_ID_OF.characters),
    scenes: outputOf(AGENT_ID_OF.scenes),
    storyboard: outputOf(AGENT_ID_OF.storyboard),
  };

  return { project, runs, output, loading, error, reload };
}
