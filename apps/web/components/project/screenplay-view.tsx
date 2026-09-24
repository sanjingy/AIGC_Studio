/**
 * 剧本产出：逐集逐场展开，动作 / 音效 / 旁白 / 对白各有各的排版。
 *
 * `episodeIndex` 是项目栏选中的那一集，只筛这里的显示——「集」在后端不是
 * 一等实体（只是 `screenplay.episodes` 里的一项），筛选不改变任何生产范围。
 */
export function ScreenplayView({
  data,
  episodeIndex = null,
}: {
  data: any;
  episodeIndex?: number | null;
}) {
  const episodes = (data.episodes ?? []).filter(
    (ep: any) => episodeIndex === null || Number(ep?.index) === episodeIndex,
  );

  return (
    <div className="flex flex-col gap-3 p-3">
      <p className="max-w-[68ch] text-sm leading-6 text-fg-muted">{String(data.synopsis ?? "")}</p>
      {episodes.map((ep: any) => (
        <div key={ep.index} className="flex flex-col gap-2">
          <div className="text-sm font-medium">
            第 {ep.index} 集 {ep.title}
          </div>
          {(ep.scenes ?? []).map((sc: any) => (
            <div key={sc.id} className="ff-paper-scene bg-surface-2 px-3 py-2.5">
              <div className="text-xs text-fg-subtle">
                {sc.id}　【{sc.location} - {sc.time_mood}】
              </div>
              <div className="mt-1.5 flex max-w-[68ch] flex-col gap-1 text-xs leading-6">
                {(sc.beats ?? []).map((b: any, i: number) => (
                  <p key={i} className="text-fg-muted">
                    {b.kind === "action" && `△${b.text}`}
                    {b.kind === "sfx" && `【音效：${b.text}】`}
                    {b.kind === "vo" && (
                      <>
                        <span className="text-fg">{b.character_ref}（VO）</span>：{b.text}
                      </>
                    )}
                    {b.kind === "dialogue" && (
                      <>
                        <span className="text-fg">
                          {b.character_ref}
                          {b.emotion && `（${b.emotion}）`}
                        </span>
                        ：{b.text}
                      </>
                    )}
                  </p>
                ))}
                {sc.hook && <p className="text-primary">【钩子】{sc.hook}</p>}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export function screenplayMeta(data: any): string {
  return `《${data.title ?? ""}》，${data.episodes?.length ?? 0} 集，覆盖 ${data.node_coverage?.length ?? 0} 个节点`;
}
