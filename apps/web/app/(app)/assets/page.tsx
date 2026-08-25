"use client";

import { useCallback, useEffect, useState } from "react";
import { FileVideo, Image as ImageIcon, Music, FileText, Plus, Trash2 } from "lucide-react";

import { CharactersView, charactersMeta } from "@/components/project/characters-view";
import { ScenesView, scenesMeta } from "@/components/project/scenes-view";
import { Button } from "@/components/ui/button";
import { Disclosure } from "@/components/ui/disclosure";
import { Panel, PanelHeader } from "@/components/ui/panel";
import {
  ApiRequestError,
  assets as assetsApi,
  type AssetFolder,
  type CharacterEntry,
  type FolderItemType,
  type Library,
  type LibraryAsset,
  type ProfileEntry,
} from "@/lib/api";
import { cn, formatBytes } from "@/lib/utils";
import { PageScroll } from "@/components/shell/page-scroll";

/** 用量到这个百分比就开始视觉预警。留一成余量让用户来得及清理。 */
const WARN_AT = 90;

/** 与后端 MAX_REFERENCE_CHARS 一致。超了后端会拒，前端提前拦下来省一次往返。 */
const MAX_REFERENCE_CHARS = 6000;

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiRequestError ? e.error.user_message : fallback;
}

export default function AssetsPage() {
  const [data, setData] = useState<Library | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** null = "全部"视图，保持加文件夹之前的样子 */
  const [folderId, setFolderId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setData(await assetsApi.library({ folderId: folderId ?? undefined }));
    } catch (e) {
      setError(errorText(e, "加载资产库失败"));
    }
  }, [folderId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (error) {
    return (
      <p role="alert" className="m-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
        {error}
      </p>
    );
  }

  if (!data) return <p className="p-4 text-sm text-fg-muted">加载中…</p>;

  const folders = data.folders;

  return (
    <PageScroll>
      <div className="mx-auto flex max-w-[1000px] flex-col gap-4">
        <UsageBar usage={data.usage} />
        <FolderBar
          folders={folders}
          selected={folderId}
          onSelect={setFolderId}
          onChanged={reload}
        />
        <CreateCharacterPanel folders={folders} folderId={folderId} onCreated={reload} />
        <AssetGrid items={data.assets} folders={folders} onChanged={reload} />
        <CharacterSection items={data.characters} folders={folders} onChanged={reload} />
        <ProfileSection
          profiles={data.profiles}
          folders={folders}
          grouped={folderId === null}
          onChanged={reload}
        />
      </div>
    </PageScroll>
  );
}

// ---------------------------------------------------------------- 用量

function UsageBar({ usage }: { usage: Library["usage"] }) {
  const { used_bytes, quota_bytes, percent_used } = usage;
  const nearFull = quota_bytes !== null && percent_used >= WARN_AT;

  return (
    <Panel>
      <PanelHeader
        title="资产库容量"
        meta={
          quota_bytes === null
            ? `${formatBytes(used_bytes)} · 未设上限`
            : `${formatBytes(used_bytes)} / ${formatBytes(quota_bytes)} · ${percent_used}%`
        }
      />
      <div className="px-3 py-3">
        <div className="h-2 overflow-hidden rounded-full bg-surface-2">
          <div
            role="progressbar"
            aria-valuenow={percent_used}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="资产库已用容量"
            className={cn(
              "h-full transition-[width] duration-300",
              nearFull ? "bg-danger" : "bg-primary",
            )}
            style={{ width: `${Math.max(percent_used, quota_bytes && used_bytes ? 2 : 0)}%` }}
          />
        </div>
        {nearFull && (
          <p
            role="status"
            className="mt-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger"
          >
            容量快满了。满了之后新的上传和生成都会被拒绝——删掉不用的素材可以立刻腾出空间。
          </p>
        )}
        {/* 角色/场景档案是几 KB 的结构化文本，计进容量既没意义，
            也会让"清理素材腾空间"变得莫名其妙 */}
        <p className="mt-2 text-xs text-fg-subtle">
          只有图片、视频这类文件占用容量；角色档案与场景档案不计入。
        </p>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- 文件夹

/**
 * 文件夹切换条。做成横向的标签而不是侧边栏：这个页面本来就是一列
 * 从上往下的 Panel，左边还有全局导航栏，再插一根竖栏会把内容挤到很窄。
 */
function FolderBar({
  folders,
  selected,
  onSelect,
  onChanged,
}: {
  folders: AssetFolder[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  onChanged: () => Promise<void>;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const current = folders.find((f) => f.id === selected) ?? null;

  async function submit() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await assetsApi.createFolder(name.trim());
      setName("");
      setAdding(false);
      await onChanged();
    } catch (e) {
      setError(errorText(e, "建文件夹失败"));
    } finally {
      setBusy(false);
    }
  }

  async function rename() {
    if (!current) return;
    const next = window.prompt("新的文件夹名", current.name);
    if (!next || next === current.name) return;
    try {
      await assetsApi.renameFolder(current.id, next);
      await onChanged();
    } catch (e) {
      setError(errorText(e, "改名失败"));
    }
  }

  async function remove() {
    if (!current) return;
    // 删文件夹不删里面的东西，这一点必须在确认框里说清楚，
    // 否则用户会以为自己刚刚删掉了一批素材。
    if (!window.confirm(`删除文件夹「${current.name}」？里面的资产不会被删除，只会回到未分类。`))
      return;
    try {
      await assetsApi.deleteFolder(current.id);
      onSelect(null);
      await onChanged();
    } catch (e) {
      setError(errorText(e, "删除失败"));
    }
  }

  return (
    <Panel>
      <div className="flex flex-wrap items-center gap-1.5 px-3 py-2">
        <FolderChip label="全部" active={selected === null} onClick={() => onSelect(null)} />
        {folders.map((f) => (
          <FolderChip
            key={f.id}
            label={f.name}
            count={f.item_count}
            active={selected === f.id}
            onClick={() => onSelect(f.id)}
          />
        ))}

        {adding ? (
          <span className="inline-flex items-center gap-1.5">
            <input
              autoFocus
              value={name}
              maxLength={60}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submit();
                if (e.key === "Escape") setAdding(false);
              }}
              aria-label="新文件夹名"
              placeholder="文件夹名"
              className="h-7 w-36 rounded-md border border-border-strong bg-surface px-2 text-xs text-fg"
            />
            <Button size="sm" variant="primary" onClick={() => void submit()} disabled={busy}>
              建好
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>
              取消
            </Button>
          </span>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
            <Plus aria-hidden className="size-3.5" />
            新建文件夹
          </Button>
        )}

        {current && (
          <span className="ml-auto inline-flex items-center gap-1.5">
            <Button size="sm" variant="ghost" onClick={() => void rename()}>
              改名
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void remove()}>
              <Trash2 aria-hidden className="size-3.5" />
              删除文件夹
            </Button>
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="px-3 pb-2 text-xs text-danger">
          {error}
        </p>
      )}
    </Panel>
  );
}

function FolderChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count?: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 text-xs",
        "transition-colors duration-150",
        active
          ? "border-transparent bg-primary text-primary-fg"
          : "border-border-strong bg-surface text-fg hover:bg-surface-2",
      )}
    >
      <span className="max-w-[12rem] truncate">{label}</span>
      {count !== undefined && <span className="tnum opacity-70">{count}</span>}
    </button>
  );
}

/**
 * 归类下拉。列表里每条都带一个，比"选中再点移动"少一次交互，
 * 也不需要为拖拽写一套键盘可达的替代路径。
 */
function FolderPicker({
  folders,
  itemType,
  itemId,
  current,
  onChanged,
}: {
  folders: AssetFolder[];
  itemType: FolderItemType;
  itemId: string;
  current: string | null;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  if (folders.length === 0) return null;

  return (
    <select
      aria-label="归到文件夹"
      disabled={busy}
      value={current ?? ""}
      onChange={async (e) => {
        setBusy(true);
        try {
          await assetsApi.classify(itemType, itemId, e.target.value || null);
          await onChanged();
        } finally {
          setBusy(false);
        }
      }}
      className="h-6 max-w-[9rem] cursor-pointer rounded border border-border bg-surface px-1 text-xs text-fg-muted"
    >
      <option value="">未分类</option>
      {folders.map((f) => (
        <option key={f.id} value={f.id}>
          {f.name}
        </option>
      ))}
    </select>
  );
}

// ---------------------------------------------------------------- 从描述创建角色

function CreateCharacterPanel({
  folders,
  folderId,
  onCreated,
}: {
  folders: AssetFolder[];
  folderId: string | null;
  onCreated: () => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function submit() {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      // 当前正看着某个文件夹时，新档案直接归到那里——用户的意图很明确
      const r = await assetsApi.createCharacter(text.trim(), folderId);
      setText("");
      setNote(`已生成「${r.entry.title}」，本次扣 ${r.cost_credits} Credits。`);
      await onCreated();
    } catch (e) {
      setError(errorText(e, "生成失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel>
      <PanelHeader
        title="从描述创建角色"
        meta="不需要先建项目"
        action={
          <Button
            size="sm"
            variant="primary"
            onClick={() => void submit()}
            disabled={busy || !text.trim()}
          >
            {busy ? "生成中…" : "生成角色档案"}
          </Button>
        }
      />
      <div className="flex flex-col gap-2 px-3 py-3">
        <label htmlFor="character-reference" className="text-xs text-fg-muted">
          写一段角色设定：外貌、性格、处境都可以。写得越具体，出来的档案越能直接拿去出图。
        </label>
        <textarea
          id="character-reference"
          value={text}
          maxLength={MAX_REFERENCE_CHARS}
          rows={4}
          onChange={(e) => setText(e.target.value)}
          placeholder="例：三十出头的女法医，常年值夜班，说话很短。左眉有一道旧疤，习惯把头发全部束起来，穿洗得发白的深蓝工装。"
          className="w-full resize-y rounded-md border border-border-strong bg-surface px-2.5 py-2 text-sm text-fg placeholder:text-fg-subtle"
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          {/* 这是一次真实的模型调用，会扣 Credits。不写清楚的话，
              用户会把它当成免费的输入框反复点。 */}
          <p className="text-xs text-fg-subtle">
            会调用一次模型并扣除 Credits。
            {folderId && folders.some((f) => f.id === folderId) && (
              <> 生成后归到「{folders.find((f) => f.id === folderId)?.name}」。</>
            )}
          </p>
          <span className="tnum text-xs text-fg-subtle">
            {text.length} / {MAX_REFERENCE_CHARS}
          </span>
        </div>
        {error && (
          <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
            {error}
          </p>
        )}
        {note && (
          <p role="status" className="text-xs text-success">
            {note}
          </p>
        )}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- 文件资产

const TYPE_ICON = {
  image: ImageIcon,
  video: FileVideo,
  audio: Music,
} as const;

function AssetGrid({
  items,
  folders,
  onChanged,
}: {
  items: LibraryAsset[];
  folders: AssetFolder[];
  onChanged: () => Promise<void>;
}) {
  return (
    <Panel>
      <PanelHeader title="图片与视频" meta={`${items.length} 个文件`} />
      {items.length === 0 ? (
        <p className="px-3 py-8 text-center text-sm text-fg-subtle">
          还没有生成或上传过文件。出图链路接上之后，成片素材会自动出现在这里。
        </p>
      ) : (
        <ul className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3 lg:grid-cols-4">
          {items.map((a) => (
            <AssetCard key={a.id} asset={a} folders={folders} onChanged={onChanged} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function AssetCard({
  asset,
  folders,
  onChanged,
}: {
  asset: LibraryAsset;
  folders: AssetFolder[];
  onChanged: () => Promise<void>;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const Icon = TYPE_ICON[asset.type as keyof typeof TYPE_ICON] ?? FileText;

  // 缩略图就是原图：真正的缩略图生成要走一条独立的转码链路，
  // 现在没有，做个假的占位图更没意义。链接是预签名的，有有效期，
  // 所以只在渲染时现签，不缓存进列表接口。
  useEffect(() => {
    if (asset.type !== "image") return;
    let alive = true;
    assetsApi
      .downloadUrl(asset.id)
      .then((r) => alive && setUrl(r.url))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [asset.id, asset.type]);

  return (
    <li className="overflow-hidden rounded-md border border-border bg-surface-2">
      <div className="flex aspect-square items-center justify-center bg-surface-3">
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element -- 预签名 URL 是运行时才知道的外部地址，用不了 next/image 的构建期优化
          <img src={url} alt={asset.filename} className="size-full object-cover" />
        ) : (
          <Icon aria-hidden className="size-8 text-fg-subtle" />
        )}
      </div>
      <div className="px-2 py-1.5">
        <p className="truncate text-xs text-fg" title={asset.filename}>
          {asset.filename}
        </p>
        <p className="tnum mt-0.5 text-xs text-fg-subtle">
          {asset.size_bytes === null ? "—" : formatBytes(asset.size_bytes)}
          {" · "}
          {new Date(asset.created_at).toLocaleDateString("zh-CN")}
        </p>
        <div className="mt-1">
          <FolderPicker
            folders={folders}
            itemType="asset"
            itemId={asset.id}
            current={asset.folder_id}
            onChanged={onChanged}
          />
        </div>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------- 独立角色档案

function CharacterSection({
  items,
  folders,
  onChanged,
}: {
  items: CharacterEntry[];
  folders: AssetFolder[];
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState<string | null>(null);

  if (items.length === 0) return null;

  return (
    <section className="flex flex-col gap-2">
      <h2 className="px-1 text-sm font-semibold text-fg">
        独立角色档案
        <span className="tnum ml-2 text-xs font-normal text-fg-subtle">
          {items.length} 份 · 不挂项目
        </span>
      </h2>

      {items.map((entry) => (
        <Disclosure
          key={entry.id}
          title={entry.title}
          meta={charactersMeta(entry.output)}
          open={open === entry.id}
          onToggle={() => setOpen(open === entry.id ? null : entry.id)}
          action={
            <span className="flex items-center gap-1.5">
              <FolderPicker
                folders={folders}
                itemType="character"
                itemId={entry.id}
                current={entry.folder_id}
                onChanged={onChanged}
              />
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  if (!window.confirm(`删除角色档案「${entry.title}」？`)) return;
                  await assetsApi.deleteCharacter(entry.id);
                  await onChanged();
                }}
              >
                <Trash2 aria-hidden className="size-3.5" />
                删除
              </Button>
            </span>
          }
        >
          {/* 与项目详情页共用同一个组件：两处渲染的是同一个 schema 的产出，
              抄一遍必然会分叉 */}
          <CharactersView data={entry.output} />
          <div className="border-t border-border px-3 py-2">
            <p className="text-xs text-fg-subtle">
              当初写的描述：{entry.source_text.slice(0, 120)}
              {entry.source_text.length > 120 && "…"}
            </p>
            {/* 诚实标注：能看见、能归类，但还不能被项目的生产流程消费。
                照 ADR-026 对"选了但没接运行时"的处理方式办——
                做一个看起来能用的按钮比没有按钮更糟。 */}
            <p className="mt-1 text-xs text-fg-subtle">
              导入到项目还没接上生产流程（要先设计外部角色档案怎么进
              current_state_json），本轮只做到"存得下、看得见、归得了类"。
            </p>
          </div>
        </Disclosure>
      ))}
    </section>
  );
}

// ---------------------------------------------------------------- 项目里的角色 / 场景档案

function ProfileSection({
  profiles,
  folders,
  grouped,
  onChanged,
}: {
  profiles: ProfileEntry[];
  folders: AssetFolder[];
  grouped: boolean;
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState<string | null>(profiles[0]?.run_id ?? null);

  if (profiles.length === 0) {
    return (
      <Panel>
        <PanelHeader title="角色与场景档案" meta="0 份" />
        <p className="px-3 py-8 text-center text-sm text-fg-subtle">
          {grouped
            ? "还没有角色或场景档案。项目推进到“确认剧本”之后就会生成。"
            : "这个文件夹里还没有角色或场景档案。"}
        </p>
      </Panel>
    );
  }

  // 按项目分组：同名角色在两个项目里是两份独立设定，合并需要一致性引擎
  // 参与，不是列表页该做的判断。文件夹视图不分组——那里的组织方式
  // 就是用户自己定的分类。
  const byProject = new Map<string, ProfileEntry[]>();
  for (const p of profiles) {
    byProject.set(p.project_id, [...(byProject.get(p.project_id) ?? []), p]);
  }

  const entry = (p: ProfileEntry) => (
    <Disclosure
      key={p.run_id}
      title={p.kind === "characters" ? "角色档案" : "场景档案"}
      meta={p.kind === "characters" ? charactersMeta(p.output) : scenesMeta(p.output)}
      open={open === p.run_id}
      onToggle={() => setOpen(open === p.run_id ? null : p.run_id)}
      action={
        <FolderPicker
          folders={folders}
          itemType="profile"
          itemId={p.run_id}
          current={p.folder_id}
          onChanged={onChanged}
        />
      }
    >
      {/* 展示逻辑复用项目详情页的同一组件：两处渲染同一份产出，
          抄一遍必然会分叉 */}
      {p.kind === "characters" ? <CharactersView data={p.output} /> : <ScenesView data={p.output} />}
    </Disclosure>
  );

  return (
    <section className="flex flex-col gap-3">
      <h2 className="px-1 text-sm font-semibold text-fg">
        角色与场景档案
        <span className="tnum ml-2 text-xs font-normal text-fg-subtle">
          {profiles.length} 份 · {byProject.size} 个项目
        </span>
      </h2>

      {grouped
        ? [...byProject.entries()].map(([projectId, entries]) => (
            <div key={projectId} className="flex flex-col gap-2">
              <p className="px-1 text-xs text-fg-subtle">
                {entries[0]?.project_title || projectId}
              </p>
              {entries.map(entry)}
            </div>
          ))
        : profiles.map(entry)}
    </section>
  );
}
