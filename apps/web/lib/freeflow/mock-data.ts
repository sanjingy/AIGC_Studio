/**
 * "分支 B · 自由工作流" 原型的示例数据。
 *
 * 设计交底文档明确写了"文案为示例数据，不代表最终真实内容"（见
 * design-system 里的 README.md「Fidelity」一节）。这里的数据只服务于
 * 结构和交互演示，不接真实后端；等 REQ-011/021/030 这些后端概念真的
 * 落地了，这个文件应该整个删掉，不是逐步替换。
 */
import type { FreeflowGraph, FreeflowTemplate } from "./types";

export const MOCK_GRAPH: FreeflowGraph = {
  nodes: [
    {
      id: "n1",
      type: "input",
      title: "输入",
      params: [{ label: "文件", value: "小说.txt · 字数 32,541" }],
      status: "done",
      statusText: "完成",
      position: { x: 40, y: 120 },
    },
    {
      id: "n2",
      type: "script",
      title: "脚本解析",
      params: [{ label: "路线", value: "漫剧" }],
      status: "done",
      statusText: "完成",
      position: { x: 320, y: 120 },
    },
    {
      id: "n3",
      type: "character",
      title: "角色设计",
      params: [{ label: "风格", value: "写实" }],
      status: "done",
      statusText: "完成",
      position: { x: 600, y: 120 },
    },
    {
      id: "n4",
      type: "scene",
      title: "场景设计",
      params: [{ label: "时代背景", value: "现代" }],
      status: "running",
      statusText: "进行中 62%",
      position: { x: 880, y: 120 },
    },
    {
      id: "n5",
      type: "storyboard",
      title: "分镜生成",
      params: [{ label: "镜头密度", value: "中" }],
      status: "waiting",
      statusText: "等待运行",
      position: { x: 1160, y: 120 },
    },
    {
      id: "n6",
      type: "voice",
      title: "配音生成",
      params: [{ label: "音色", value: "默认" }],
      status: "waiting",
      statusText: "等待运行",
      position: { x: 880, y: 340 },
    },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2" },
    { id: "e2", source: "n2", target: "n3" },
    { id: "e3", source: "n3", target: "n4" },
    { id: "e4", source: "n4", target: "n5" },
    { id: "e5", source: "n4", target: "n6" },
  ],
};

export const MOCK_TEMPLATES: FreeflowTemplate[] = [
  { id: "t1", name: "悬疑漫剧标准线", category: "漫剧动画", presetGraph: MOCK_GRAPH },
  { id: "t2", name: "产品口播广告", category: "营销广告", presetGraph: MOCK_GRAPH },
  { id: "t3", name: "科普解说", category: "解说视频", presetGraph: MOCK_GRAPH },
  { id: "t4", name: "都市情感短剧", category: "短剧", presetGraph: MOCK_GRAPH },
];

export type MockTaskRow = {
  id: string;
  title: string;
  model: string;
  status: "running" | "waiting" | "done" | "failed";
  progress: number | null;
  elapsedMs: number | null;
  tokensSpent: number | null;
  failReason: string | null;
};

/** 05 任务中心。真实任务列表见 `projects.runs()`——这里保留的是
 *  设计稿要求、后端还没有的字段（排队原因 REQ-050、Token 消耗 REQ-051），
 *  Worker C 接线时优先用真数据，缺字段的部分才落回这份 mock。 */
export const MOCK_TASKS: MockTaskRow[] = [
  {
    id: "task-1",
    title: "场景设计 · 第 3 集",
    model: "DeepSeek-V4 · 高精度",
    status: "running",
    progress: 62,
    elapsedMs: 47000,
    tokensSpent: 1820,
    failReason: null,
  },
  {
    id: "task-2",
    title: "分镜出图 · 镜号 12",
    model: "万相 2.5",
    status: "failed",
    progress: null,
    elapsedMs: null,
    tokensSpent: 640,
    failReason: "上游限流，10 分钟后自动重试",
  },
];
