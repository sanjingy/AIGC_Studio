import { RenderSlot } from "@/components/project/render-slot";
import type { Renders } from "@/lib/useRenders";

/**
 * 分镜表产出。9 列，与三份提示词里的分镜表列名一一对应。
 *
 * 带上 `renders` 时多出第 10 列「画面」——那是这张表唯一能变成图的地方。
 */
export function StoryboardView({ data, renders }: { data: any; renders?: Renders }) {
  return (
    // 宽表在自己的容器里横向滚动，页面本身不横向滚
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-xs">
        <thead>
          <tr className="border-b border-border text-left text-fg-subtle">
            <th className="px-3 py-1.5 font-medium">镜号</th>
            <th className="px-2 py-1.5 font-medium">节点</th>
            <th className="px-2 py-1.5 font-medium">景别</th>
            <th className="px-2 py-1.5 font-medium">角度</th>
            <th className="px-2 py-1.5 font-medium">运镜</th>
            <th className="px-2 py-1.5 font-medium">画面内容</th>
            <th className="px-2 py-1.5 font-medium">出场人物</th>
            <th className="px-2 py-1.5 font-medium">场景</th>
            <th className="px-2 py-1.5 font-medium">对白 / 音效</th>
            {renders && <th className="px-2 py-1.5 font-medium">画面</th>}
          </tr>
        </thead>
        <tbody>
          {(data.shots ?? []).map((s: any) => (
            <tr key={s.index} className="border-b border-border align-top last:border-0">
              <td className="tnum px-3 py-1.5">{s.index}</td>
              <td className="tnum px-2 py-1.5 text-fg-subtle">{s.node_index}</td>
              <td className="px-2 py-1.5 whitespace-nowrap">{s.shot_size}</td>
              <td className="px-2 py-1.5 text-fg-subtle">{s.angle || "—"}</td>
              <td className="px-2 py-1.5 whitespace-nowrap text-fg-subtle">
                {s.camera_move || "—"}
              </td>
              <td className="px-2 py-1.5 text-fg-muted">{s.content}</td>
              <td className="px-2 py-1.5 text-fg-subtle">
                {(s.character_refs ?? []).join("、") || "—"}
              </td>
              <td className="px-2 py-1.5 text-fg-subtle">{s.scene_ref}</td>
              <td className="px-2 py-1.5 text-fg-muted">
                {s.dialogue && (
                  <div>
                    {s.speaker_ref}：{s.dialogue}
                  </div>
                )}
                {s.sfx && <div className="text-fg-subtle">【{s.sfx}】</div>}
                {!s.dialogue && !s.sfx && "—"}
              </td>
              {renders && (
                <td className="px-2 py-1.5">
                  <RenderSlot
                    subject={{ kind: "shot", index: s.index }}
                    renders={renders}
                    label="生成"
                    alt={`第 ${s.index} 镜`}
                    className="w-24"
                    aspect="aspect-video"
                  />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function storyboardMeta(data: any): string {
  return `${data.nodes?.length ?? 0} 个节点 · ${data.shots?.length ?? 0} 个镜号`;
}
