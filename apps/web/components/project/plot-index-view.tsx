/**
 * 情节目录产出。
 *
 * 这一组视图组件只负责把一份产出画出来：不取数、不改状态、不知道折叠。
 * 产出的形状由后端 output_schema 决定，前端拿到的是 any——
 * 所有字段一律按"可能没有"处理，缺一个字段不能把整页炸掉。
 */
export function PlotIndexView({ data }: { data: any }) {
  return (
    <div className="flex flex-col gap-2 p-3">
      <p className="text-sm text-fg-muted">{String(data.logline ?? "")}</p>
      <p className="text-xs text-fg-subtle">核心冲突：{String(data.central_conflict ?? "")}</p>
      <ol className="mt-1 grid grid-cols-1 gap-1 sm:grid-cols-2">
        {(data.nodes ?? []).map((n: any) => (
          <li key={n.index} className="flex gap-2 text-xs">
            <span className="tnum w-5 shrink-0 text-fg-subtle">{n.index}</span>
            <span className="text-fg-muted">{n.summary}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function plotIndexMeta(data: any): string {
  return `${data.nodes?.length ?? 0} 个节点 · ${data.scene_count ?? 0} 场景 · 台词约 ${data.dialogue_chars ?? 0} 字`;
}
