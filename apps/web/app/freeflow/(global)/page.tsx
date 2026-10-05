import { ProjectHome } from "@/components/freeflow/project-lobby";

/**
 * 01 创作首页：创作输入 → 继续制作 + 需要你处理 → 近期项目。
 *
 * 「只创建项目」与「开始生产」是两个动作（`components/freeflow/home/start-composer.tsx`）：
 * 后端 create 只收标题，原文经 advance 写入并会启动付费生产。
 */
export default function FreeflowHomePage() {
  return <ProjectHome />;
}
