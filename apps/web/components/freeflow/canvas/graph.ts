/**
 * `FreeflowGraph`（共享类型，接后端时要重新对齐）与 React Flow 内部节点表示
 * 之间的双向转换，外加保存用的 localStorage 读写。
 *
 * 这里只做**形状转换**，不做校验：REQ-021 明确了图与执行状态要分开存，
 * 而本原型根本没有执行状态的真相源（tasks 表在后端），所以 status 字段
 * 一路只是展示值，不要在这层加任何"推断状态"的逻辑。
 */
import type { Edge, Node } from "@xyflow/react";

import type { FreeflowGraph, FreeflowNode, FreeflowNodeStatus, FreeflowNodeType } from "@/lib/freeflow/types";

import { NODE_TYPE_META } from "./node-meta";

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
      disabled: false,
    },
  };
}

export function toCanvasEdge(edge: { id: string; source: string; target: string }): Edge {
  return { id: edge.id, source: edge.source, target: edge.target, type: CANVAS_EDGE_TYPE };
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
    })),
    edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target })),
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
    const ids = new Set(nodes.map((n) => n.id));
    const edges = graph.edges.filter((e) => e && ids.has(e.source) && ids.has(e.target));
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
