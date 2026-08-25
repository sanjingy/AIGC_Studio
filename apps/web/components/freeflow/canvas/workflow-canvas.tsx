"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeTypes,
  type NodeTypes,
  type Viewport,
} from "@xyflow/react";
import { Ban, Copy, Trash2, X } from "lucide-react";

import { NODE_TYPES_NEEDING_BACKEND, type FreeflowNodeType } from "@/lib/freeflow/types";
import { MOCK_GRAPH } from "@/lib/freeflow/mock-data";

import "@xyflow/react/dist/style.css";

import { CanvasActionsContext } from "./canvas-actions";
import { MenuItem, MenuShell } from "./canvas-menu";
import { CanvasRail } from "./canvas-rail";
import { CanvasToolbar } from "./canvas-toolbar";
import { DeletableEdge } from "./deletable-edge";
import { NodeCard } from "./node-card";
import { NodePicker } from "./node-picker";
import {
  CANVAS_EDGE_TYPE,
  CANVAS_NODE_TYPE,
  createNode,
  flowToGraph,
  graphToFlow,
  loadSavedGraph,
  nextEdgeId,
  nextNodeId,
  saveGraph,
  type CanvasNode,
} from "./graph";
import { NODE_DRAG_MIME, NODE_TYPE_META } from "./node-meta";
import { useGraphHistory } from "./use-graph-history";

/** 组件表必须是模块级常量，每次渲染重建会让 React Flow 卸载重挂所有节点 */
const NODE_TYPES: NodeTypes = { [CANVAS_NODE_TYPE]: NodeCard };
const EDGE_TYPES: EdgeTypes = { [CANVAS_EDGE_TYPE]: DeletableEdge };

const DEFAULT_EDGE_OPTIONS = {
  type: CANVAS_EDGE_TYPE,
  markerEnd: { type: MarkerType.ArrowClosed, color: "var(--border-strong)", width: 14, height: 14 },
};

const TOOLBAR_HINT = "滚轮缩放 · 拖拽平移 · Shift 框选/多选 · Delete 删除 · Ctrl+C/V 复制粘贴 · 右键菜单";

/**
 * 把 React Flow 自带样式表里写死的那几个颜色（框选框是 #0059dc 系的蓝）
 * 改指到我们的 token 上。它的规则形如
 * `var(--xy-selection-border, var(--xy-selection-border-default))`，
 * 所以覆盖 `-default` 这一层就够，不用整份重写样式表。
 */
const FLOW_THEME = {
  "--xy-selection-background-color-default": "color-mix(in oklab, var(--primary) 10%, transparent)",
  "--xy-selection-border-default": "1px dotted var(--primary)",
  "--xy-attribution-background-color-default": "color-mix(in oklab, var(--surface) 70%, transparent)",
} as React.CSSProperties;

/** 页面（ProjectHeader 的保存/运行按钮）通过这个句柄调进画布 */
export type CanvasHandle = { save: () => void; run: () => void };

type MenuState =
  | { kind: "add"; x: number; y: number; flowX: number; flowY: number }
  | { kind: "node"; x: number; y: number; nodeId: string }
  | { kind: "edge"; x: number; y: number; edgeId: string }
  | null;

export function WorkflowCanvas(props: {
  projectId: string;
  handleRef: React.RefObject<CanvasHandle | null>;
}) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

function CanvasInner({
  projectId,
  handleRef,
}: {
  projectId: string;
  handleRef: React.RefObject<CanvasHandle | null>;
}) {
  const initial = useMemo(() => graphToFlow(MOCK_GRAPH), []);
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges);
  const [zoom, setZoom] = useState(1);
  const [menu, setMenu] = useState<MenuState>(null);
  const [toast, setToast] = useState<{ id: number; text: string } | null>(null);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const clipboard = useRef<{ nodes: CanvasNode[]; edges: Edge[] }>({ nodes: [], edges: [] });
  const spawnSeq = useRef(0);
  const rf = useReactFlow<CanvasNode, Edge>();

  const notify = useCallback((text: string) => setToast({ id: Date.now(), text }), []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 9000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  // ---- 撤销 / 重做 -------------------------------------------------------
  const read = useCallback(() => ({ nodes: rf.getNodes(), edges: rf.getEdges() }), [rf]);
  const apply = useCallback(
    (snapshot: { nodes: CanvasNode[]; edges: Edge[] }) => {
      setNodes(snapshot.nodes);
      setEdges(snapshot.edges);
    },
    [setNodes, setEdges],
  );
  const history = useGraphHistory(read, apply);
  const { commit, undo, redo, reset: resetHistory } = history;

  // ---- 本地草稿恢复（后端没有节点图存储，REQ-021）------------------------
  useEffect(() => {
    const saved = loadSavedGraph(projectId);
    if (!saved) return;
    const flow = graphToFlow(saved);
    setNodes(flow.nodes);
    setEdges(flow.edges);
    resetHistory();
    notify("已恢复上次「保存」到本浏览器的画布草稿。");
  }, [projectId, setNodes, setEdges, resetHistory, notify]);

  // ---- 节点增删改 --------------------------------------------------------
  const addNodeAt = useCallback(
    (type: FreeflowNodeType, position: { x: number; y: number }) => {
      commit();
      setNodes((ns) => [
        ...ns.map((n) => (n.selected ? { ...n, selected: false } : n)),
        { ...createNode(type, position), selected: true },
      ]);
      setMenu(null);
    },
    [commit, setNodes],
  );

  const addNodeAtCenter = useCallback(
    (type: FreeflowNodeType) => {
      const rect = wrapperRef.current?.getBoundingClientRect();
      const center = rect
        ? rf.screenToFlowPosition({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 })
        : { x: 0, y: 0 };
      // 连点同一个图标时错开落点，否则新节点会精确叠在一起看不出来
      const offset = (spawnSeq.current++ % 5) * 24;
      addNodeAt(type, { x: center.x - 86 + offset, y: center.y - 44 + offset });
    },
    [addNodeAt, rf],
  );

  const deleteNodes = useCallback(
    (ids: string[]) => {
      if (ids.length === 0) return;
      commit();
      const gone = new Set(ids);
      setNodes((ns) => ns.filter((n) => !gone.has(n.id)));
      setEdges((es) => es.filter((e) => !gone.has(e.source) && !gone.has(e.target)));
      setMenu(null);
    },
    [commit, setNodes, setEdges],
  );

  const toggleDisabled = useCallback(
    (ids: string[]) => {
      if (ids.length === 0) return;
      commit();
      const target = new Set(ids);
      setNodes((ns) =>
        ns.map((n) =>
          target.has(n.id) ? { ...n, data: { ...n.data, disabled: !n.data.disabled } } : n,
        ),
      );
      setMenu(null);
    },
    [commit, setNodes],
  );

  /** 复制一组节点及它们之间的连线，整体偏移后落回画布并选中 */
  const duplicate = useCallback(
    (source: { nodes: CanvasNode[]; edges: Edge[] }, offset = 32) => {
      if (source.nodes.length === 0) return;
      commit();
      const idMap = new Map<string, string>();
      const clones = source.nodes.map((n) => {
        const id = nextNodeId();
        idMap.set(n.id, id);
        return {
          ...n,
          id,
          position: { x: n.position.x + offset, y: n.position.y + offset },
          selected: true,
          dragging: false,
        };
      });
      const cloneEdges = source.edges
        .filter((e) => idMap.has(e.source) && idMap.has(e.target))
        .map((e) => {
          const source2 = idMap.get(e.source)!;
          const target2 = idMap.get(e.target)!;
          return { ...e, id: nextEdgeId(source2, target2), source: source2, target: target2, selected: false };
        });
      setNodes((ns) => [...ns.map((n) => (n.selected ? { ...n, selected: false } : n)), ...clones]);
      setEdges((es) => [...es, ...cloneEdges]);
      setMenu(null);
    },
    [commit, setNodes, setEdges],
  );

  const selectionOf = useCallback(
    (nodeId?: string) => {
      const all = rf.getNodes();
      const selected = all.filter((n) => n.selected);
      // 右键点在未选中的节点上时，只作用于这一个，不误伤别处的选中态
      const targets =
        nodeId && !selected.some((n) => n.id === nodeId)
          ? all.filter((n) => n.id === nodeId)
          : selected;
      const ids = new Set(targets.map((n) => n.id));
      return {
        nodes: targets,
        edges: rf.getEdges().filter((e) => ids.has(e.source) && ids.has(e.target)),
      };
    },
    [rf],
  );

  // ---- 连线 --------------------------------------------------------------
  const onConnect = useCallback(
    (connection: Connection) => {
      commit();
      setEdges((es) =>
        addEdge(
          {
            ...connection,
            id: nextEdgeId(connection.source, connection.target),
            type: CANVAS_EDGE_TYPE,
          },
          es,
        ),
      );
    },
    [commit, setEdges],
  );

  const deleteEdge = useCallback(
    (edgeId: string) => {
      commit();
      setEdges((es) => es.filter((e) => e.id !== edgeId));
      setMenu(null);
    },
    [commit, setEdges],
  );

  const canvasActions = useMemo(() => ({ deleteEdge }), [deleteEdge]);

  // ---- 拖拽新增 ----------------------------------------------------------
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const raw = event.dataTransfer.getData(NODE_DRAG_MIME);
      if (!raw || !(raw in NODE_TYPE_META)) return;
      const position = rf.screenToFlowPosition({ x: event.clientX, y: event.clientY });
      addNodeAt(raw as FreeflowNodeType, { x: position.x - 86, y: position.y - 44 });
    },
    [addNodeAt, rf],
  );

  // ---- 快捷键 ------------------------------------------------------------
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (key === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
      } else if (key === "y") {
        event.preventDefault();
        redo();
      } else if (key === "c") {
        clipboard.current = selectionOf();
      } else if (key === "v") {
        event.preventDefault();
        duplicate(clipboard.current, 32);
      } else if (key === "d") {
        event.preventDefault();
        duplicate(selectionOf(), 32);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [undo, redo, duplicate, selectionOf]);

  // ---- 保存 / 运行（暴露给 ProjectHeader）--------------------------------
  const handleSave = useCallback(() => {
    const graph = flowToGraph(rf.getNodes(), rf.getEdges());
    const ok = saveGraph(projectId, graph);
    notify(
      ok
        ? `已保存 ${graph.nodes.length} 个节点 / ${graph.edges.length} 条连线到本机浏览器。后端还没有节点图存储接口（REQ-021），这份草稿不会同步到服务器，换浏览器就没有了。`
        : "保存失败：浏览器拒绝写入本地存储（隐私模式或存储已满）。",
    );
  }, [projectId, rf, notify]);

  const handleRun = useCallback(() => {
    const all = rf.getNodes();
    const pending = all.filter((n) => NODE_TYPES_NEEDING_BACKEND.includes(n.data.nodeType));
    const disabled = all.filter((n) => n.data.disabled);
    const parts = ["运行需要后端执行引擎，本原型未接入（REQ-022/023），没有提交任何任务。"];
    if (pending.length > 0) {
      parts.push(
        `其中 ${pending.length} 个流程控制节点（人工确认/条件判断/循环）会被直接跳过——它们的执行语义还没有定义。`,
      );
    }
    if (disabled.length > 0) parts.push(`另有 ${disabled.length} 个已禁用节点会被跳过。`);
    notify(parts.join(" "));
  }, [rf, notify]);

  useEffect(() => {
    handleRef.current = { save: handleSave, run: handleRun };
    return () => {
      handleRef.current = null;
    };
  }, [handleRef, handleSave, handleRun]);

  // ---- 视口 --------------------------------------------------------------
  const onMove = useCallback((_: unknown, viewport: Viewport) => setZoom(viewport.zoom), []);

  const menuNodeIds = useMemo(
    () => (menu?.kind === "node" ? selectionOf(menu.nodeId).nodes.map((n) => n.id) : []),
    [menu, selectionOf],
  );

  return (
    <CanvasActionsContext.Provider value={canvasActions}>
      <div className="flex min-h-0 flex-1">
        <CanvasRail onAdd={addNodeAtCenter} />

        <div className="flex min-w-0 flex-1 flex-col">
          <div ref={wrapperRef} className="relative min-h-0 flex-1" onDrop={onDrop} onDragOver={onDragOver}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
              connectionLineStyle={{ stroke: "var(--primary)", strokeWidth: 2 }}
              onNodeDragStart={() => commit()}
              onBeforeDelete={async () => {
                commit();
                return true;
              }}
              onMove={onMove}
              onPaneClick={() => setMenu(null)}
              onNodeClick={() => setMenu(null)}
              onPaneContextMenu={(event) => {
                event.preventDefault();
                const flow = rf.screenToFlowPosition({ x: event.clientX, y: event.clientY });
                setMenu({
                  kind: "add",
                  x: event.clientX,
                  y: event.clientY,
                  flowX: flow.x,
                  flowY: flow.y,
                });
              }}
              onNodeContextMenu={(event, node) => {
                event.preventDefault();
                setMenu({ kind: "node", x: event.clientX, y: event.clientY, nodeId: node.id });
              }}
              onEdgeContextMenu={(event, edge) => {
                event.preventDefault();
                setMenu({ kind: "edge", x: event.clientX, y: event.clientY, edgeId: edge.id });
              }}
              deleteKeyCode={["Delete", "Backspace"]}
              multiSelectionKeyCode={["Shift", "Meta", "Control"]}
              selectionKeyCode="Shift"
              panActivationKeyCode="Space"
              minZoom={0.25}
              maxZoom={2}
              fitView
              fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
              proOptions={{ hideAttribution: false }}
              style={FLOW_THEME}
              aria-label="工作流画布"
            >
              {/* 点阵用 --border-strong 而不是 --border：--border 是 slate-200，
                  落在 slate-50 的画布底上 1px 的点基本看不见。 */}
              <Background
                variant={BackgroundVariant.Dots}
                gap={20}
                size={1}
                color="var(--border-strong)"
              />
            </ReactFlow>

            {toast && (
              <div
                role="status"
                className="pointer-events-auto absolute bottom-4 left-1/2 flex max-w-[560px] -translate-x-1/2 items-start gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs leading-5 text-fg-muted shadow-lg"
              >
                <span className="min-w-0 flex-1">{toast.text}</span>
                <button
                  type="button"
                  onClick={() => setToast(null)}
                  aria-label="关闭提示"
                  className="shrink-0 rounded-sm p-0.5 text-fg-subtle hover:text-fg"
                >
                  <X aria-hidden className="size-3.5" />
                </button>
              </div>
            )}
          </div>

          <CanvasToolbar
            zoom={zoom}
            onZoomIn={() => rf.zoomIn({ duration: 120 })}
            onZoomOut={() => rf.zoomOut({ duration: 120 })}
            onZoomReset={() => rf.zoomTo(1, { duration: 120 })}
            onFitView={() => rf.fitView({ padding: 0.25, duration: 160, maxZoom: 1 })}
            onUndo={undo}
            onRedo={redo}
            canUndo={history.canUndo}
            canRedo={history.canRedo}
            hint={TOOLBAR_HINT}
          />
        </div>
      </div>

      {menu?.kind === "add" && (
        <NodePicker
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          onPick={(type) => addNodeAt(type, { x: menu.flowX, y: menu.flowY })}
        />
      )}

      {menu?.kind === "node" && (
        <MenuShell x={menu.x} y={menu.y} onClose={() => setMenu(null)} label="节点操作">
          <MenuItem icon={Copy} hint="Ctrl+D" onSelect={() => duplicate(selectionOf(menu.nodeId))}>
            复制{menuNodeIds.length > 1 ? ` ${menuNodeIds.length} 个节点` : ""}
          </MenuItem>
          <MenuItem icon={Ban} onSelect={() => toggleDisabled(menuNodeIds)}>
            禁用 / 启用
          </MenuItem>
          <MenuItem icon={Trash2} hint="Delete" danger onSelect={() => deleteNodes(menuNodeIds)}>
            删除
          </MenuItem>
        </MenuShell>
      )}

      {menu?.kind === "edge" && (
        <MenuShell x={menu.x} y={menu.y} onClose={() => setMenu(null)} label="连线操作">
          <MenuItem icon={Trash2} hint="Delete" danger onSelect={() => deleteEdge(menu.edgeId)}>
            删除连线
          </MenuItem>
        </MenuShell>
      )}
    </CanvasActionsContext.Provider>
  );
}
