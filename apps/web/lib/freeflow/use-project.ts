"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiRequestError, projects, type Project } from "@/lib/api";

/**
 * 只要项目本身（标题、状态、模型偏好、过期记账）的页面用这个。
 *
 * 需要产出、审核门和推进动作的页面用 `useProjectState`——那一份会多拉
 * 三条接口，标题栏没必要为此等它们。
 */
export function useProject(projectId: string) {
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setProject(await projects.get(projectId));
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiRequestError ? cause.error.user_message : "读取项目失败");
    }
  }, [projectId]);

  useEffect(() => {
    setLoading(true);
    void reload().finally(() => setLoading(false));
  }, [reload]);

  return { project, loading, error, reload };
}
