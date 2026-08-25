"use client";

import type { FreeflowNodeType } from "@/lib/freeflow/types";

import { MenuItem, MenuLabel, MenuSeparator, MenuShell } from "./canvas-menu";
import { NODE_TYPE_META, RAIL_FLOW_TYPES, RAIL_PRODUCTION_TYPES } from "./node-meta";

/**
 * 画布空白处右键「添加节点」的类型选择（需求 3.1）。
 * 上面一组是左侧图标栏的十项，下面一组是流程控制三项——后者标了
 * 「即将支持」，因为它们需要新的后端执行语义（REQ-023）。
 */
export function NodePicker({
  x,
  y,
  onPick,
  onClose,
}: {
  x: number;
  y: number;
  onPick: (type: FreeflowNodeType) => void;
  onClose: () => void;
}) {
  return (
    <MenuShell x={x} y={y} onClose={onClose} label="添加节点">
      <MenuLabel>添加节点</MenuLabel>
      {RAIL_PRODUCTION_TYPES.map((type) => (
        <MenuItem key={type} icon={NODE_TYPE_META[type].icon} onSelect={() => onPick(type)}>
          {NODE_TYPE_META[type].label}
        </MenuItem>
      ))}
      <MenuSeparator />
      <MenuLabel>流程控制</MenuLabel>
      {RAIL_FLOW_TYPES.map((type) => (
        <MenuItem
          key={type}
          icon={NODE_TYPE_META[type].icon}
          hint="即将支持"
          onSelect={() => onPick(type)}
        >
          {NODE_TYPE_META[type].label}
        </MenuItem>
      ))}
    </MenuShell>
  );
}
