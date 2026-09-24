"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  FileText,
  Puzzle,
  Loader2,
  Upload,
  type LucideIcon,
} from "lucide-react";

import {
  AssetLibraryIcon,
  CharacterIcon,
  RenderImageIcon,
  SceneIcon,
  VideoIcon,
  VoiceIcon,
} from "@/components/icons/studio-icons";
import { charactersMeta } from "@/components/project/characters-view";
import { scenesMeta } from "@/components/project/scenes-view";
import { SKILLS_CHANGED, useSkillUpload } from "@/components/project/skill-upload";
import { Button } from "@/components/ui/button";
import {
  ApiRequestError,
  assets as assetsApi,
  orgSkills,
  UPLOAD_ACCEPT,
  type CharacterEntry,
  type Library,
  type LibraryAsset,
  type OrgSkill,
  type ProfileEntry,
} from "@/lib/api";
import { ASSET_KIND_LABEL, type AssetKind } from "@/lib/freeflow/types";
import { cn, formatBytes } from "@/lib/utils";

/**
 * 03 素材库（需求文档「屏幕 03」、REQ-030/031）。
 *
 * 文件、角色档案、场景档案都走 `assets.library()`——和
 * `app/(app)/assets/page.tsx` 是同一个接口、同一批类型（`LibraryAsset` /
 * `CharacterEntry` / `ProfileEntry`），这一屏只是换了一种组织方式
 * （一级 chip + 卡片网格），没有另造数据结构。
 *
 * 只有 Skill 是例外：它在 `/skills`（`orgSkills.list()`），不在资产库接口
 * 里，所以切到那个 chip 时单独发一次请求。
 *
 * 与主线那一页的分工：那页管文件夹归类、从描述建角色、档案展开细读；
 * 这页管「按类型快速找一个素材」。所以这里不重复实现文件夹管理。
 */

/** 用量到这个百分比就开始视觉预警。与 `app/(app)/assets/page.tsx` 同值。 */
const WARN_AT = 90;

/**
 * 一级 chip → `assets.library({type})` 的真实类型参数。
 *
 * `undefined` 表示不带 type 参数，取全量（`all` 以及所有结构化类型都走这条）。
 *
 * 注意一个已知缺口：后端 `apps/api/modules/asset/mime.py` 把
 * `text/plain` / `text/markdown` 映射成资产类型 `text`，而 `AssetKind`
 * （在冻结的 `lib/freeflow/types.ts` 里）没有对应的 chip，所以上传的
 * 小说 txt 目前只在「全部」里出现，「文档」筛不到它。要修得先给
 * AssetKind 加一项，那是公共契约，本轮不动。
 */
const SERVER_TYPE: Record<AssetKind, string | undefined> = {
  all: undefined,
  image: "image",
  video: "video",
  audio: "audio",
  document: "document",
  // 下面这些不是 `assets` 表里的文件，`type` 参数对它们没有意义：
  // 角色/场景是 agent_runs 的结构化产出，Skill 在 /skills，
  // 分镜与 Workflow 后端还没有可列的索引。
  character: undefined,
  scene: undefined,
  storyboard: undefined,
  workflow: undefined,
  skill: undefined,
};

/**
 * 后端还没有跨类型索引的两类。筛出来就是空的——不拿别的东西凑数，
 * 凑了用户就会以为索引已经有了。
 *
 * 分镜的产出在 `agent_runs` 里，但资产库接口只透出角色/场景两类档案
 * （`library.PROFILE_AGENTS`），拿不到它；Workflow 后端根本没有这个实体。
 *
 * 「场景」「Skill」原本也在这张表里，现在都接上了真实数据：场景走
 * `assets.library()` 返回的 `profiles`（kind = "scenes"），Skill 走
 * `/skills`（`orgSkills.list()`，是另一个接口，切到这个 chip 时单独发）。
 */
const UNINDEXED: readonly AssetKind[] = ["storyboard", "workflow"];

/**
 * 空态文案。有真实索引却真的一条都没有，和"索引还没做"是两件事，
 * 说反了用户就会去等一个不会来的东西（或者反过来，以为功能坏了）。
 * 查不到的落回一句通用的。
 */
const EMPTY_TEXT: Partial<Record<AssetKind, string>> = {
  character: "还没有独立角色档案。在主线资产库页用一段描述就能生成一份。",
  scene: "还没有场景档案。项目跑到「场景档案」那一步之后会出现在这里。",
  // Skill 的空态不走这张表：它自带上传入口，见 SkillEmptyState。
};

const KIND_ORDER: readonly AssetKind[] = [
  "all",
  "image",
  "video",
  "audio",
  "character",
  "scene",
  "storyboard",
  "document",
  "workflow",
  "skill",
];

const TYPE_ICON: Record<string, LucideIcon> = {
  image: RenderImageIcon,
  video: VideoIcon,
  audio: VoiceIcon,
};

/**
 * 二级筛选（REQ-030 里的「项目、角色、场景、标签、时间、类型、模型」）。
 *
 * **只有外观和选中状态，不接真实筛选。** `assets.library()` 认
 * `type` / `folder_id` / `project_id`，时间、标签、模型后端一个都没有；
 * 选了之后列表不变是故意的，不是坏了。项目那一项的参数虽然有了，但这个
 * 下拉里还没有可选的项目——它要的是项目列表接口，不是这里再筛一遍。
 * 真要接得先把下拉填上（标签那一项还得先有标签表）。
 */
const SECONDARY_FILTERS = [
  { id: "time", label: "全部时间", options: ["全部时间", "最近 7 天", "最近 30 天"] },
  { id: "project", label: "全部项目", options: ["全部项目"] },
  { id: "tag", label: "全部标签", options: ["全部标签"] },
  { id: "model", label: "全部模型", options: ["全部模型"] },
] as const;

export function AssetLibraryGrid() {
  const [kind, setKind] = useState<AssetKind>("all");
  const [data, setData] = useState<Library | null>(null);
  const [skills, setSkills] = useState<OrgSkill[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  /** 上传成功后靠它重拉列表。计数器而不是布尔：连传两个也要各刷新一次。 */
  const [reloadKey, setReloadKey] = useState(0);

  /**
   * Skill 上传（ADR-026 / 决策记录 §11.5 裁决 8）。
   *
   * 入口原本长在旧壳对话框的「+」附件菜单里，那个菜单随旧壳一起删了，
   * 所以上传能力搬到这里——技能 chip 是列 Skill 的地方，也就是找它的人
   * 会去的地方。复用 `useSkillUpload` 这个既有 hook，不抄第二份上传实现：
   * 校验规则是安全边界（处理器白名单、导出路径白名单），全在后端，
   * 前端两份实现迟早分叉。
   */
  const skillUpload = useSkillUpload();

  useEffect(() => {
    let alive = true;
    setLoading(true);
    assetsApi
      .library({ type: SERVER_TYPE[kind] })
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError(null);
      })
      .catch((e) => {
        if (!alive) return;
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载资产库失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [kind, reloadKey]);

  // Skill 不在 `/assets/library` 里，是 `/skills` 这个独立接口。只在切到
  // 这个 chip 时发一次，之后留在内存里——每个 chip 都带上它等于给九个
  // 用不到它的 chip 各加一次请求。
  useEffect(() => {
    if (kind !== "skill" || skills !== null) return;
    let alive = true;
    orgSkills
      .list()
      .then((r) => alive && setSkills(r.items))
      .catch((e) => {
        if (!alive) return;
        setSkills([]);
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载 Skill 列表失败");
      });
    return () => {
      alive = false;
    };
  }, [kind, skills]);

  // 传完一份 Skill 之后重拉清单。`useSkillUpload` 成功时会广播
  // SKILLS_CHANGED，这里把缓存清空，上面那个 effect 就会重新拉一次
  // ——不在两处各写一遍"上传成功后做什么"。
  useEffect(() => {
    const onChanged = () => setSkills(null);
    window.addEventListener(SKILLS_CHANGED, onChanged);
    return () => window.removeEventListener(SKILLS_CHANGED, onChanged);
  }, []);

  // 文件：结构化类型的 chip 不展示文件；`all` 和四个文件类 chip 才展示。
  // 后者的过滤已经由后端的 type 参数做完了，前端不再筛一遍。
  const structural =
    UNINDEXED.includes(kind) || kind === "character" || kind === "scene" || kind === "skill";
  const files: LibraryAsset[] = data && !structural ? data.assets : [];

  // 角色：`characters` 是独立角色档案，后端已经有真实索引。
  const characters: CharacterEntry[] =
    data && (kind === "all" || kind === "character") ? data.characters : [];

  // 场景：`profiles` 里 kind = "scenes" 的那些，每个项目最新一版
  // （后端 `library._latest_per_project` 已经收敛过）。`all` 里不混进来
  // ——那一屏是文件网格，档案有自己的 chip。
  const scenes: ProfileEntry[] =
    data && kind === "scene" ? data.profiles.filter((p) => p.kind === "scenes") : [];

  const skillItems: OrgSkill[] = kind === "skill" ? (skills ?? []) : [];

  const total = files.length + characters.length + scenes.length + skillItems.length;

  /*
   * 分成两层：**有帧的进光桌，没帧的进档案列。**
   *
   * 上一版把六种东西全塞进同一张等宽网格，于是一份纯文本的角色档案和一张
   * 真实分镜图占同样大的方块——那是「什么都一样重要」，也正是通用素材管理
   * 后台的样子。现在图占它应得的宽度，档案只占一条。
   *
   * 视频和音频暂时也走档案列：这条链路上没有转码，拿不到封面帧，
   * 硬给一个空画框就是在承诺一个还不存在的缩略图。
   */
  const plates = files.filter((a) => a.type === "image");
  const otherFiles = files.filter((a) => a.type !== "image");
  const dockets = otherFiles.length + characters.length + scenes.length + skillItems.length;

  return (
    <div className="ff-page flex max-w-[1200px] flex-col">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h1 className="text-base font-semibold text-fg">我的素材</h1>
        {/* 技能 chip 下换成传 Skill：Skill 不进 `assets` 表，走的是 /skills
            这条完全不同的链路，摆一个「上传素材」在这里点下去不会多出一份
            Skill。两个上传按钮并排则要用户先分辨自己在传哪一种。 */}
        {kind === "skill" ? (
          <Button size="sm" disabled={skillUpload.busy} onClick={skillUpload.pick}>
            {skillUpload.busy ? (
              <Loader2 aria-hidden className="size-3.5 animate-spin" />
            ) : (
              <Upload aria-hidden className="size-3.5" />
            )}
            上传 Skill
          </Button>
        ) : (
          <UploadButton onUploaded={() => setReloadKey((n) => n + 1)} onError={setError} />
        )}
      </div>
      {skillUpload.input}

      {data && <UsageLine usage={data.usage} />}

      {/* 一级类型 chip：十项，单选 */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {KIND_ORDER.map((k) => (
          <button
            key={k}
            type="button"
            aria-pressed={kind === k}
            onClick={() => setKind(k)}
            className={cn(
              "inline-flex h-7 cursor-pointer items-center rounded-[2px] px-2.5 text-xs",
              "transition-colors duration-150",
              kind === k
                ? "bg-primary font-medium text-primary-fg"
                : "bg-surface-2 text-fg-muted hover:bg-surface-3 hover:text-fg",
            )}
          >
            {ASSET_KIND_LABEL[k]}
          </button>
        ))}
      </div>

      <SecondaryFilters />

      {error && (
        <p role="alert" className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {kind === "skill" && skillUpload.notice && <div className="mt-3">{skillUpload.notice}</div>}

      {(loading && !data) || (kind === "skill" && skills === null) ? (
        <AssetSkeleton />
      ) : UNINDEXED.includes(kind) ? (
        <EmptyState>
          该类型的统一索引还没有接入（REQ-030）。{ASSET_KIND_LABEL[kind]}
          目前是各处的结构化产出，不在 `assets` 这张文件表里，素材库要覆盖它
          得先有一张跨类型的资产索引。
        </EmptyState>
      ) : total === 0 ? (
        kind === "skill" ? (
          <SkillEmptyState onPick={skillUpload.pick} busy={skillUpload.busy} />
        ) : (
          <EmptyState>
            {EMPTY_TEXT[kind] ?? "这个类型下还没有素材。出图或上传之后会出现在这里。"}
          </EmptyState>
        )
      ) : (
        <>
          <p className="tnum mt-4 mb-2 text-xs text-fg-subtle">
            {[
              files.length > 0 && `${files.length} 个文件`,
              characters.length > 0 && `${characters.length} 份角色档案`,
              scenes.length > 0 && `${scenes.length} 份场景档案`,
              skillItems.length > 0 && `${skillItems.length} 份 Skill`,
            ]
              .filter(Boolean)
              .join("，")}
          </p>
          {kind === "skill" && skillItems.some((k) => !k.runtime_wired) && (
            // ADR-026 的验收标准：列 Skill 的地方必须照实说运行时没接线。
            // 判据取后端的 runtime_wired，不是前端写死一句话——真接上了
            // 这块自己就不显示了。
            <p className="mb-2 rounded-[2px] bg-surface-2 px-3 py-2 text-xs text-fg-subtle">
              这些 Skill 已存入技能库，但
              <span className="text-fg-muted">运行时尚未接线</span>
              ——当前生产流程仍走内置阶段图，它们暂时不会生效。
            </p>
          )}

          {/* 光桌：有画面的素材铺在这里，按各自的宽高比占位。
              横画幅真的比竖画幅宽，一行的高度对齐——这是 dailies 摊在
              灯箱上的样子，不是一张张等宽的卡片。 */}
          {plates.length > 0 && (
            <div className="ff-lighttable">
              {plates.map((a) => (
                <FilePlate key={a.id} asset={a} />
              ))}
            </div>
          )}

          {/* 档案不是画面：没有可看的帧，就不给它画面的体量。
              一列索引卡，左侧书脊的颜色标明是哪一类。 */}
          {dockets > 0 && (
            <div className={cn("ff-dockets", plates.length > 0 && "mt-4")}>
              {otherFiles.map((a) => (
                <FileDocket key={a.id} asset={a} />
              ))}
              {characters.map((c) => (
                <CharacterDocket key={c.id} entry={c} />
              ))}
              {scenes.map((p) => (
                <SceneDocket key={p.run_id} profile={p} />
              ))}
              {skillItems.map((k) => (
                <SkillDocket key={k.id} skill={k} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 容量

/**
 * 容量说明。规则照抄 `app/(app)/assets/page.tsx`（REQ-031 要求维持现实现）：
 * 只有图片/视频/音频这类文件占容量，角色、场景、分镜等结构化产出不占。
 * 百分比用后端算好的 `percent_used`，前端不重算——两处各算一次迟早对不上。
 */
function UsageLine({ usage }: { usage: Library["usage"] }) {
  const { used_bytes, quota_bytes, percent_used } = usage;
  const nearFull = quota_bytes !== null && percent_used >= WARN_AT;

  return (
    <div className="rounded-[2px] border border-border bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-fg-muted">
          只有图片、视频这类文件占用容量；角色档案与场景档案不计入。
        </span>
        <span className="tnum text-xs text-fg-subtle">
          {quota_bytes === null
            ? `${formatBytes(used_bytes)}，未设上限`
            : `${formatBytes(used_bytes)} / ${formatBytes(quota_bytes)}，已用 ${percent_used}%`}
        </span>
      </div>
      {quota_bytes !== null && (
        <div className="ff-meter mt-1.5" data-tone={nearFull ? "danger" : undefined}>
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
            style={{ width: `${Math.min(Math.max(percent_used, used_bytes ? 2 : 0), 100)}%` }}
          />
        </div>
      )}
      {nearFull && (
        <p role="status" className="mt-1.5 text-xs text-danger">
          容量快满了。满了之后新的上传和生成都会被拒绝——删掉不用的素材可以立刻腾出空间。
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 二级筛选

function SecondaryFilters() {
  // 选中值只存在本地：后端没有对应查询参数，选了不会改变列表（见上方注释）
  const [values, setValues] = useState<Record<string, string>>({});

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      {SECONDARY_FILTERS.map((f) => (
        <select
          key={f.id}
          aria-label={f.label}
          value={values[f.id] ?? f.options[0]}
          onChange={(e) => setValues((v) => ({ ...v, [f.id]: e.target.value }))}
          className="h-7 cursor-pointer rounded-md border border-border-strong bg-surface px-2 text-xs text-fg-muted"
        >
          {f.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      ))}
      <span className="text-xs text-fg-subtle">
        二级筛选还没接后端：这几个条件列表接口都不认，选了不会改变结果。
      </span>
    </div>
  );
}

// ---------------------------------------------------------------- 卡片

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rf-empty-state mt-6 rounded-[2px] border border-dashed border-border-strong px-4 py-10 text-center">
      <span className="rf-empty-icon"><AssetLibraryIcon aria-hidden className="size-6" /></span>
      <p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-fg-subtle">{children}</p>
    </div>
  );
}

function AssetSkeleton() {
  return (
    <div role="status" aria-label="加载中" className="ff-lighttable mt-6">
      <span className="sr-only">加载中…</span>
      {/* 骨架也按不同宽高比排，免得内容到位时整片版式跳一次 */}
      {[1.78, 0.75, 1.5, 1.78].map((ar, i) => (
        <span
          key={i}
          className="rf-skeleton block"
          style={{ ["--ar" as string]: String(ar), aspectRatio: String(ar) }}
        />
      ))}
    </div>
  );
}

/**
 * 技能 chip 的空态：一句说明 + 上传入口 + 「运行时未接线」。
 *
 * 三件事缺一不可——
 * 说明要写清 Skill 是一份 YAML（用户不知道该拖什么进去）；
 * 上传入口要在这里（决策记录 §11.5 裁决 8：旧壳的「+」菜单没了，
 * 不能留一句指向已删菜单的文案）；
 * 「运行时未接线」是 ADR-026 的验收标准，传上去只会被校验和存档，
 * 生产流程仍走后端硬编码的阶段图。不说清楚，传完的人会以为下一次
 * 跑生产就按他这份 Skill 走。
 */
function SkillEmptyState({ onPick, busy }: { onPick: () => void; busy: boolean }) {
  return (
    <div className="mt-6 rounded-md border border-dashed border-border-strong px-4 py-10 text-center">
      <Puzzle aria-hidden className="mx-auto size-6 text-fg-subtle" />
      <p className="mt-2 text-sm text-fg-subtle">
        技能库里还没有 Skill。Skill 是一份声明式的生产模板（`.yaml` / `.yml`），
        上传后由后端校验并存进本组织的技能库。
      </p>
      <Button className="mt-3" size="sm" disabled={busy} onClick={onPick}>
        {busy ? (
          <Loader2 aria-hidden className="size-3.5 animate-spin" />
        ) : (
          <Upload aria-hidden className="size-3.5" />
        )}
        上传 Skill
      </Button>
      <p className="mt-3 text-xs text-fg-subtle">
        校验不通过的也会存进来并显示错误原文；但
        <span className="text-fg-muted">运行时尚未接线</span>
        ——当前生产流程仍走内置阶段图，传上来的 Skill 暂时不会生效。
      </p>
    </div>
  );
}

/**
 * 光桌上的一格。**宽高比来自 `assets` 表的真实 width / height**，
 * 所以一张 1024×576 的分镜图真的比 1024×1024 的立绘宽——用户扫一眼
 * 就知道哪些是镜头、哪些是立绘，不必读文件名。
 *
 * 缩略图就是原图：真正的缩略图要走一条独立转码链路，现在没有。
 * 链接是预签名的、有有效期，所以只在渲染时现签，不缓存进列表。
 */
function FilePlate({ asset }: { asset: LibraryAsset }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    assetsApi
      .downloadUrl(asset.id)
      .then((r) => alive && setUrl(r.url))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [asset.id]);

  // 后端两列都可能是 null（没探到尺寸）。退回 16:9 而不是 1:1——
  // 这个产品里绝大多数图是镜头画面。
  const ratio =
    asset.width && asset.height ? Math.max(0.4, Math.min(3, asset.width / asset.height)) : 16 / 9;

  return (
    <figure
      className="ff-plate"
      data-empty={url ? undefined : "true"}
      style={{ ["--ar" as string]: String(ratio) }}
    >
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element -- 预签名 URL 是运行时才知道的外部地址，用不了 next/image 的构建期优化
        <img src={url} alt={asset.filename} loading="lazy" />
      ) : (
        <RenderImageIcon aria-hidden className="size-6" />
      )}
      <figcaption className="ff-plate-burn">
        <span title={asset.filename}>{asset.filename}</span>
        <span>
          {asset.width && asset.height ? `${asset.width}×${asset.height}` : ""}
          {asset.size_bytes === null ? "" : ` ${formatBytes(asset.size_bytes)}`}
        </span>
      </figcaption>
    </figure>
  );
}

/** 档案列里的一条：书脊颜色标类别，右边是标题和一行元信息。 */
function Docket({
  kind,
  icon: Icon,
  title,
  meta,
  date,
}: {
  kind: "file" | "character" | "scene" | "skill";
  icon: LucideIcon;
  title: string;
  meta: string;
  date: string;
}) {
  return (
    <article className="ff-docket" data-kind={kind}>
      <Icon aria-hidden className="ff-docket-icon size-4" />
      <div className="ff-docket-body">
        <p className="ff-docket-title" title={title}>
          {title}
        </p>
        <p className="ff-docket-meta">
          <span title={meta}>{meta}</span>
          <span className="code shrink-0">{new Date(date).toLocaleDateString("zh-CN")}</span>
        </p>
      </div>
    </article>
  );
}

/**
 * 没有可看帧的文件：文档、音频、视频。
 *
 * 视频也在这里——这条链路上没有转码，拿不到封面帧。给它一个空画框
 * 等于承诺一个还不存在的缩略图，不如照实说这是一个文件。
 */
function FileDocket({ asset }: { asset: LibraryAsset }) {
  return (
    <Docket
      kind="file"
      icon={TYPE_ICON[asset.type] ?? FileText}
      title={asset.filename}
      meta={asset.size_bytes === null ? "大小未知" : formatBytes(asset.size_bytes)}
      date={asset.created_at}
    />
  );
}

function CharacterDocket({ entry }: { entry: CharacterEntry }) {
  return (
    <Docket
      kind="character"
      icon={CharacterIcon}
      title={entry.title}
      meta={charactersMeta(entry.output)}
      date={entry.created_at}
    />
  );
}

function SceneDocket({ profile }: { profile: ProfileEntry }) {
  return (
    <Docket
      kind="scene"
      icon={SceneIcon}
      title={profile.project_title}
      meta={scenesMeta(profile.output)}
      date={profile.created_at}
    />
  );
}

function SkillDocket({ skill }: { skill: OrgSkill }) {
  const invalid = skill.status !== "valid";
  return (
    <Docket
      kind="skill"
      icon={Puzzle}
      title={skill.name}
      /* 校验不过的照样入库（ADR-026），列表里就得看得出来是哪一份。
         version 是 spec 里原样抄出来的字符串，作者写的一般就是 "v1"
         ——别再拼一个 v 上去；没写的后端给占位 "-"。 */
      meta={`${skill.version === "-" ? "版本未知" : skill.version}，${
        invalid ? "校验未通过" : `${skill.stage_count} 个阶段`
      }`}
      date={skill.created_at}
    />
  );
}

/**
 * 上传素材。走仓库现成的那条三段式直传（`assets.upload`）：
 * 建 pending 记录拿预签名地址 → 客户端 PUT 到对象存储 → complete 让服务端
 * HEAD 校验后置 ready。**不另写一套阈值**——MIME 白名单、大小上限、
 * 容量配额全长在那条链路上，前端再写一遍只会和 `s3_max_upload_bytes` 分叉。
 *
 * `accept` 用后端白名单的全集，只是给文件选择器过滤，不是第二套校验。
 * 多选时串行传：并发会同时打满配额检查那一行的锁，也让失败的那个说不清是哪一个。
 */
function UploadButton({
  onUploaded,
  onError,
}: {
  onUploaded: () => void;
  onError: (message: string | null) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<{ done: number; total: number } | null>(null);

  const pick = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const list = Array.from(files);
      onError(null);
      setBusy({ done: 0, total: list.length });
      try {
        let done = 0;
        for (const file of list) {
          await assetsApi.upload(file);
          done += 1;
          setBusy({ done, total: list.length });
        }
        onUploaded();
      } catch (e) {
        onError(e instanceof ApiRequestError ? e.error.user_message : "上传失败，请重试");
      } finally {
        setBusy(null);
        // 清空 value，否则再选同一个文件不会触发 change
        if (inputRef.current) inputRef.current.value = "";
      }
    },
    [onError, onUploaded],
  );

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={UPLOAD_ACCEPT}
        className="sr-only"
        onChange={(e) => void pick(e.target.files)}
      />
      <Button
        size="sm"
        disabled={busy !== null}
        onClick={() => inputRef.current?.click()}
        title="图片 / 视频 / 音频 / 文本 / PDF；类型与大小由后端校验"
      >
        {busy ? (
          <Loader2 aria-hidden className="size-3.5 animate-spin" />
        ) : (
          <Upload aria-hidden className="size-3.5" />
        )}
        {busy ? `上传中 ${busy.done}/${busy.total}` : "上传素材"}
      </Button>
    </>
  );
}
