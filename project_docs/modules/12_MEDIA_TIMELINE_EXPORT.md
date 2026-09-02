# 12 视频、音频与导出

> 状态：**未实现**（三个任务类型是预留，无 Provider、无表、无 Worker 处理器、无页面）
> 优先级：P0
> 负责人：待定
> 最近核对：2026-09-02

本模块是 M2 的核心。终点定义见 ADR-032：**项目内每个镜头产出一条带配音的 MP4**，
可单镜下载、可批量下载全部镜头。字幕、BGM、转场、整集时间线、剪映导出都不在内。

## 1. 模块目标与边界

把已确认的分镜（每镜：台词、首帧图、时长）变成：

```text
每镜台词 → TTS → 真实音频时长 → 回写镜时长
每镜时长 → 按视频模型单段上限切成 N 段 → 每段一次图生视频 → 段视频
段视频 + 配音 → ffmpeg → 每镜一条 MP4 → 下载
```

边界：

- 本模块拥有：配音、段、段视频候选、每镜成片、渲染规格。
- **执行状态只认 `tasks.status`**（ADR-008）。本模块不建第二套状态机。
- 媒体文件归资产模块（07），`assets` 已有 `video` / `audio` 类型和 `duration_ms` 列。
- 模型解析归 Gateway（05）；预扣结算归计费（09）；首帧图归一致性（06）。
- **不做**：整集时间线、字幕轨、BGM、转场、剪映草稿导出（决策记录 §1 明确不做）。

## 2. 用户与使用场景

- 分镜确认后点"生成配音"，听一遍，不满意换个音色重配。
- 点"生成视频"，系统按当前视频模型的单段上限自动切段并逐段生成，界面显示 3/5 段完成。
- 某段不满意，只重出那一段，其它段不动；重出的是新候选，旧的还在。
- 点"合成"，得到这一镜的 MP4，播放、下载。
- 一次性下载全部镜头的 MP4。
- 改了台词之后，这一镜的配音和视频显示"上游已换 / 待重出"，由用户决定重出还是保留。

## 3. 当前真实能力

状态：**未实现**。下面全部是预留，按决策记录 §0 的定义**不计入功能**。

| 项 | 状态 | 证据 |
|---|---|---|
| `video.generate` / `audio.tts` / `timeline.render` 三个任务类型 | 预留 | `apps/api/modules/task/models.py:TASK_TYPES` 里有，但 `worker/jobs/execute.py:_dispatch` 只处理 `mock.*` 和 `image.generate`，其余一律抛 `no handler for {task_type}` |
| 计费能力映射 | 预留 | `billing/pricing.py:_CAPABILITY_OF` 已写 `video.generate → image_to_video`、`audio.tts → text_to_speech`；但 `_shape()` 对这两类没有分支，会走到 `log.warning("pricing.unknown_task_type")` 后按 `units=1` 兜底 |
| `video_retry_factor = 150` | 预留 | `billing/service.py` 的默认规则里有，无人读 |
| `assets` 支持 `video` / `audio` 类型与 `duration_ms` | 已实现 | `asset/models.py:ASSET_TYPES`、`Asset.duration_ms` |
| Skill 处理器白名单里的 `timeline.render` | 预留 | `skills/spec.py` 白名单里有，Skill 运行时本身未接线（ADR-026） |
| 前端 video / audio / export 类型 | 预留 | freeflow 的分镜编辑器视频按钮只显示"未接入"提示 |
| **视频 / TTS Provider** | 未实现 | `adapters/providers/` 只有 `deepseek.py`（文本）和 `dashscope.py`（图像）；`gateway/catalog.py:SPECS` 只有 `text_generation` 和 `image_generation` 两个能力 |
| **ffmpeg / ffprobe** | 未实现 | `infra/docker/api.Dockerfile` 只装了 `build-essential` 和 `curl`，镜像里**没有 ffmpeg** |
| 时间线 / 段 / 配音的任何一张表 | 未实现 | `migrations/versions/` 里没有 |

## 4. 功能需求

### P0（挡住"逐镜 MP4"）

- **FR-MEDIA-001**：接一个 TTS Provider（先 DashScope CosyVoice），产出音频资产并写回**真实时长**。
- **FR-MEDIA-002**：接一个图生视频 Provider（先 DashScope 万相 i2v），入参是首帧图 + 提示词 + 时长。
- **FR-MEDIA-003**：视频模型在 adapter 里声明**单段最大时长**（ADR-031 第 5 条、ADR-032 第 3 条），
  Gateway 能按能力查出来。
- **FR-MEDIA-004**：分镜阶段消费这个上限，产出每镜时长与段数（与模块 04 的 FR-AGENT-001、
  模块 03 的 FR-CONTENT-003 是同一件事的三面）。
- **FR-MEDIA-005**：镜内切段，每段一次图生视频调用；段拼成镜。
- **FR-MEDIA-006**：每镜 MP4 = 段视频拼接 + 配音混音（假设 A2，见决策记录 §10；
  若负责人的意思是纯视频，本步退化为段拼接）。
- **FR-MEDIA-007**：单镜下载 + 批量下载全部镜头。
- **FR-MEDIA-008**：段视频、配音都是**多候选 + 恰好一个当前版**，下游记录消费的是哪一版（ADR-033）。
- **FR-MEDIA-009**：视频 / TTS / 合成三类任务全部走 `tasks` + reserve/settle/release，
  估算按 `model_pricing`，**不写价格常量**。
- **FR-MEDIA-010**：单镜失败不影响其它镜。用户能看到"18/20 镜已成片，2 镜失败"，
  并单独重试失败的那两镜。

### P1

- **FR-MEDIA-011**：音色档案（`voice_profiles`）：每个角色一个音色，项目内复用。
- **FR-MEDIA-012**：视频质量抽检与时序一致性记录（复用 `shot_quality_scores` 的形状）。
- **FR-MEDIA-013**：分级渲染（预览档 / 定稿档，ADR-015）。
- **FR-MEDIA-014**：段视频失败时用首帧静帧 + 缓慢推镜占位，让用户先看到整镜。
- **FR-MEDIA-015**：独立队列（video / audio / render），不与轻量文本任务争抢并发。
  `worker/main.py` 现在是单队列 `max_jobs = 8`、`job_timeout = 900`。

### P2 / 不做（决策记录 §1）

整集时间线合成、字幕（烧录与 SRT）、BGM、转场、剪映草稿导出。
这些**不能出现在 P0/P1 需求里**，也不建对应的表。

> 特别提醒：`aigc_studio_docs/12_MVP_Roadmap.md` 的 M2 段和 `17_ConsistencyEngine.md` §5
> 都写着"时间线装配（视频轨 + 配音轨 + BGM 轨 + 字幕轨）"。ADR-032 把它砍掉了，
> 以 ADR-032 为准。ADR-011"音频优先"**继续有效**——被砍的是多轨时间线，
> 不是"先有音频时长再定镜时长"这条顺序。

## 5. 数据模型草案

四张新表 + 两处对现有表的引用。全部继承 `OrgEntity`（自带 `org_id` / `created_at` /
`updated_at` / `deleted_at`），每个用户侧查询带 `org_id`，跨租户返 404。
**金额一律 `BIGINT` 最小单位**，且金额只存在于 `ledger_entries`，这四张表里不存钱。
**执行状态一律不存**：这四张表都没有 `status` 列，"在不在跑"只问 `tasks.status`（ADR-008）。

### 5.1 `shot_voiceovers` — 每镜配音候选

```text
id, org_id, project_id
shot_index            int      分镜里的镜号（与 shot_conditioning.shot_index 同源）
speaker_ref           str(32)  说的是哪个角色（StoryboardShot.speaker_ref）
dialogue_text         text     合成时用的台词全文（台词后来被改了也要能查出用的是哪一版）
voice_id              str(64)  音色
model_id              str(64)  实际使用的 TTS 模型（failover 后是实际那个，ADR-031 第 7 条）
asset_id              uuid     音频资产（assets.duration_ms 存真实时长）
duration_ms           int      冗余一份，避免每次 join assets
task_id               uuid|null 生成它的任务；用户上传的配音没有任务
is_current            bool     恰好一个（ADR-033 第 1、2 条）
```

### 5.2 `shot_segments` — 镜内的段（切段方案）

段是**镜内的视频生成单元**，不是 ReelBench 的"一组镜头"（决策记录 §3.2）。

```text
id, org_id, project_id
shot_index            int
segment_index         int      镜内第几段，从 1 开始
planned_duration_ms   int      这一段计划多长（切段算法算出来的）
video_model_id        str(64)  切段时依据的视频模型
max_segment_ms        int      切段时该模型的单段上限（冻结一份：换模型后要能解释旧的段为什么是这么切的）
first_frame_asset_id  uuid|null 这一段的首帧图（衔接方案见 §7，方案不同这一列的来源不同）
```

段是**计划**，不是产物。段的产物在下一张表。

### 5.3 `segment_videos` — 段视频候选

```text
id, org_id, project_id
segment_id            uuid     属于哪一段
model_id              str(64)  实际使用的视频模型
prompt                text     实际下发的提示词全文（与 shot_conditioning.resolved_prompt 同理，必须存）
source_image_asset_id uuid     消费的首帧图是哪一张候选（ADR-033 第 3 条：记录消费的版本）
asset_id              uuid     视频资产
duration_ms           int      ffprobe 读出的真实时长
task_id               uuid|null
is_current            bool     同一 segment_id 下恰好一个
```

### 5.4 `shot_renders` — 每镜成片

```text
id, org_id, project_id
shot_index            int
render_spec_json      jsonb    结构化渲染规格（见 §8），可复现
consumed_segment_video_ids uuid[]  这次合成消费了哪些段视频候选
consumed_voiceover_id uuid|null    消费了哪一条配音候选
asset_id              uuid     成片 MP4 资产
duration_ms           int
task_id               uuid|null
is_current            bool     同一 shot_index 下恰好一个
```

### 5.5 与现有表的关系

| 现有表 | 关系 |
|---|---|
| `tasks` | 唯一执行状态真相。上面四张表的 `task_id` 只是**指针**，不复制 `status`。列表接口要显示"正在生成"就 join `tasks`，跟 `consistency/render.py:list_renders` 现在的做法一致 |
| `assets` | 所有媒体文件。`type` 用现成的 `video` / `audio`，时长写 `duration_ms`（列已存在） |
| `shot_conditioning` | 首帧图的来源。`segment_videos.source_image_asset_id` 指向的是它产出的资产 |
| `projects.current_state_json.storyboard` | 镜号、台词、说话人、时长、段数的正文所在。**不实体化**（决策记录 §3.4），所以上面四张表用 `shot_index` 而不是外键指向一张不存在的 `shots` 表 |
| `ledger_entries` | 唯一的金额所在 |

### 5.6 "当前版"放在哪

放在**候选行自己身上**（`is_current` 布尔列），不在父表上放一列 `current_xxx_id`。

理由：父表放指针要求每次切换改两行（清旧的、设新的），而 `shot_segments` 这一层根本没有父行
（段的父是 `shot_index`，一个 JSON 里的数字）。布尔列的代价是"恰好一个"要靠约束保证——
用 PostgreSQL 部分唯一索引：

```sql
CREATE UNIQUE INDEX uq_segment_videos_current
  ON segment_videos (segment_id) WHERE is_current AND deleted_at IS NULL;
```

`shot_voiceovers` 按 `(project_id, shot_index)`、`shot_renders` 按 `(project_id, shot_index)` 同理。
切换当前版在一个事务里做，先清后设。

## 6. 视频模型的"单段最大时长"如何声明

**放 adapter 的能力声明里**（ADR-031 第 5 条、ADR-032 第 3 条），不放数据库、不放配置文件。

理由：单段上限是**协议事实**，不是价格也不是策略。万相约 5 秒、Seedance 15 秒以上，
这是 Provider 的接口决定的，运营改不了它，改了只会让请求被上游拒绝。
价格必须可热更新（`model_pricing`），能力不需要——加一个模型本来就要写 adapter 代码。

### 6.1 建议的声明形状

`gateway/catalog.py:ProviderSpec` 现在的 `models` 是 `(model_id, priority)` 的元组。
视频能力需要每个模型多带几个数，建议扩成一个小结构（文本 / 图像的现有形状不动）：

```python
@dataclass(frozen=True, slots=True)
class VideoModelSpec:
    model_id: str
    priority: int
    max_segment_ms: int        # 单段最大时长
    supports_last_frame: bool  # 是否支持首尾帧模式（§7 方案 C 的前提）
    sizes: tuple[str, ...]
```

并新增两个能力：`video_generation`、`tts`（ADR-031 第 2 条已定这四个能力维度）。

### 6.2 分镜阶段怎么消费它

一个新的只读查询，跨模块只调 Gateway 的 service 层：

```text
gateway.video_capability(org_id, project_id) -> {model_id, max_segment_ms, source_layer}
```

`source_layer` 是三层解析里命中的那一层（组织默认 / 项目覆盖 / 本次临时），
ADR-031 的"代价"一节要求每个生成任务记下解析出的模型和它来自哪一层。

分镜阶段（模块 04 的 `visual.storyboard.v1`）把 `max_segment_ms` 作为**模板变量**注入，
但**段数不由模型算**——模型填这类数字只会得到看着合理却对不上的值
（`StoryboardShot` 的注释已经写过这个教训）。正确分工：

- Agent 产出：镜的**内容**、台词、景别、运镜。
- 确定性代码产出：镜时长（来自 TTS，见 §9）、段数 `ceil(shot_ms / max_segment_ms)`、每段时长。

换视频模型后段数会变，属于"上游变更"，按 ADR-033 标记过期，**不自动重跑**。
`shot_segments.max_segment_ms` 冻结一份，就是为了让"这些段为什么是这么切的"在换模型后仍可解释。

## 7. 段与段的首帧衔接：三个候选方案（**只列方案，不做决定**）

ADR-032 第 5 条把这条留给 Lead 在 M2 实施前定。三个方案的代价如下。

### 方案 A：沿用上一段的末帧

第 N+1 段的首帧 = 第 N 段视频的最后一帧（ffmpeg 抽帧）。

| 维度 | 代价 |
|---|---|
| 衔接观感 | 最好，画面连续 |
| 并发 | **段必须串行**。一个 4 段的镜，生成时间 = 4 × 单段时间，不能并行 |
| 失败放大 | 第 1 段崩了，后面 3 段全部作废重来 |
| 漂移 | 误差逐段累积：末帧本身带着上一段的漂移，喂给下一段会放大 |
| 依赖 | 需要 ffmpeg 抽帧（镜像里现在没有 ffmpeg，见 §3） |
| 候选版本交互 | 切换第 N 段的当前版会让第 N+1 段的首帧失效 → 整条后续链标记过期 |

### 方案 B：所有段共用同一张首帧图

每段都用该镜的当前版首帧图起头。

| 维度 | 代价 |
|---|---|
| 衔接观感 | 最差。每段都从同一个画面重新开始，拼起来像"卡一下再重播" |
| 并发 | **段可以完全并行**，一个 4 段的镜生成时间 ≈ 单段时间 |
| 失败放大 | 无。哪段崩了重出哪段 |
| 漂移 | 不累积，每段独立 |
| 依赖 | 无额外依赖，现有能力就能做 |
| 候选版本交互 | 最简单：切换首帧图 → 全部段过期，语义清晰 |

### 方案 C：首尾帧模式（模型支持时）

给模型同时指定首帧和尾帧，尾帧用下一段的首帧图（需要预先为每段准备一张图）。

| 维度 | 代价 |
|---|---|
| 衔接观感 | 理论最好且可控，段边界处画面对得上 |
| 并发 | 可并行（尾帧不依赖上一段的产物） |
| 失败放大 | 无 |
| 漂移 | 受控：每段两端都被钉住 |
| 依赖 | **模型必须支持首尾帧**（万相部分型号支持，各家不一）；且要**先额外出 N-1 张关键帧图**——多一轮出图成本，也多一道用户确认 |
| 候选版本交互 | 最复杂：一个镜有 N 张关键帧图，每张都有候选和当前版 |

### 一句话对比

A 换并发换连贯，B 换连贯换并发和简单，C 两头都要但要多花一轮出图钱、且不是所有模型都支持。
**Lead 定。** 数据模型上三个方案都装得下：`shot_segments.first_frame_asset_id` 在 A 里由
上一段末帧填、在 B 里全填同一张、在 C 里指向预生成的关键帧图。

## 8. 每镜 MP4 的 Render Spec

**禁止 Agent 生成 shell 命令。** 渲染器只接受结构化的 Render Spec，自己拼命令行。
这条 ADR-032 第 6 条明确保留。

### 8.1 白名单原则

1. **只认枚举，不认字符串。** 编码器、容器、分辨率、帧率、音频码率全部是后端定义的枚举值，
   请求里传枚举名而不是 ffmpeg 参数。传进来一个不认识的值直接 422。
2. **所有输入必须是授权的 Asset ID**，不是路径也不是 URL。渲染前逐个校验
   `asset.org_id == 当前 org`，任何一个不属于本租户就整体拒绝。
3. **没有"透传参数"字段。** 一个 `extra_args` 就能让白名单形同虚设。要加参数就改代码加枚举。
4. **输出路径由服务端生成**，不接受调用方指定。
5. **临时目录隔离**，一次渲染一个目录，结束即清。大文件从对象存储流式下载到临时目录，
   不经过 API 进程内存。
6. **失败只保留结构化阶段 + 短日志摘要**，不把完整 ffmpeg 命令行回给用户
   （命令行里有存储路径和内部结构）。
7. **Render Spec 落库**（`shot_renders.render_spec_json`），因为它是"这条 MP4 是怎么来的"的唯一答案。

### 8.2 形状草案

```json
{
  "version": 1,
  "video_track": [
    {"asset_id": "...", "in_ms": 0, "out_ms": 5000},
    {"asset_id": "...", "in_ms": 0, "out_ms": 3200}
  ],
  "audio_track": [
    {"asset_id": "...", "start_ms": 0, "gain_db": 0}
  ],
  "output": {"container": "mp4", "video_codec": "h264", "audio_codec": "aac",
             "resolution": "720p", "fps": 24, "preset": "final"}
}
```

没有 `filters`、没有 `subtitle_track`、没有 `bgm_track`——那些不在 M2 内，
现在留字段只会让人以为能用。

### 8.3 工程前提

`infra/docker/api.Dockerfile` 需要加 `ffmpeg`（含 `ffprobe`）。
注意 compose 里 api 和 worker 共用 `image: aigc-studio-backend`，改依赖两个都会重建——
这正是当初共用镜像的原因。

## 9. TTS 时长回写镜时长的规则草案

音频优先（ADR-011）：视频片段可以按需生成任意时长，人说话的速度不能改。所以先有音频。

### 9.1 规则

```text
对每个镜：
  1. 取该镜当前版配音的真实时长 speech_ms（来自 assets.duration_ms，由 ffprobe 读出）
  2. shot_ms = speech_ms + lead_in_ms + tail_ms
  3. shot_ms = clamp(shot_ms, min_shot_ms, max_shot_ms)
  4. 无台词的镜：shot_ms = default_silent_shot_ms
  5. segments = ceil(shot_ms / max_segment_ms)
  6. 平均分配：每段 shot_ms / segments，向上取整到 100ms，最后一段吸收余数
```

### 9.2 参数放哪

`lead_in_ms` / `tail_ms` / `min_shot_ms` / `max_shot_ms` / `default_silent_shot_ms`
是**业务阈值**，走 `pricing_rules`（ADR-014 的口径：阈值不写代码常量、不冻进会被分发的 YAML）。
`max_segment_ms` 是**协议事实**，走 adapter 声明（§6）。两者不要混。

### 9.3 幂等与回写边界

- 回写的是 `projects.current_state_json.storyboard.shots[i].duration_ms`（新增字段，
  见模块 03 的 FR-CONTENT-003），走 ADR-029 的 Patch 写路径，`source` 用一个新的
  `system_derive`（需要扩 `content/models.py:SOURCES`），这样它和人工改动在同一条历史里可审计。
- **只在配音当前版变化时回写**。重新配了但没切当前版，不回写。
- 用户手工改过 `duration_ms` 之后，系统回写要么跳过、要么提示冲突——
  **这是一个待定问题，见 §12**。
- 回写导致段数变化时，已有的段视频按 ADR-033 标记过期，**不自动重跑**。

## 10. 计费

三类任务全部走现有的 reserve/settle/release（ADR-005），不新建计费路径。

| 任务类型 | 能力 | 计价维度 | 废片系数 |
|---|---|---|---|
| `audio.tts` | `text_to_speech` | 字数（台词字符数） | 暂不加（TTS 边际成本近似为零） |
| `video.generate` | `image_to_video` | 秒数（该段的 `planned_duration_ms`） | `video_retry_factor`（默认 150，已在 `billing/service.py` 的默认规则里） |
| `timeline.render` | 无上游能力 | 本地 ffmpeg，按 `pricing_rules` 的本地渲染档位 | 无 |

要做的接线：

1. `billing/pricing.py:_shape()` 补 `video.generate` 与 `audio.tts` 两个分支
   （现在会掉进 `log.warning("pricing.unknown_task_type")` 后按 `units=1` 兜底，估价必然错）。
2. `_DEFAULT_MODEL` 补这两类的默认模型 id。
3. 单价一律查 `model_pricing`，**不写常量**。`19_UnitEconomics.md` §1.1 的那张价格表是
   调研快照，只能用来填库，不能进代码。
4. `timeline.render` 不打上游，故意不在 `_CAPABILITY_OF` 里——本地 ffmpeg 合成不因为
   用户配了自己的 Key 就该便宜。这条现有代码已经写对了，保留。
5. 预扣和结算的 `attempt` 编号必须一致（`begin_execution` 会自增 attempt，
   差一位会导致任务成功了但钱没扣，且不报任何错）。
6. 换模型必重算 Credits 预估（ADR-024 硬约束 1，ADR-031 未推翻它）。

## 11. API 草案

```text
POST /api/v1/projects/{id}/shots/{shot_index}/voiceover     生成配音（建 audio.tts 任务）
PUT  /api/v1/projects/{id}/shots/{shot_index}/voiceover     指定当前版 {voiceover_id}（不花钱）
GET  /api/v1/projects/{id}/shots/{shot_index}/voiceovers    候选列表，带 is_current

POST /api/v1/projects/{id}/shots/{shot_index}/segments      按当前视频模型切段（纯计算，不花钱）
GET  /api/v1/projects/{id}/shots/{shot_index}/segments      段与各自的视频候选

POST /api/v1/projects/{id}/segments/{segment_id}/videos     生成一段视频（建 video.generate 任务）
PUT  /api/v1/projects/{id}/segments/{segment_id}/videos     指定当前版（不花钱）

POST /api/v1/projects/{id}/shots/{shot_index}/render        合成这一镜的 MP4（建 timeline.render 任务）
GET  /api/v1/projects/{id}/renders                          全项目每镜成片，带 is_current 与过期标记
GET  /api/v1/projects/{id}/renders.zip                      批量下载（或返回一批预签名 URL）
```

约定沿用现有的两条：

- **POST = 生成一个新候选**（扣 Credits），**PUT = 指定当前版**（幂等、不花钱）。
  这是 S14 已经在图上验证过的语义分工，视频和配音照抄。
- 所有耗时接口**返回 Task**，不同步等 Provider 或 ffmpeg。前端进度走项目 SSE。

## 12. 留给 Lead 的待定问题

1. **段与段的首帧衔接选哪个方案**（§7 的 A / B / C）。ADR-032 第 5 条指定由 Lead 定。
2. **候选存哪张表**：`assets` 加变体组字段，还是本模块的 `segment_videos` / `shot_voiceovers`
   自己当候选表？ADR-033 第 5 条要求 07 与 12 对齐同一个方案。本文档草案假设的是后者
   （媒体候选自己建表，`assets` 只存文件），07 若给出不同方案以 Lead 的裁决为准。
3. **A2 假设**（决策记录 §10）：每镜 MP4 是否包含配音？本文档按"包含"写。若为纯视频，
   §5.4 的 `consumed_voiceover_id` 和 §8.2 的 `audio_track` 退化为空。
4. **用户手工改过镜时长后，TTS 回写要不要覆盖它**（§9.3）。三个选项：系统优先、
   用户优先并提示冲突、锁定字段。这是产品语义，不是技术问题。
5. **批量下载的形态**：服务端打 zip（要占 Worker 和临时空间）还是返回一批预签名 URL
   （前端逐个下，浏览器会拦多文件下载）。

## 13. 缺口与风险

| 缺口 / 风险 | 影响 | 缓解 |
|---|---|---|
| 镜像里没有 ffmpeg | 合成完全做不了 | 改 Dockerfile，api/worker 共用镜像一起重建 |
| 无视频 / TTS Provider | M2 全部阻塞 | 先接 DashScope 两条；Seedance 的 Key 由负责人后续提供 |
| `_shape()` 没有视频 / TTS 分支 | 估价按 `units=1` 兜底，预扣与实际严重不符 | FR-MEDIA-009 |
| 图生视频的角色漂移 | 廉价感的主要来源，且当前只有 L0 一致性（见模块 06） | 首帧锁定 + 低运动预设 + 抽检 |
| 段边界可见 | 用户第一眼就会看到 | §7 的方案选择 |
| 单队列 | 一个 15 分钟的视频任务会占满 `max_jobs=8` 里的一个槽，文本请求排队 | FR-MEDIA-015 |
| 候选不自动清理 | 对象存储持续增长（ADR-033 的"代价"一节已记） | 运维策略进模块 14 |
| 盈亏平衡的"部"重新定义 | `19_UnitEconomics.md` 按"每天 16 部成片"算，"部"现在是逐镜 MP4 的集合 | ADR-032 明确**本轮不重估**，等第一批真实数据 |

## 14. 迭代计划

1. Dockerfile 加 ffmpeg；`_dispatch` 补三个处理器的空壳（先只跑 Mock）。
2. 定 adapter 的视频 / TTS 能力声明形状，Gateway 加两个能力。
3. 接 TTS：拿到真实时长 → 回写镜时长 → 段数计算（§9）。此时还没有视频，但时长链路已经能验。
4. 建四张表 + 部分唯一索引；候选与当前版语义先在配音上跑通。
5. 接图生视频，按 Lead 定的衔接方案生成段视频。
6. Render Spec + ffmpeg 合成每镜 MP4，单镜下载。
7. 批量下载 + 失败镜的单独重试。
8. P1：音色档案、分级渲染、占位降级、独立队列。

## 15. 验收标准与测试

- 一个 3 镜的测试项目，不经任何手工命令，产出 3 条 MP4 并能下载；每条都带该镜配音。
- 每条 MP4 可追溯：消费了哪些段视频候选、哪条配音候选、Render Spec 全文、模型 id、任务 id、费用。
- 每个 `(segment_id)` 下 `is_current` 的行**恰好一条**，由部分唯一索引保证；
  并发切换当前版有测试。
- 视频 / TTS 任务的预扣与结算 `attempt` 一致，失败时预扣**全额释放**，余额审计平（复用
  `tests/integration/test_task_billing_lifecycle.py` 的断言口径）。
- `PUT` 指定当前版：无 `tasks` 行、无 Ledger 流水、余额不动。
- 某一镜失败不影响其它镜出片；失败镜可单独重试。
- 换视频模型后，已有段被标记为过期，**不自动重跑**，且旧段的 `max_segment_ms` 仍可解释。
- Render Spec 里传入不属于本租户的 `asset_id` 时整体拒绝，不渲染。
- Render Spec 里传入枚举外的编码器 / 分辨率时 422，不落到 ffmpeg 命令行上。
- 跨租户访问配音 / 段 / 成片一律 404。
- `pricing_rules` 里没有对应规则时估算走兜底且记日志，不崩。
- 全链路无价格常量：`tests/unit/test_pricing_no_constants.py` 保持绿。
