import { GlobalTasks } from "@/components/freeflow/tasks/global-tasks";

/** 全局任务：所有项目的执行任务。项目内的执行队列在 `/freeflow/projects/[id]/tasks?view=queue`。 */
export default function FreeflowGlobalTasksPage() {
  return <GlobalTasks />;
}
