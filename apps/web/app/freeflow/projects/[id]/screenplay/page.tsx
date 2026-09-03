import { redirect } from "next/navigation";

/** 旧脚本链接兼容到合并后的「故事」一级入口。 */
export default async function FreeflowScreenplayPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/freeflow/projects/${id}/story`);
}
