/**
 * 节点画布的示例图（ADR-030 第 5 条：Canvas 路由隐藏、代码保留、不接后端）。
 *
 * 这份数据**只服务于 Canvas**，主链路上不允许出现 mock（决策记录
 * §11.4 裁决 1）。原文件在 `lib/freeflow/mock-data.ts`，同时被首页模板区
 * 引用；首页模板区已随「从模板起手」一起删除，文件随之搬进 canvas 目录，
 * 让「主链路上没有 mock」和「Canvas 冻结时自带它的假数据」两条决定同时成立。
 *
 * 节点图存储与执行引擎真的落地时，这个文件应该整个删掉，不是逐步替换。
 */
import type { FreeflowGraph } from "@/lib/freeflow/types";

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
