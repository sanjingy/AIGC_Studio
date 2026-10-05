"use client";

import { use, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { ProjectWorkbench } from "@/components/freeflow/project/project-workbench";
import { TaskCenter } from "@/components/freeflow/project/task-center";
import { GenerationRecords } from "@/components/freeflow/project/generation-records";
import { Button } from "@/components/ui/button";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

type View = "records" | "queue";

const VIEW_NOTE: Record<View, string> = {
  records: "回看创作过程：文本步骤和出图各自的输入、提示词和结果。",
  queue: "出图等执行任务：进度、失败原因，以及能重试 / 取消的任务。状态以任务表为准，实时更新。",
};

/**
 * 项目的「生成」页，两个视图分开：
 *
 * - **生成记录**：回看（`/generation-records`，文本步骤 + 出图）；
 * - **执行队列**：`tasks`（执行状态的唯一真相），状态与进度走项目 SSE，重试 / 取消在这里。
 *
 * 视图和定位都写在 URL 上（`?view=queue&task=…`、`?view=records&record_type=…&record_id=…`），
 * 刷新后回到同一处，全局任务页和记录详情也能直接链过来。
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
  const router = useRouter();
  const search = useSearchParams();

  const view: View = search.get("view") === "queue" || search.get("task") ? "queue" : "records";
  const focusTask = search.get("task");
  const recordType = search.get("record_type");
  const recordId = search.get("record_id");
  const recordFocus =
    (recordType === "agent" || recordType === "image") && recordId
      ? { type: recordType as "agent" | "image", id: recordId }
      : null;

  const liveStatus = useMemo(() => new Map(tasks.items.map((t) => [t.id, t.status as string])), [tasks.items]);
  const base = `/freeflow/projects/${encodeURIComponent(id)}/tasks`;

  function switchTo(next: View) {
    if (next === view) return;
    router.replace(`${base}?view=${next}`, { scroll: false });
  }

  return (
    <ProjectWorkbench
      projectId={id}
      state={state}
      tasks={tasks}
      images={images}
      activeHref={pathname}
    >
      <div className="mx-auto flex w-full max-w-[980px] flex-col gap-3 p-6">
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="生成视图">
          <Button variant={view === "records" ? "primary" : "ghost"} aria-pressed={view === "records"} onClick={() => switchTo("records")}>
            生成记录
          </Button>
          <Button variant={view === "queue" ? "primary" : "ghost"} aria-pressed={view === "queue"} onClick={() => switchTo("queue")}>
            执行队列
          </Button>
        </div>
        <p className="text-xs leading-5 text-fg-subtle">{VIEW_NOTE[view]}</p>
        {view === "records" ? (
          <GenerationRecords
            projectId={id}
            focus={recordFocus}
            liveStatus={liveStatus}
            queueHref={(taskId) => `${base}?view=queue&task=${encodeURIComponent(taskId)}`}
          />
        ) : (
          <TaskCenter tasks={tasks} projectId={id} focusId={focusTask} />
        )}
      </div>
    </ProjectWorkbench>
  );
}
