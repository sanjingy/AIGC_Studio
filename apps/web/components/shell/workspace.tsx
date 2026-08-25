"use client";

import * as React from "react";

import type { StageKey } from "@/components/project/stages";

/**
 * 当前打开的项目。
 *
 * 顶栏的面包屑和左栏的「流程」列表都要它，但这两个组件由 `(app)/layout.tsx`
 * 渲染，在项目页的**上面**——数据从下往上传不了，所以走一个 context。
 *
 * 存的全是原始值（字符串、数字），不存 ReactNode 也不存回调：
 * 存节点的话每次 render 身份都变，`useEffect` 会自己把自己叫醒，转成死循环。
 * 左栏点某一步要跳到对应产出，靠的是 `#group-xxx` 锚点，不需要回调。
 */
export type WorkspaceProject = {
  id: string;
  title: string;
  stage: StageKey;
  /** 面包屑第二段，如「第 1 集 · 雾里的手」。没跑出剧本就没有 */
  episodeLabel: string | null;
};

type Ctx = {
  project: WorkspaceProject | null;
  setProject: (p: WorkspaceProject | null) => void;
};

const WorkspaceContext = React.createContext<Ctx>({
  project: null,
  setProject: () => {},
});

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [project, setProject] = React.useState<WorkspaceProject | null>(null);
  const value = React.useMemo(() => ({ project, setProject }), [project]);
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceProject | null {
  return React.useContext(WorkspaceContext).project;
}

/**
 * 项目页用这个把自己登记上去，离开时自动摘掉。
 *
 * 依赖列表里全是原始值，所以只有内容真变了才会重新写入。
 */
export function usePublishWorkspace(project: WorkspaceProject | null) {
  const { setProject } = React.useContext(WorkspaceContext);
  const id = project?.id ?? null;
  const title = project?.title ?? null;
  const stage = project?.stage ?? null;
  const episodeLabel = project?.episodeLabel ?? null;

  React.useEffect(() => {
    if (!id || !title || !stage) {
      setProject(null);
      return;
    }
    setProject({ id, title, stage, episodeLabel });
    return () => setProject(null);
  }, [id, title, stage, episodeLabel, setProject]);
}
