"use client";

import { useEffect, useState } from "react";
import { Loader2, Lock } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  ApiRequestError,
  modelCatalog,
  projects,
  type CapabilityModels,
  type Project,
} from "@/lib/api";
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
 * - 分辨率/帧率/存储位置：后端连列都没有，一律禁用态 + 示例值
 * - 描述：本地 state 真交互（字数统计是真的），但不持久化，写明
 * - 删除项目：`DELETE /projects/{id}` 后端一直都在（软删，见
 *   `lib/api.ts` 的 `projects.remove`），确认流程真的会调用它
 *
 * **「默认模型」这次从示例值变成真的**（ADR-024 接线）。它原本和分辨率、
 * 帧率一起躺在 `MOCK_FIELDS` 里写着 `Seedance 1.0`——那个值本身就是编的，
 * 后端目录里从来没有过这个模型。现在选项来自 `/model-catalog`，选中项来自
 * `projects.model_preference`，切换调 `PATCH /projects/{id}/model-preference`。
 *
 * 两处克制：
 * - **只按能力给下拉，不编"经济/标准/高质"三档。** ADR-024 说面向用户的
 *   应该是档位而不是模型 id，但"档位→模型"那张映射表还没建。每个能力现在
 *   只有两个真实模型，硬凑出一个不存在的"标准档"是往界面上加假东西。
 * - **没接入的能力（视频、语音）不给下拉框**，照实说未接入，原因由后端给。
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
 *  改了不会生效，所以不能给可编辑的假象。
 *
 *  「默认模型」已经从这里移走——`projects.model_preference` 是真列，
 *  下面 `ModelPreferenceCard` 里是真下拉。 */
const MOCK_FIELDS: { label: string; value: string }[] = [
  { label: "分辨率", value: "1920 × 1080" },
  { label: "帧率", value: "24 FPS" },
  { label: "存储位置", value: "自动" },
];

/** 跟随后端默认路由。用空串而不是 undefined：`<select>` 的 value 必须是字符串。 */
const FOLLOW_DEFAULT = "";

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
              上面三项是示例值：projects 表里没有这些列，改了不会生效，所以做成禁用态。
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

          <ModelPreferenceCard project={project} />

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

// ---------------------------------------------------------------- 默认模型

/**
 * 项目级模型覆盖（ADR-024）。
 *
 * 选中项的真相是 `project.model_preference`，不是这里的 state——组件只在
 * 挂载时把它读进来，之后每次切换都以服务端返回的那份为准。乐观更新在这里
 * 没有价值：一次 PATCH 是几十毫秒的事，而猜错了会让用户以为自己已经切过了。
 */
function ModelPreferenceCard({ project }: { project: Project | null }) {
  const [items, setItems] = useState<CapabilityModels[] | null>(null);
  const [preference, setPreference] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    modelCatalog
      .list()
      .then((r) => setItems(r.items))
      .catch(() => setLoadFailed(true));
  }, []);

  useEffect(() => {
    setPreference(project?.model_preference ?? {});
  }, [project]);

  async function choose(capability: string, modelId: string) {
    if (!project) return;
    setSaving(capability);
    setError(null);
    try {
      const updated = await projects.setModelPreference(
        project.id,
        capability,
        modelId === FOLLOW_DEFAULT ? null : modelId,
      );
      // 用服务端返回的整份偏好覆盖：它是合并之后的结果，
      // 本地拼一份出来迟早会和后端的合并规则分叉
      setPreference(updated.model_preference);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "保存失败，请稍后重试");
    } finally {
      setSaving(null);
    }
  }

  if (loadFailed) {
    return (
      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="text-sm font-medium text-fg">默认模型</div>
        <p className="mt-1 text-xs text-fg-subtle">
          模型目录加载失败，暂时改不了。已经存下的偏好不受影响，生成仍按它执行。
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3.5 rounded-lg border border-border bg-surface p-4">
      <div>
        <div className="text-sm font-medium text-fg">默认模型</div>
        <p className="mt-1 text-xs leading-5 text-fg-subtle">
          只对这个项目生效。不选就跟随系统默认档；选中的模型出故障时仍会自动
          切到同能力的下一个，不会因为选过一次就把容错关掉。
        </p>
      </div>

      {!items && <p className="text-xs text-fg-subtle">加载中…</p>}

      {items?.map((item) =>
        item.available ? (
          <label key={item.capability} className="flex flex-col gap-1">
            <span className="flex items-center gap-1.5 text-xs font-medium text-fg">
              {item.label}
              {saving === item.capability && (
                <Loader2 aria-hidden className="size-3 animate-spin text-fg-subtle" />
              )}
            </span>
            <select
              value={preference[item.capability] ?? FOLLOW_DEFAULT}
              disabled={!project || saving !== null}
              onChange={(e) => void choose(item.capability, e.target.value)}
              className={cn(
                "h-8 w-full cursor-pointer rounded-md border border-border-strong bg-surface px-2 text-sm text-fg",
                "transition-colors duration-150 hover:border-fg-subtle",
                "disabled:cursor-not-allowed disabled:opacity-45",
              )}
            >
              <option value={FOLLOW_DEFAULT}>
                跟随系统默认
                {item.default_model_id ? `（${item.default_model_id}）` : ""}
              </option>
              {item.models.map((m) => (
                <option key={m.model_id} value={m.model_id}>
                  {m.label} · {m.model_id}
                </option>
              ))}
            </select>
            {/* 档位说明只讲模型定位，不讲价格——价格在 model_pricing 表里，
                写死在前端的"更便宜"等上游调价就变成谎话 */}
            <span className="text-xs leading-5 text-fg-subtle">
              {item.models.find((m) => m.model_id === preference[item.capability])?.note ??
                `由 ${item.provider_label} 提供，共 ${item.models.length} 档`}
            </span>
          </label>
        ) : (
          <div key={item.capability} className="flex flex-col gap-1">
            <span className="text-xs font-medium text-fg-muted">{item.label}</span>
            {/* 不给下拉框。选了不生效的控件比没有控件更糟。 */}
            <div className="flex h-8 items-center rounded-md border border-dashed border-border-strong bg-surface-2 px-2.5 text-sm text-fg-subtle">
              暂未接入
            </div>
            <span className="text-xs leading-5 text-fg-subtle">{item.unavailable_reason}</span>
          </div>
        ),
      )}

      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
