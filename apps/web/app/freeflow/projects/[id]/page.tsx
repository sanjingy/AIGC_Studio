"use client";

import { use } from "react";
import { redirect } from "next/navigation";

// 项目层没有落地页本身——进项目直接落到工作流画布，
// 复用 REQ-010 的判定逻辑（这里简化为固定落 canvas，
// 真实的"按生产阶段判定落地页"留给后端有阶段概念之后再做）。
export default function ProjectIndexPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  redirect(`/freeflow/projects/${id}/canvas`);
}
