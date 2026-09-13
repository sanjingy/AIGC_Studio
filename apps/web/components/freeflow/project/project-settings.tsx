"use client";

import { useEffect, useState } from "react";
import { Loader2, Lock } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  ApiRequestError,
  modelCatalog,
  projects,
  type CapabilityModels,
  type LockVariables,
  type LockVariablesPatch,
  type Project,
} from "@/lib/api";
import { useLockVariables } from "@/lib/freeflow/use-lock-variables";
import { cn } from "@/lib/utils";

import { ConfirmDialog } from "./feedback";
import { LegacyNotice } from "./plan-gate";

/** 锁定变量里能在这一页改的五项。情节目录是产出，不在这张表上。 */
type LockField = "era" | "region" | "ethnicity" | "style_key" | "adaptation_mode";

const LOCK_FIELDS: LockField[] = ["era", "region", "ethnicity", "style_key", "adaptation_mode"];

/** 与门① 上的说法保持一致：两处不同的叫法比没有叫法更糟。 */
const ADAPTATION_LABEL: Record<string, string> = { adapt: "改编", rewrite: "洗稿" };

/**
 * 06 项目设置（需求文档「屏幕 07 分区表」）。
 *
 * 这一页的关键不是把设计稿的表单画像，而是**别画出后端没有的东西**：
 * `projects` 表只有 title / route_type / status / budget_cap_credits /
 * spent_credits / model_preference / stale_roles，没有描述、分辨率、帧率
 * 这些列。所以：
 *
 * - 项目名称：可改，走 `PATCH /projects/{id}`
 * - 项目类型：只读展示真值（route_type），由 Router Agent 判定，用户选不了
 * - 锁定变量：门① 定的画风 / 时代背景 / 改编模式，走
 *   `GET|PUT /projects/{id}/lock-variables`（ADR-037）
 * - 删除项目：`DELETE /projects/{id}`（软删，见 `lib/api.ts` 的 `projects.remove`）
 *
 * **「默认模型」这次从示例值变成真的**（ADR-024 接线）。它原本和分辨率、
 * 帧率一起躺在一张示例值表里写着 `Seedance 1.0`——那个值本身就是编的，
 * 后端目录里从来没有过这个模型（那张表已随旧壳一起删掉）。现在选项来自
 * `/model-catalog`，选中项来自
 * `projects.model_preference`，切换调 `PATCH /projects/{id}/model-preference`。
 *
 * 两处克制：
 * - **只按能力给下拉，不编"经济/标准/高质"三档。** ADR-024 说面向用户的
 *   应该是档位而不是模型 id，但"档位→模型"那张映射表还没建。每个能力现在
 *   只有两个真实模型，硬凑出一个不存在的"标准档"是往界面上加假东西。
 * - **没接入的能力（视频、语音）不给下拉框**，照实说未接入，原因由后端给。
 */

/*
 * 这一页只剩后端真的有的东西：
 *
 * - **成员管理 / 权限设置**：删掉。决策记录 §1 明确「团队成员」不在 M2 内，
 *   一个永远显示"即将支持"的 tab 就是假入口。
 * - **高级设置**：同上，那几项都要给 `projects` 加列才可能存在。
 * - **分辨率 / 帧率 / 存储位置**：删掉。`projects` 表没有这些列，
 *   摆成禁用的"示例值"仍然是在告诉用户这个系统有这些设置。
 * - **描述**：删掉。没有 description 列，之前那个 textarea 是纯本地 state。
 * - **项目名称**：从只读改成**真的能改**——`PATCH /projects/{id}` 一直都在，
 *   只是 `lib/api.ts` 没把它封出来（和 DELETE 当初一样）。
 */

/** 跟随后端默认路由。用空串而不是 undefined：`<select>` 的 value 必须是字符串。 */
const FOLLOW_DEFAULT = "";

export function ProjectSettings({
  projectId,
  project,
  onDelete,
}: {
  /** 路由参数里的 id。不从 `project` 上取——它是异步来的，加载中还是 null。 */
  projectId: string;
  project: Project | null;
  /** 真的会删——由页面层传下来，这里只管确认交互和错误展示。 */
  onDelete: () => Promise<void>;
}) {
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

      <RenameCard project={project} />

      <LockVariablesCard projectId={projectId} />

      <div className="grid gap-3 rounded-lg border border-border bg-surface p-4 sm:grid-cols-2">
        <ReadOnlyField
          label="项目类型"
          value={project?.route_type ?? "未判定"}
          hint="真实值，来自 projects.route_type（由 Router Agent 判定，不由用户选）。"
        />
        <ReadOnlyField
          label="记录状态"
          value={project?.status ?? "—"}
          hint="真实值，来自 projects.status。注意编排器目前不推进这一列（决策记录 §11.5 裁决 1），生产阶段以概览页显示的为准。"
        />
      </div>

      <ModelPreferenceCard project={project} />

      <div className="flex items-center justify-between gap-3 rounded-lg border border-danger/35 p-4">
        <div className="min-w-0">
          <div className="text-sm font-medium text-fg">删除项目</div>
          <div className="mt-0.5 text-xs text-fg-subtle">
            软删除：从列表中移除，产出数据不会立刻物理清除（跟 Skill 删除是同一套模式）
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

/**
 * 改项目名。`PATCH /projects/{id}` 一直都在，只是之前没封装出来，
 * 这一页就一直写着"重命名接口未接入"。
 *
 * 保存后**不做乐观更新**：拿后端返回的那一版覆盖本地输入框，
 * 服务端 trim 过的标题才是真值。
 */
function RenameCard({ project }: { project: Project | null }) {
  const [title, setTitle] = useState(project?.title ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 项目是异步来的：第一次拿到时把输入框填上，之后不再覆盖用户正在打的字
  useEffect(() => {
    if (project) setTitle((current) => (current === "" ? project.title : current));
  }, [project]);

  const dirty = project !== null && title.trim() !== project.title && title.trim() !== "";

  async function save() {
    if (!project || !dirty) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await projects.update(project.id, { title: title.trim() });
      setTitle(updated.title);
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.error.user_message : "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-4">
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-fg">项目名称</span>
        <input
          value={title}
          maxLength={200}
          disabled={!project || saving}
          onChange={(e) => {
            setTitle(e.target.value);
            setSaved(false);
          }}
          className="h-8 rounded-md border border-border-strong bg-bg px-2.5 text-sm text-fg"
        />
      </label>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="primary" disabled={!dirty || saving} onClick={() => void save()}>
          {saving ? "保存中…" : "保存名称"}
        </Button>
        {saved && !dirty && <span className="text-xs text-success">已保存</span>}
      </div>
      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}


// ---------------------------------------------------------------- 锁定变量（ADR-037）

/**
 * 门① 定下来的三件事，在这一页可以随时看、也能改。
 *
 * **为什么设置页也要有它**，而不是只放在门① 上：门① 只在项目走到那个位置时
 * 打开一次。之后用户想知道「这个项目锁的到底是哪个画风」，或者要改时代背景
 * （它只影响还没跑的阶段，改了是有效的），就再也没有入口了。更要紧的是
 * `legacy_unconfirmed` 那句「历史项目，未经确认」——迁移补出来的项目**已经
 * 越过门① 的位置**，那道门再也不会为它们打开，不在这里显示就等于永远不显示。
 *
 * 画风是例外：一旦风格档案建出来就冻结了，后端会 409 拒绝。这里**不预先禁用**
 * 那个选择框——前端读不到「风格档案建了没有」（`consistency_style_profiles`
 * 没有读接口），猜一个禁用条件出来，猜错时挡住的是合法操作。让它发出去，
 * 把后端那句「改画风需要重出全部已生成的画面」原样显示给用户。
 */
function LockVariablesCard({ projectId }: { projectId: string }) {
  const lock = useLockVariables(projectId);
  const [edits, setEdits] = useState<Partial<Record<LockField, string>>>({});

  const saved = lock.data;
  const valueOf = (field: LockField) => edits[field] ?? saved?.[field] ?? "";
  const set = (field: LockField, value: string) =>
    setEdits((prev) => ({ ...prev, [field]: value }));

  const patch: LockVariablesPatch = {};
  for (const field of LOCK_FIELDS) {
    const next = edits[field];
    if (next !== undefined && next !== (saved?.[field] ?? "")) patch[field] = next;
  }
  const dirty = Object.keys(patch).length > 0;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-medium text-fg">锁定变量</h2>
          <p className="mt-0.5 text-xs leading-5 text-fg-subtle">
            「开拍前确认」那道门定下的三件事。改动只影响还没跑的阶段，已经生成的内容不会变。
          </p>
        </div>
        <OriginBadge lock={saved} />
      </div>

      {lock.loading && <div className="rf-skeleton h-24 rounded-md" />}

      {lock.error && (
        <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {lock.error}
        </p>
      )}

      {saved?.legacy_unconfirmed && <LegacyNotice />}

      {saved && !lock.loading && (
        <>
          <div className="grid gap-2.5 sm:grid-cols-3">
            <TextRow
              label="时代背景"
              value={valueOf("era")}
              disabled={lock.saving}
              onChange={(v) => set("era", v)}
            />
            <TextRow
              label="国别 / 地区"
              value={valueOf("region")}
              disabled={lock.saving}
              onChange={(v) => set("region", v)}
            />
            <TextRow
              label="人种"
              value={valueOf("ethnicity")}
              disabled={lock.saving}
              onChange={(v) => set("ethnicity", v)}
            />
          </div>
          {saved.era_evidence && (
            <p className="text-xs leading-5 text-fg-subtle">
              判定依据（原文证据）：{saved.era_evidence}
            </p>
          )}

          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-fg">画风</span>
            <select
              value={valueOf("style_key")}
              disabled={lock.saving}
              onChange={(e) => set("style_key", e.target.value)}
              className="h-8 rounded-md border border-border-strong bg-bg px-2 text-sm text-fg"
            >
              <option value="">未选（按目录缺省）</option>
              {saved.style_options.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.name}
                </option>
              ))}
            </select>
            <span className="text-xs leading-5 text-fg-subtle">
              第一张图生成之后画风就冻结了；那之后再改要重出全部已生成的画面，后端会拒绝。
            </span>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-fg">改编模式</span>
            <select
              value={valueOf("adaptation_mode")}
              disabled={lock.saving}
              onChange={(e) => set("adaptation_mode", e.target.value)}
              className="h-8 rounded-md border border-border-strong bg-bg px-2 text-sm text-fg"
            >
              {saved.adaptation_options.map((mode) => (
                <option key={mode} value={mode}>
                  {ADAPTATION_LABEL[mode] ?? mode}
                </option>
              ))}
            </select>
            <span className="text-xs leading-5 text-fg-subtle">
              只影响还没跑的剧本阶段。已经生成的剧本不会因为改这里而重写。
            </span>
          </label>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={!dirty || lock.saving}
              onClick={() =>
                void lock.save(patch).then((ok) => {
                  if (ok) setEdits({});
                })
              }
            >
              {lock.saving && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
              保存锁定变量
            </Button>
            <span className="text-xs text-fg-subtle">
              {saved.confirmed_at
                ? `门① 确认于 ${new Date(saved.confirmed_at).toLocaleString("zh-CN")}`
                : "门① 还没有确认过"}
              {saved.anchors_confirmed_at
                ? ` · 门③ 确认于 ${new Date(saved.anchors_confirmed_at).toLocaleString("zh-CN")}`
                : ""}
            </span>
          </div>

          {lock.saveError && (
            <p
              role="alert"
              className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs leading-5 text-danger"
            >
              {lock.saveError}
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** 这三项是怎么来的。`migrated` 那条另有一整段提示，见 `LegacyNotice`。 */
function OriginBadge({ lock }: { lock: LockVariables | null }) {
  if (!lock) return null;
  const copy: Record<string, { label: string; tone: string }> = {
    detected: { label: "系统判定", tone: "bg-surface-2 text-fg-muted" },
    confirmed: { label: "你确认过", tone: "bg-success-soft text-success" },
    migrated: { label: "迁移补的", tone: "bg-rf-agent-soft text-rf-agent" },
  };
  const row = copy[lock.origin] ?? copy.detected!;
  return (
    <span className={cn("shrink-0 rounded-full px-2 py-1 text-[10px] font-semibold", row.tone)}>
      {row.label}
    </span>
  );
}

function TextRow({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="text-xs font-medium text-fg">{label}</span>
      <input
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 rounded-md border border-border-strong bg-bg px-2.5 text-sm text-fg"
      />
    </label>
  );
}

/** 只读字段。`mock` 那条分支已经删了——这一页不再有示例值。 */
function ReadOnlyField({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="flex items-center gap-1 text-xs font-medium text-fg">
        {label}
        <Lock aria-hidden className="size-3 text-fg-subtle" />
        <span className="sr-only">（只读）</span>
      </span>
      <div className="flex h-8 items-center rounded-md border border-border bg-surface-2 px-2.5 text-sm text-fg-muted">
        {value}
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
