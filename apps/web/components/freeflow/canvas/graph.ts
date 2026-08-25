/**
 * `FreeflowGraph`（共享类型，接后端时要重新对齐）与 React Flow 内部节点表示
 * 之间的双向转换，外加保存用的 localStorage 读写。
 *
 * 这里只做**形状转换**，不做校验：REQ-021 明确了图与执行状态要分开存，
 * 而本原型根本没有执行状态的真相源（tasks 表在后端），所以 status 字段
 * 一路只是展示值，不要在这层加任何"推断状态"的逻辑。
 */
import type { Edge, Node } from "@xyflow/react";

import type {
  FreeflowEdge,
  FreeflowGraph,
  FreeflowNode,
  FreeflowNodeStatus,
  FreeflowNodeType,
} from "@/lib/freeflow/types";

import { NODE_TYPE_META, outputHandleIds } from "./node-meta";

export type FreeflowNodeData = {
  nodeType: FreeflowNodeType;
  title: string;
  params: FreeflowNode["params"];
  status: FreeflowNodeStatus;
  statusText: string;
  /** 右键菜单「禁用」：留在图里、留着连线，但运行时跳过 */
  disabled: boolean;
};

/** React Flow 里只注册一种节点组件，业务类型放在 data.nodeType 上 */
export type CanvasNode = Node<FreeflowNodeData, "freeflow">;

export const CANVAS_NODE_TYPE = "freeflow";
export const CANVAS_EDGE_TYPE = "freeflow";

export function toCanvasNode(node: FreeflowNode): CanvasNode {
  return {
    id: node.id,
    type: CANVAS_NODE_TYPE,
    position: { ...node.position },
    data: {
      nodeType: node.type,
      title: node.title,
      params: node.params.map((p) => ({ ...p })),
      status: node.status,
      statusText: node.statusText,
      // 老数据没有 disabled 字段，缺省按"未禁用"读
      disabled: node.disabled ?? false,
    },
  };
}

export function toCanvasEdge(edge: FreeflowEdge): Edge {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    // 端口原样透传：单出口节点这两个是 null，多出口（条件判断）带 "true"/"false"
    sourceHandle: edge.sourceHandle ?? null,
    targetHandle: edge.targetHandle ?? null,
    type: CANVAS_EDGE_TYPE,
  };
}

export function graphToFlow(graph: FreeflowGraph): { nodes: CanvasNode[]; edges: Edge[] } {
  return {
    nodes: graph.nodes.map(toCanvasNode),
    edges: graph.edges.map(toCanvasEdge),
  };
}

export function flowToGraph(nodes: CanvasNode[], edges: Edge[]): FreeflowGraph {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.data.nodeType,
      title: n.data.title,
      params: n.data.params,
      status: n.data.status,
      statusText: n.data.statusText,
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      disabled: n.data.disabled,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? null,
      targetHandle: e.targetHandle ?? null,
    })),
  };
}

let idSeq = 0;

export function nextNodeId(): string {
  idSeq += 1;
  return `nf-${Date.now().toString(36)}-${idSeq}`;
}

export function nextEdgeId(source: string, target: string): string {
  idSeq += 1;
  return `ef-${source}-${target}-${idSeq}`;
}

export function createNode(type: FreeflowNodeType, position: { x: number; y: number }): CanvasNode {
  const meta = NODE_TYPE_META[type];
  return {
    id: nextNodeId(),
    type: CANVAS_NODE_TYPE,
    position,
    data: {
      nodeType: type,
      title: meta.label,
      params: meta.defaultParams.map((p) => ({ ...p })),
      status: "waiting",
      statusText: "等待运行",
      disabled: false,
    },
  };
}

/**
 * 把一条连线的 `sourceHandle` 校到源节点当前真实拥有的锚点上。
 *
 * React Flow 对**找不到的 handle id 直接不渲染这条边**，且不报错——图里明明
 * 有这条边，画布上却是空的。本地存的是上一版前端写的，节点的出口定义变了
 * 以后不能拿它当可信输入，所以读回来时统一过一遍：
 *  - 多出口节点收到空 handle（这条边是加分支之前存的）→ 落到第一个分支，
 *    保住老数据的连线而不是让它凭空消失；
 *  - handle id 不在该类型的出口里 → 丢掉这条边，画布上看不到的边不该留在图里；
 *  - 单出口节点带着残留的 handle id → 抹成 null。
 */
function normalizeEdgeHandle(edge: FreeflowEdge, sourceType: FreeflowNodeType): FreeflowEdge | null {
  const ids = outputHandleIds(sourceType);
  if (!ids) return { ...edge, sourceHandle: null };
  const first = NODE_TYPE_META[sourceType].outputs![0]!.id;
  if (edge.sourceHandle == null) return { ...edge, sourceHandle: first };
  return ids.has(edge.sourceHandle) ? edge : null;
}

/**
 * 保存只写浏览器 localStorage。后端没有节点图存储接口（REQ-021），
 * 与其假装保存成功，不如把"只存在这台浏览器里"写进提示文案。
 */
export const storageKey = (projectId: string) => `freeflow:canvas:${projectId}`;

export function loadSavedGraph(projectId: string): FreeflowGraph | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(storageKey(projectId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const graph = parsed as Partial<FreeflowGraph>;
    if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) return null;
    // 只认识 NODE_TYPE_META 里有的类型，别的直接丢——本地存的是上一版前端写的，
    // 类型union 变了以后不能拿它当可信输入。
    const nodes = graph.nodes.filter((n) => n && n.type in NODE_TYPE_META);
    const typeOf = new Map(nodes.map((n) => [n.id, n.type]));
    const edges = graph.edges
      .filter((e) => e && typeOf.has(e.source) && typeOf.has(e.target))
      .map((e) => normalizeEdgeHandle(e, typeOf.get(e.source)!))
      .filter((e): e is FreeflowEdge => e !== null);
    return { nodes, edges };
  } catch {
    return null;
  }
}

export function saveGraph(projectId: string, graph: FreeflowGraph): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(storageKey(projectId), JSON.stringify(graph));
    return true;
  } catch {
    return false;
  }
}
