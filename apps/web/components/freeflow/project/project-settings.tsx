"use client";

import { useState } from "react";
import { Lock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiRequestError, type Project } from "@/lib/api";
import { cn } from "@/lib/utils";

import { ConfirmDialog } from "./feedback";

/**
 * 06 项目设置（需求文档「屏幕 07 分区表」）。
 *
 * 这一页的关键不是把设计稿的表单画像，而是**别画出后端没有的东西**：
 * `projects` 表只有 title / route_type / status / spent_credits /
 * stale_roles，`lib/api.ts` 的 projects 也只有 list/create/get，
 * 没有 update、没有 delete。所以：
 *
 * - 项目名称：只读展示真值 + 说明改名接口没有
 * - 项目类型：只读展示真值（route_type），不是编的
 * - 分辨率/帧率/默认模型/存储位置：后端连列都没有，一律禁用态 + 示例值
 * - 描述：本地 state 真交互（字数统计是真的），但不持久化，写明
 * - 删除项目：`DELETE /projects/{id}` 后端一直都在（软删，见
 *   `lib/api.ts` 的 `projects.remove`），确认流程真的会调用它
 */

const SECTIONS = [
  { key: "basic", label: "基础设置" },
  { key: "members", label: "成员管理" },
  { key: "permissions", label: "权限设置" },
  { key: "advanced", label: "高级设置" },
] as const;

type SectionKey = (typeof SECTIONS)[number]["key"];

const SOON: Record<Exclude<SectionKey, "basic">, string> = {
  members:
    "即将支持：项目级成员要复用全局「成员与团队」的邀请与角色分配组件，后端目前只有 org 级成员，没有项目级成员表。",
  permissions:
    "即将支持：需要先定义项目级角色（所有者/编辑者/查看者）以及它们的操作矩阵，这是产品决策，不是 UI 问题。",
  advanced:
    "即将支持：默认导演 Agent、默认 Skill、Webhook/导出目标都要给 projects 表加列（REQ-060 还牵扯两种模式能否互切）。",
};

/** 后端没有对应列的字段，一律禁用态展示示例值。
 *  改了不会生效，所以不能给可编辑的假象。 */
const MOCK_FIELDS: { label: string; value: string }[] = [
  { label: "分辨率", value: "1920 × 1080" },
  { label: "帧率", value: "24 FPS" },
  { label: "默认模型", value: "Seedance 1.0" },
  { label: "存储位置", value: "自动" },
];

const DESC_LIMIT = 500;

export function ProjectSettings({
  project,
  onDelete,
}: {
  project: Project | null;
  /** 真的会删——由页面层传下来，这里只管确认交互和错误展示。 */
  onDelete: () => Promise<void>;
}) {
  const [section, setSection] = useState<SectionKey>("basic");
  const [description, setDescription] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [typed, setTyped] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const title = project?.title ?? "";
  const nameMatches = typed.trim() === title && title.length > 0;

  async function confirmAndDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDelete();
      // 成功之后页面会跳走（onDelete 里 router.push），这里不用再关弹窗——
      // 组件即将被卸载，setConfirmDelete(false) 反而可能落在卸载后的树上。
    } catch (err) {
      setDeleteError(
        err instanceof ApiRequestError ? err.error.user_message : "删除失败，请稍后重试",
      );
      setDeleting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-[640px] flex-col gap-4 p-6">
      <h1 className="text-sm font-semibold text-fg">项目设置</h1>

      <div className="flex gap-1">
        {SECTIONS.map((s) => (
          <button
            key={s.key}
            type="button"
            aria-pressed={section === s.key}
            onClick={() => setSection(s.key)}
            className={cn(
              "cursor-pointer rounded-lg px-3 py-1.5 text-xs transition-colors duration-150",
              section === s.key
                ? "bg-primary-soft font-medium text-primary"
                : "text-fg-muted hover:bg-surface-2 hover:text-fg",
            )}
          >
            {s.label}
          </button>
        ))}
      </div>

      {section !== "basic" && (
        <div className="rounded-lg border border-dashed border-border-strong bg-surface p-6">
          <p className="text-sm font-medium text-fg">{
            SECTIONS.find((s) => s.key === section)?.label
          }</p>
          <p className="mt-1.5 text-xs leading-5 text-fg-muted">{SOON[section]}</p>
        </div>
      )}

      {section === "basic" && (
        <>
          <div className="flex flex-col gap-3.5 rounded-lg border border-border bg-surface p-4">
            <ReadOnlyField
              label="项目名称"
              value={title || "—"}
              hint="真实值，来自 projects.title。重命名接口未接入：lib/api.ts 的 projects 只有 list / create / get。"
            />

            <div className="grid gap-3 sm:grid-cols-2">
              <ReadOnlyField
                label="项目类型"
                value={project?.route_type ?? "未判定"}
                hint="真实值，来自 projects.route_type（由 Router Agent 判定，不由用户选）。"
              />
              <ReadOnlyField
                label="状态"
                value={project?.status ?? "—"}
                hint="真实值，来自 projects.status。"
              />
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              {MOCK_FIELDS.map((f) => (
                <ReadOnlyField key={f.label} label={f.label} value={f.value} mock />
              ))}
            </div>
            <p className="text-xs text-fg-subtle">
              上面四项是示例值：projects 表里没有这些列，改了不会生效，所以做成禁用态。
            </p>

            {/* 描述是这一页唯一的真交互：本地 state + 字数统计。
                不持久化——后端没有 description 列，也没有 update 接口。 */}
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-fg">描述</span>
              <textarea
                rows={3}
                maxLength={DESC_LIMIT}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="给这个项目写一句话说明"
                className="resize-none rounded-md border border-border-strong bg-surface px-2.5 py-2 text-sm leading-5 text-fg placeholder:text-fg-subtle"
              />
              <span className="tnum self-end text-xs text-fg-subtle">
                {description.length}/{DESC_LIMIT}
              </span>
              <span className="text-xs text-fg-subtle">
                只存在本地：projects 表没有描述列，刷新页面就没了。
              </span>
            </label>
          </div>

          <div className="flex items-center justify-between gap-3 rounded-lg border border-danger/35 p-4">
            <div className="min-w-0">
              <div className="text-sm font-medium text-fg">删除项目</div>
              <div className="mt-0.5 text-xs text-fg-subtle">
                软删除：从列表中移除，产出数据不会立刻物理清除（跟 Skill
                删除是同一套模式）
              </div>
            </div>
            <Button
              size="sm"
              variant="secondary"
              className="border-danger text-danger"
              onClick={() => {
                setTyped("");
                setDeleteError(null);
                setConfirmDelete(true);
              }}
            >
              删除项目
            </Button>
          </div>
        </>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title="删除项目"
        description={`这一步不可撤销。请输入项目名称「${title}」以确认。`}
        confirmLabel={deleting ? "删除中…" : "永久删除"}
        tone="danger"
        confirmDisabled={!nameMatches || deleting}
        disabledReason={!nameMatches ? "输入的名称与项目名不一致。" : undefined}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={confirmAndDelete}
      >
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={title}
          aria-label="输入项目名称以确认删除"
          disabled={deleting}
          className={cn(
            "h-8 w-full rounded-md border bg-surface px-2.5 text-sm text-fg placeholder:text-fg-subtle",
            nameMatches ? "border-success" : "border-border-strong",
          )}
        />
        {/* 失败原因独立展示，不挂在 disabledReason 上——那个只在
            confirmDisabled 时渲染，而失败之后按钮要重新可点以便重试。 */}
        {deleteError && (
          <p role="alert" className="mt-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
            {deleteError}
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}

function ReadOnlyField({
  label,
  value,
  hint,
  mock = false,
}: {
  label: string;
  value: string;
  hint?: string;
  mock?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="flex items-center gap-1 text-xs font-medium text-fg">
        {label}
        <Lock aria-hidden className="size-3 text-fg-subtle" />
        <span className="sr-only">（只读）</span>
      </span>
      <div
        className={cn(
          "flex h-8 items-center rounded-md border border-border bg-surface-2 px-2.5 text-sm",
          mock ? "text-fg-subtle" : "text-fg-muted",
        )}
      >
        {value}
        {mock && <span className="ml-auto text-xs text-fg-subtle">示例值</span>}
      </div>
      {hint && <span className="text-xs text-fg-subtle">{hint}</span>}
    </div>
  );
}
