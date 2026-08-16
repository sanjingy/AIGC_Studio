import { Panel, PanelHeader } from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { StatusChip, type Status } from "@/components/ui/status";
import { creditsToYuan, timecode } from "@/lib/utils";

type Shot = {
  no: string;
  status: Status;
  durationMs: number;
  shotSize: string;
  line: string;
  similarity?: number;
};

// 占位数据。真实字段见 09_Database.md §4–5（shot_conditioning / shot_quality_scores）
const SHOTS: Shot[] = [
  { no: "SH-021", status: "succeeded", durationMs: 4200, shotSize: "中景", line: "他推开锈死的铁门。", similarity: 0.91 },
  { no: "SH-022", status: "succeeded", durationMs: 3100, shotSize: "特写", line: "指节泛白。", similarity: 0.88 },
  { no: "SH-023", status: "failed", durationMs: 5000, shotSize: "全景", line: "雾从码头漫上来。" },
  { no: "SH-024", status: "running", durationMs: 4800, shotSize: "近景", line: "「你来晚了。」" },
  { no: "SH-025", status: "running", durationMs: 3600, shotSize: "过肩", line: "她没有回头。" },
  { no: "SH-026", status: "queued", durationMs: 5200, shotSize: "远景", line: "汽笛声撕开夜色。" },
  { no: "SH-027", status: "review", durationMs: 2900, shotSize: "特写", line: "怀表停在三点十七。", similarity: 0.62 },
  { no: "SH-028", status: "draft", durationMs: 4400, shotSize: "中景", line: "脚步声由远及近。" },
];

export default function StoryboardPage() {
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <Panel>
        <PanelHeader
          title="分镜"
          meta="60 个镜头 · 预览档"
          action={
            <div className="flex gap-2">
              <Button size="sm" variant="ghost">重新生成失败项</Button>
              <Button size="sm" variant="primary">确认并进入定稿</Button>
            </div>
          }
        />
        <div className="flex items-center gap-3 border-b border-border px-3 py-2 text-xs text-fg-subtle">
          <span>预览档已完成 23/60</span>
          <span aria-hidden>·</span>
          <span className="tnum">已消耗 {creditsToYuan(14210)}</span>
          <span aria-hidden>·</span>
          <span className="tnum">定稿档预估 {creditsToYuan(6200)}</span>
        </div>

        {/* 接触印相表式网格：图占满，信息压成底部一条细说明带 */}
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3 p-3">
          {SHOTS.map((shot) => (
            <ShotCard key={shot.no} shot={shot} />
          ))}
        </ul>
      </Panel>
    </div>
  );
}

function ShotCard({ shot }: { shot: Shot }) {
  // 一致性分数低于阈值要显性提示——这是废片率的直接来源
  // （17_ConsistencyEngine.md §6，阈值 0.75）
  const weak = shot.similarity !== undefined && shot.similarity < 0.75;

  return (
    <li className="group overflow-hidden rounded-md border border-border bg-surface-2 transition-colors duration-150 hover:border-border-strong">
      <div className="relative aspect-video bg-surface-3">
        {shot.status === "running" && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="animate-pulse-soft text-xs text-running">生成中</span>
          </div>
        )}
        {shot.status === "failed" && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-xs text-danger">生成失败</span>
          </div>
        )}
        <div className="absolute top-1.5 left-1.5">
          <span className="tnum rounded bg-black/65 px-1.5 py-0.5 text-xs font-medium text-white">
            {shot.no}
          </span>
        </div>
        <div className="absolute right-1.5 bottom-1.5">
          <span className="tnum rounded bg-black/65 px-1.5 py-0.5 text-xs text-white">
            {timecode(shot.durationMs)}
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-1.5 p-2">
        <div className="flex items-center justify-between gap-2">
          <StatusChip status={shot.status} />
          <span className="text-xs text-fg-subtle">{shot.shotSize}</span>
        </div>
        <p className="line-clamp-2 text-xs text-fg-muted">{shot.line}</p>
        {shot.similarity !== undefined && (
          <div className="flex items-center gap-1 text-xs">
            <span className="text-fg-subtle">角色相似度</span>
            <span className={weak ? "tnum font-medium text-danger" : "tnum text-fg-muted"}>
              {shot.similarity.toFixed(2)}
            </span>
            {weak && <span className="text-danger">偏低</span>}
          </div>
        )}
      </div>
    </li>
  );
}
