// 视觉来自 ReelFlow 原型，数据由页面注入
// DEV ONLY — 仅供本地目视验收；生产构建始终返回 404。
"use client";

import * as React from "react";
import { notFound } from "next/navigation";
import {
  FileText,
  Film,
  History,
  LayoutDashboard,
  LayoutGrid,
  Library,
  Palette,
  RefreshCw,
  UserRound,
  Wand2,
  Workflow,
} from "lucide-react";

import {
  AsideConsistency,
  AsideStageCard,
  AsideTaskList,
} from "@/components/freeflow/shell/context-aside";
import {
  WorkbenchShell,
  type NavItem,
  type StageState,
} from "@/components/freeflow/shell/workbench-shell";
import { BatchRenderDialog } from "@/components/freeflow/storyboard/batch-render-dialog";
import { ShotGrid, type ShotCardData } from "@/components/freeflow/storyboard/shot-card";
import { ShotDetail } from "@/components/freeflow/storyboard/shot-detail";
import { Button } from "@/components/ui/button";

const PREVIEW_PATH = "/freeflow/dev/shell-preview";

const NAVIGATION: NavItem[] = [
  { id: "overview", label: "项目总览", href: `${PREVIEW_PATH}#overview`, icon: LayoutDashboard },
  { id: "story", label: "故事大纲", href: `${PREVIEW_PATH}#story`, icon: FileText },
  { id: "characters", label: "角色设定", href: `${PREVIEW_PATH}#characters`, icon: UserRound, badge: 4 },
  { id: "scenes", label: "世界美术", href: `${PREVIEW_PATH}#scenes`, icon: Palette, badge: 2 },
  { id: "screenplay", label: "分场剧本", href: `${PREVIEW_PATH}#screenplay`, icon: Film, badge: 8 },
  { id: "storyboard", label: "镜头工作台", href: `${PREVIEW_PATH}#storyboard`, icon: LayoutGrid, badge: 4 },
];

const UTILITY_NAVIGATION: NavItem[] = [
  { id: "tasks", label: "生成队列", href: `${PREVIEW_PATH}#tasks`, icon: Workflow, badge: 2 },
  { id: "assets", label: "资产库", href: `${PREVIEW_PATH}#assets`, icon: Library },
  { id: "history", label: "版本历史", href: `${PREVIEW_PATH}#history`, icon: History },
];

const STAGES: StageState[] = [
  { key: "story", label: "故事", state: "locked" },
  { key: "assets", label: "角色与世界", state: "ready" },
  { key: "script", label: "剧本", state: "approved" },
  { key: "storyboard", label: "镜头", state: "active" },
  { key: "generation", label: "生成", state: "pending" },
];

type PreviewShot = ShotCardData & {
  description: string;
  characters: string[];
  scene?: string;
  dialogue?: string;
};

const SHOTS: PreviewShot[] = [
  {
    index: 1,
    code: "S01-01",
    title: "雨夜的旧站台",
    framing: "大全景",
    camera: "缓慢推进",
    durationLabel: "4.0s",
    status: "ready",
    description: "雨幕切开空旷站台，冷色顶灯在积水里拉成长线。林遥停在安全线外，远处传来列车声。",
    characters: ["林遥 / C01"],
    scene: "旧站台 / L01",
  },
  {
    index: 2,
    code: "S01-02",
    title: "林遥回望",
    framing: "中近景",
    camera: "肩扛微晃",
    durationLabel: "3.5s",
    status: "rendering",
    description: "林遥被身后的金属碰撞声打断，回身时背景灯光被雨水折成细碎光点。",
    characters: ["林遥 / C01"],
    scene: "旧站台 / L01",
    dialogue: "谁在那里？",
  },
  {
    index: 3,
    code: "S01-03",
    title: "站台时钟停摆",
    framing: "特写",
    camera: "固定镜头",
    durationLabel: "2.5s",
    status: "draft",
    description: "秒针停在十二点前一格，玻璃表面映出一道人影，随后被雨痕覆盖。",
    characters: [],
    scene: "旧站台 / L01",
  },
  {
    index: 4,
    code: "S01-04",
    title: "雾中列车驶入",
    framing: "远景",
    camera: "横向跟拍",
    durationLabel: "4.5s",
    status: "failed",
    // 指向一个不存在的地址：预览页要能看到「图像加载失败」那块占位，
    // 它和「还没出图」长得一样，但读屏念的是两句不同的话
    imageUrl: "/freeflow/dev/missing-shot-frame.png",
    description: "雾气被车灯推开，列车沿站台缓慢驶入，窗内却看不到任何乘客。",
    characters: ["林遥 / C01"],
    scene: "旧站台 / L01",
  },
];

export default function ShellPreviewPage() {
  if (process.env.NODE_ENV === "production") notFound();

  const [selectedIndex, setSelectedIndex] = React.useState<number | null>(1);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const selectedShot = SHOTS[selectedIndex ?? 0] ?? SHOTS[0]!;

  return (
    <WorkbenchShell
      project={{
        id: "preview-project",
        title: "雨夜无声",
        subtitle: "EP01",
        savedAgo: "最后保存 12 秒前",
      }}
      navigation={NAVIGATION}
      utilityNavigation={UTILITY_NAVIGATION}
      activeHref={`${PREVIEW_PATH}#storyboard`}
      stages={STAGES}
      primaryAction={{ label: "批量出图", onClick: () => setDialogOpen(true) }}
      aside={
        <div className="space-y-4">
          <AsideStageCard
            stage={STAGES[3]!}
            gate={{
              status: "needs_review",
              onApprove: () => undefined,
              onReject: () => undefined,
            }}
          />
          <AsideTaskList
            tasks={[
              { id: "task-1", title: "S01-02 镜头出图", status: "running", progress: 68 },
              { id: "task-2", title: "S01-04 镜头出图", status: "queued" },
            ]}
            onViewAll={() => undefined}
          />
          <AsideConsistency
            items={[
              { label: "风格档案", value: "已锁定", ok: true },
              { label: "角色基准图", value: "4 / 4 已就绪", ok: true },
              { label: "场景参考图", value: "1 / 2 待补充", ok: false },
            ]}
          />
        </div>
      }
    >
      <section aria-labelledby="preview-title" className="mx-auto w-full max-w-6xl px-5 py-7 lg:px-8 lg:py-9">
        <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
          <div>
            <p className="font-mono text-[10px] tracking-[0.18em] text-primary uppercase">
              Scene 01 / Shot design
            </p>
            <h1 id="preview-title" className="mt-3 text-3xl font-semibold tracking-tight text-fg">
              雨夜抵达
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-fg-muted">
              检查镜头顺序、画面描述与一致性档案，再将已就绪的镜头加入生成队列。
            </p>
          </div>
          <span className="tnum w-fit rounded-full border border-border bg-surface-2 px-3 py-1.5 text-xs text-fg-muted">
            {SHOTS.length} 个镜头
          </span>
        </div>

        <div className="mt-7">
          <ShotGrid shots={SHOTS} selectedIndex={selectedIndex} onSelect={setSelectedIndex} />
        </div>

        <div className="mt-6">
          <ShotDetail
            shot={selectedShot}
            actions={
              <>
                <Button variant="ghost" size="sm" className="border border-border">
                  <RefreshCw aria-hidden className="size-3.5" />
                  重做
                </Button>
                <Button variant="primary" size="sm">
                  <Wand2 aria-hidden className="size-3.5" />
                  单镜出图
                </Button>
              </>
            }
          />
        </div>
      </section>

      <BatchRenderDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        shotCount={SHOTS.length}
        estimateCredits={480}
        estimateRange={{ low: 420, high: 560 }}
        onConfirm={async () => undefined}
      />
    </WorkbenchShell>
  );
}
