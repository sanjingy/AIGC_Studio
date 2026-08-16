import { ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Metric, Panel, PanelHeader } from "@/components/ui/panel";
import { StatusChip, type Status } from "@/components/ui/status";
import { creditsToYuan } from "@/lib/utils";

// 占位数据。S3/S4 接后端后替换。形状与 09_Database.md 的表结构一致。
const SHOTS = { total: 60, done: 23, running: 4, failed: 2, review: 6 };

const TASKS: { id: string; shot: string; kind: string; status: Status; cost: number }[] = [
  { id: "t_9f2a", shot: "SH-024", kind: "图生视频", status: "running", cost: 103 },
  { id: "t_9f2b", shot: "SH-025", kind: "图生视频", status: "running", cost: 103 },
  { id: "t_9f2c", shot: "SH-026", kind: "关键帧", status: "queued", cost: 21 },
  { id: "t_9f1e", shot: "SH-023", kind: "图生视频", status: "failed", cost: 0 },
  { id: "t_9f1d", shot: "SH-022", kind: "图生视频", status: "succeeded", cost: 103 },
  { id: "t_9f1c", shot: "SH-021", kind: "关键帧", status: "succeeded", cost: 52 },
];

export default function DashboardPage() {
  const pct = Math.round((SHOTS.done / SHOTS.total) * 100);

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      {/* 概览先于细节：一屏顶部就要回答"进度如何、花了多少、有没有出事" */}
      <Panel>
        <div className="grid grid-cols-2 divide-x divide-border md:grid-cols-5">
          <Metric label="镜头进度" value={`${SHOTS.done}/${SHOTS.total}`} hint={`${pct}% 完成`} />
          <Metric label="生成中" value={SHOTS.running} unit="个" tone="running" />
          <Metric label="待确认" value={SHOTS.review} unit="个" />
          <Metric label="失败" value={SHOTS.failed} unit="个" tone={SHOTS.failed ? "danger" : "default"} />
          <Metric label="已消耗" value={creditsToYuan(14210)} hint="预算 ¥288" />
        </div>
        <div className="h-1 w-full overflow-hidden rounded-b-md bg-surface-2">
          <div
            className="h-full bg-primary transition-[width] duration-300"
            style={{ width: `${pct}%` }}
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="镜头完成进度"
          />
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <Panel>
          <PanelHeader
            title="任务队列"
            meta={`${TASKS.length} 条`}
            action={
              <Button size="sm" variant="ghost">
                全部
                <ArrowRight aria-hidden className="size-3" />
              </Button>
            }
          />
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-fg-subtle">
                <th scope="col" className="px-3 py-1.5 font-medium">镜号</th>
                <th scope="col" className="px-3 py-1.5 font-medium">类型</th>
                <th scope="col" className="px-3 py-1.5 font-medium">状态</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">成本</th>
              </tr>
            </thead>
            <tbody>
              {TASKS.map((t) => (
                <tr
                  key={t.id}
                  className="border-b border-border last:border-0 hover:bg-surface-2"
                >
                  <td className="tnum px-3 py-2 font-medium">{t.shot}</td>
                  <td className="px-3 py-2 text-fg-muted">{t.kind}</td>
                  <td className="px-3 py-2">
                    <StatusChip status={t.status} />
                  </td>
                  <td className="tnum px-3 py-2 text-right text-fg-muted">
                    {t.cost ? creditsToYuan(t.cost) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel className="h-fit">
          <PanelHeader title="下一步" />
          <div className="flex flex-col gap-3 p-3">
            <p className="text-sm text-fg-muted">
              6 个镜头的预览已生成，确认后进入定稿档渲染。
            </p>
            <p className="text-xs text-fg-subtle">
              定稿档预计 ¥62，确认前不会产生费用。
            </p>
            <Button variant="primary" className="w-full">
              去确认分镜
            </Button>
          </div>
        </Panel>
      </div>
    </div>
  );
}
