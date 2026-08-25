/** 场景档案产出。摄影主轴与固定参照物是一致性引擎要用的，别省。 */
export function ScenesView({ data }: { data: any }) {
  return (
    <div className="flex flex-col gap-2 p-3">
      {(data.scenes ?? []).map((sc: any) => (
        <div key={sc.ref} className="rounded-md bg-surface-2 px-2.5 py-2">
          <div className="flex flex-wrap items-baseline gap-2 text-sm">
            <span className="font-medium">{sc.name}</span>
            <span className="text-xs text-fg-subtle">{sc.ref}</span>
            <span className="text-xs text-fg-subtle">{sc.time_slot}</span>
          </div>
          <p className="mt-0.5 text-xs text-fg-muted">{sc.setting}</p>
          <p className="mt-0.5 text-xs text-fg-subtle">光影：{sc.lighting}</p>
          {sc.camera_axis && (
            <p className="mt-0.5 text-xs text-fg-subtle">
              摄影主轴：{sc.camera_axis.position} → {sc.camera_axis.facing} →{" "}
              {sc.camera_axis.far_end}
            </p>
          )}
          {(sc.fixed_references ?? []).length > 0 && (
            <p className="mt-0.5 text-xs text-fg-subtle">
              固定参照物：{(sc.fixed_references ?? []).join("；")}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

export function scenesMeta(data: any): string {
  return `${data.scenes?.length ?? 0} 个场景 · ${data.era ?? ""}`;
}
