"use client";

import { use, useState } from "react";
import { usePathname } from "next/navigation";

import { ProjectWorkbench } from "@/components/freeflow/project/project-workbench";
import { TaskCenter } from "@/components/freeflow/project/task-center";
import { GenerationRecords } from "@/components/freeflow/project/generation-records";
import { Button } from "@/components/ui/button";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

/**
 * 生成队列。列表读 `tasks`（执行状态的唯一真相），状态与进度走项目 SSE。
 *
 * 这里的 `useTasks` 和右栏那份是同一个 hook 的两次调用，但它们共用同一条
 * SSE 连接（`useProjectEvents` 按项目 id 计数复用），不会开两条。
 *
 * 不给顶栏主按钮：这一页是看结果的，推进生产的入口在制作流程那几页。
 */
export default function FreeflowTasksPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const state = useProjectState(id);
  const tasks = useTasks(id);
  const images = useImages(id);
  const pathname = usePathname();
  const [tab, setTab] = useState<"records" | "queue">("records");

  return (
    <ProjectWorkbench
      projectId={id}
      state={state}
      tasks={tasks}
      images={images}
      activeHref={pathname}
    >
      <div className="mb-5 flex gap-2" aria-label="生成记录视图">
        <Button variant={tab === "records" ? "primary" : "ghost"} onClick={() => setTab("records")}>生成记录</Button>
        <Button variant={tab === "queue" ? "primary" : "ghost"} onClick={() => setTab("queue")}>运行队列</Button>
      </div>
      {tab === "records" ? <GenerationRecords projectId={id} /> : <TaskCenter tasks={tasks} projectId={id} />}
    </ProjectWorkbench>
  );
}
