import { redirect } from "next/navigation";

/** 项目默认落到真实数据概览，未接后端的画布保留为非默认专家原型。 */
export default async function ProjectIndexPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/freeflow/projects/${id}/overview`);
}
