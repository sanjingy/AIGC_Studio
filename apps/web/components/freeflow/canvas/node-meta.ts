/**
 * 节点类型 / 状态的展示元数据。
 *
 * 只描述"长什么样、默认带哪些参数"，不描述执行语义——执行语义在后端，
 * 而后端还没有节点图的概念（REQ-021/023）。
 */
import {
  Captions,
  FileText,
  Frame,
  GitBranch,
  Hand,
  LogIn,
  LogOut,
  Mic,
  Mountain,
  Repeat,
  User,
  Video,
  Wand2,
  type LucideIcon,
} from "lucide-react";

import type { FreeflowNode, FreeflowNodeStatus, FreeflowNodeType } from "@/lib/freeflow/types";

type NodeParam = FreeflowNode["params"][number];

/** 命名输出锚点。只有多出口节点（目前只有条件判断）需要声明；
 *  不声明的节点走单个匿名右侧锚点，连线的 `sourceHandle` 为 null。 */
export type NodeOutputMeta = {
  /** React Flow 的 handle id，会原样落进 `FreeflowEdge.sourceHandle` */
  id: string;
  label: string;
  /** 锚点与文字的颜色 token，用来在视觉上区分分支 */
  tone: "success" | "danger";
};

export type NodeTypeMeta = {
  label: string;
  icon: LucideIcon;
  /** 从图标栏/右键菜单新建时带上的默认业务参数（REQ-020：业务层参数，不暴露 CFG/Sampler/Seed） */
  defaultParams: NodeParam[];
  /** 多出口节点的分支锚点，按声明顺序自上而下排。省略 = 单出口 */
  outputs?: readonly NodeOutputMeta[];
};

export const NODE_TYPE_META: Record<FreeflowNodeType, NodeTypeMeta> = {
  input: {
    label: "输入",
    icon: LogIn,
    defaultParams: [{ label: "文件", value: "未选择" }],
  },
  script: {
    label: "脚本解析",
    icon: FileText,
    defaultParams: [{ label: "路线", value: "漫剧" }],
  },
  character: {
    label: "角色设计",
    icon: User,
    defaultParams: [{ label: "风格", value: "写实" }],
  },
  scene: {
    label: "场景设计",
    icon: Mountain,
    defaultParams: [{ label: "时代背景", value: "现代" }],
  },
  storyboard: {
    label: "分镜生成",
    icon: Frame,
    defaultParams: [
      { label: "镜头密度", value: "中" },
      { label: "目标时长", value: "5 分钟" },
    ],
  },
  video: {
    // 5 个参数是故意的：REQ-020 要求超过 4 个的收进"更多设置"折叠区，
    // 这个节点就是那条规则的活样本。
    label: "视频生成",
    icon: Video,
    defaultParams: [
      { label: "模型", value: "Seedance" },
      { label: "画幅", value: "16:9" },
      { label: "单镜时长", value: "5s" },
      { label: "帧率", value: "24fps" },
      { label: "运镜强度", value: "中" },
    ],
  },
  voice: {
    label: "配音生成",
    icon: Mic,
    defaultParams: [
      { label: "音色", value: "默认" },
      { label: "语速", value: "1.0x" },
    ],
  },
  effect: {
    label: "特效",
    icon: Wand2,
    defaultParams: [{ label: "类型", value: "转场" }],
  },
  subtitle: {
    label: "字幕",
    icon: Captions,
    defaultParams: [
      { label: "语言", value: "中文" },
      { label: "样式", value: "默认" },
    ],
  },
  export: {
    label: "导出",
    icon: LogOut,
    defaultParams: [
      { label: "格式", value: "MP4" },
      { label: "分辨率", value: "1080P" },
    ],
  },
  manual_confirm: {
    label: "人工确认",
    icon: Hand,
    defaultParams: [{ label: "说明", value: "请确认上一步产出" }],
  },
  condition: {
    // 需求 3.2：条件判断要有"是/否"两个分支出口。可视化条件构造器还没做，
    // 表达式仍是一个展示用参数——先把分支端口和连线语义立住。
    label: "条件判断",
    icon: GitBranch,
    defaultParams: [{ label: "表达式", value: "上一步产出.字数 > 5000" }],
    outputs: [
      { id: "true", label: "是", tone: "success" },
      { id: "false", label: "否", tone: "danger" },
    ],
  },
  loop: {
    label: "循环",
    icon: Repeat,
    defaultParams: [{ label: "循环对象", value: "按集循环" }],
  },
};

/**
 * 左侧图标栏第一组：设计稿是「输入/脚本/角色/场景/分镜/视频/特效/字幕/导出/工具」十项。
 * 「工具」在 `FreeflowNodeType` 里没有对应值，而「配音」有且 MOCK_GRAPH 已经在用，
 * 所以这一格给了「配音」——不为了对齐一张截图去改冻结的共享类型。
 */
export const RAIL_PRODUCTION_TYPES: readonly FreeflowNodeType[] = [
  "input",
  "script",
  "character",
  "scene",
  "storyboard",
  "video",
  "voice",
  "effect",
  "subtitle",
  "export",
];

/** 图标栏第二组 = NODE_TYPES_NEEDING_BACKEND（REQ-023，可拖可连但跑不了） */
export const RAIL_FLOW_TYPES: readonly FreeflowNodeType[] = ["manual_confirm", "condition", "loop"];

export const STATUS_META: Record<
  FreeflowNodeStatus,
  { barClass: string; textClass: string; label: string }
> = {
  done: { barClass: "bg-success", textClass: "text-success", label: "完成" },
  running: { barClass: "bg-running", textClass: "text-running", label: "进行中" },
  waiting: { barClass: "bg-fg-subtle", textClass: "text-fg-subtle", label: "等待运行" },
  failed: { barClass: "bg-danger", textClass: "text-danger", label: "失败" },
};

/** 节点卡片内联展示的参数上限，超出的收进「更多设置」（REQ-020） */
export const INLINE_PARAM_LIMIT = 4;

/** 拖拽时写进 dataTransfer 的键，图标栏和画布约定用它传节点类型 */
export const NODE_DRAG_MIME = "application/x-freeflow-node-type";

/** 该节点类型合法的输出锚点 id 集合；单出口节点返回 null（只接受空 handle）。 */
export function outputHandleIds(type: FreeflowNodeType): Set<string> | null {
  const outputs = NODE_TYPE_META[type].outputs;
  return outputs ? new Set(outputs.map((o) => o.id)) : null;
}
