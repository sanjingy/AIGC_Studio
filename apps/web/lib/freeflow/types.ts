/**
 * "分支 B · 自由工作流" 原型专用类型。
 *
 * 这些类型描述的是**前端原型状态**，不是后端契约——节点图目前没有持久化
 * 接口（见需求文档 REQ-021），本轮全部走本地状态 + mock 数据。真正接后端
 * 时这份类型要重新对齐 `agents/schemas.py` 一类的后端 schema，不能假设
 * 这里定义的形状会原样保留。
 */

/** 左侧图标栏对应的节点类型。人工确认/条件判断/循环三种见 REQ-023，
 *  UI 上存在，但不接真实执行——按 needsBackend 统一标注"即将支持"。 */
export type FreeflowNodeType =
  | "input"
  | "script"
  | "character"
  | "scene"
  | "storyboard"
  | "video"
  | "voice"
  | "effect"
  | "subtitle"
  | "export"
  | "manual_confirm"
  | "condition"
  | "loop";

export type FreeflowNodeStatus = "done" | "running" | "waiting" | "failed";

export type FreeflowNode = {
  id: string;
  type: FreeflowNodeType;
  title: string;
  /** 节点卡片内联展示的业务参数，最多 4 个（REQ-020），键值都是展示用字符串 */
  params: { label: string; value: string }[];
  status: FreeflowNodeStatus;
  statusText: string;
  position: { x: number; y: number };
  /** 右键菜单「禁用」：留在图里、留着连线，但运行时跳过。
   *  可选——老数据没有这个字段，读回来按未禁用处理。 */
  disabled?: boolean;
};

export type FreeflowEdge = {
  id: string;
  source: string;
  target: string;
  /** 源/目标节点上的**命名锚点** id（React Flow 的 `sourceHandle`/`targetHandle`）。
   *  条件判断这类多出口节点靠它区分"是"/"否"分支（见 `NODE_TYPE_META[].outputs`）。
   *  可选：单出口节点不填，等同旧行为；老数据缺这两个字段也能原样读回来。 */
  sourceHandle?: string | null;
  targetHandle?: string | null;
};

export type FreeflowGraph = {
  nodes: FreeflowNode[];
  edges: FreeflowEdge[];
};

/** 节点类型是否需要后端执行语义才能真正跑起来（REQ-022/023）。
 *  这三种在左侧图标栏可点、可拖进画布，但状态只能是 waiting，
 *  点击运行要给出"即将支持"提示，不能假装能跑。 */
export const NODE_TYPES_NEEDING_BACKEND: readonly FreeflowNodeType[] = [
  "manual_confirm",
  "condition",
  "loop",
];

export type FreeflowTemplateCategory = "漫剧动画" | "营销广告" | "解说视频" | "短剧";

export type FreeflowTemplate = {
  id: string;
  name: string;
  category: FreeflowTemplateCategory;
  /** 新建项目时整体复制为初始工作流（REQ-011）。原型里只是摆设，
   *  点了不会真的建项目——建项目走 projects.create()，这个字段留着
   *  是为了让后端接线时结构已经在。 */
  presetGraph: FreeflowGraph;
};

export const QUICK_START_ITEMS = [
  {
    id: "director",
    title: "AI 导演模式",
    description: "对话驱动，AI 自动推进到确认点",
    href: "/dashboard",
  },
  {
    id: "canvas",
    title: "自由画布模式",
    description: "空画布 + 输入起始节点，自己拖节点连线",
    href: null, // 新建项目后跳 /freeflow/projects/[id]/canvas，原型里点了就近似
  },
  {
    id: "import",
    title: "导入已有作品",
    description: "上传小说/剧本，自动识别可复用素材",
    href: null,
  },
  {
    id: "template",
    title: "模板中心",
    description: "从预置节点图开始",
    href: null,
  },
] as const;

/** 素材库一级类型筛选（REQ-030）。角色/场景/分镜/Workflow/Skill 是
 *  结构化产出而非文件——目前只有角色（CharacterEntry）接了真实索引，
 *  场景/分镜/Workflow/Skill 后端还没有跨类型索引表，chip 存在但筛出来
 *  是空列表，不是假装有数据。 */
export type AssetKind =
  | "all"
  | "image"
  | "video"
  | "audio"
  | "character"
  | "scene"
  | "storyboard"
  | "document"
  | "workflow"
  | "skill";

export const ASSET_KIND_LABEL: Record<AssetKind, string> = {
  all: "全部",
  image: "图片",
  video: "视频",
  audio: "音频",
  character: "角色",
  scene: "场景",
  storyboard: "分镜",
  document: "文档",
  workflow: "Workflow",
  skill: "Skill",
};
