# 05 模型网关与 BYOK

> 状态：**部分实现**（文本 + 图像已通，视频 / TTS 未接；三层默认只有中间一层）
> 优先级：**P0**（Wave 2 的第一项，`video_generation` / `tts` 不接就没有逐镜 MP4）
> 负责人：待定
> 最近核对：2026-09-02
> 权威顺序：[DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §4 > ADR-031 / 032 > ADR-002 / 024 / 025 / 027 > 当前代码

---

## 1. 模块目标与边界

把"业务要什么能力"解析成"这次用哪家的哪个模型、拿谁的 Key、失败了换谁"，
并把结果规范化成与厂商无关的形状。业务模块永远不 import 任何模型 SDK
（ADR-002、ADR-009）。

**拥有**：能力目录、Provider Adapter、路由解析、熔断与 failover、
Key 的取用（平台 Key 与 BYOK 两条路）、上游用量与实际模型的回报。

**不拥有**：Credits 的预扣与结算（模块 09）、任务状态机（模块 08）、
提示词的合成（模块 06）、生成结果的落库（模块 07）。
BYOK 的凭证表放在 `billing/credentials.py` 而不是本模块，
理由是它存在的原因是计费折扣（ADR-025），本模块只在调用前向它取一次明文。

---

## 2. 用户与使用场景

用户是外部创作者，不是运维。他们对本模块只有四个诉求：

1. **每个能力下我能用哪几个模型**，每个大概花多少 Credits。
2. **在生成前改一次**，只对这一次生效（对比万相 flash 与 plus 谁更好看）。
3. **不想每次都改**，所以要能设组织默认和项目默认。
4. **我有自己的 Key**，用它跑，价格要便宜（ADR-025）。

反过来，用户**不**关心 Provider 挂了要换哪家——那是平台的事，
但"换过了"必须让他看见（ADR-031 第 7 条）。

---

## 3. 当前真实能力

状态词按 [DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §0。
每条都能落到代码路径上。

| 能力 | 状态 | 代码 / 测试证据 |
|---|---|---|
| 能力 → Provider → 模型 的唯一目录 | 已实现 | `apps/api/modules/gateway/catalog.py`（`SPECS` 两项：DeepSeek / 万相） |
| 文本生成（DeepSeek，2 个模型） | 已实现 | `adapters/providers/deepseek.py`；`apps/api/modules/agent/llm.py:63` |
| 图像生成（DashScope 万相，2 个模型） | 已实现 | `adapters/providers/dashscope.py`；`worker/jobs/generation.py:62` |
| 优先级排序 + failover + 熔断 | 已实现 | `gateway/service.py::_attempt` / `_candidates`；`gateway/breaker.py`；`tests/unit/test_gateway_routing.py` |
| 失败分类决定换不换家 | 已实现 | `apps/api/core/errors.py` 的 `failover` 列；`gateway/service.py::_attempt` |
| BYOK：加密存储、按能力一把、尾号展示 | 已实现 | `billing/credentials.py`；`core/crypto.py`；`tests/integration/test_byok_credentials.py` |
| BYOK：调用时现查现解密、不回落平台 Key、熔断按 org 隔离 | 已实现 | `gateway/service.py::_resolve` / `_as_byok_error`；`tests/unit/test_gateway_byok_routing.py`（11 例） |
| BYOK：免费探针测连接 | 已实现 | `gateway/probe.py`；`POST /provider-credentials/{capability}/test` |
| 项目级模型偏好（按能力覆盖，重排不过滤） | 已实现 | `project/service.py::set_model_preference`；`gateway/service.py::_prefer_model`；`tests/integration/test_model_preference.py` |
| 推理模型在 `no_reasoning_roles` 上被丢弃（ADR-024 硬约束 2） | 已实现 | `gateway/service.py::_preferred_model`；`catalog.REASONING_MODELS` |
| 模型目录只读接口 + 全局模型页 | 已实现 | `GET /model-catalog`；`apps/web/components/freeflow/model-catalog-page.tsx` |
| `org_id` 传到 Gateway（决定用谁的 Key） | 已实现 | 图像 `worker/jobs/generation.py`；文本 `agent/runner.py:300`（**本分支修复，`main` 上仍是漏传状态**） |
| 出图任务回报实际使用的模型 | 部分实现 | `worker/jobs/generation.py` 返回 `model_id`、写进 asset metadata；**没有记录它来自哪一层，前端不显示** |
| `video_generation`（图生视频） | 未实现 | `catalog.SPECS` 里没有；`credentials.CAPABILITY_LABELS` 与 `skills/builtin/novel_to_anime.yaml` 里只有能力字符串 → 按 §0 属"预留"，不计功能 |
| `tts`（语音合成） | 未实现 | 同上 |
| 视频模型的"单段最大时长"声明（ADR-032 第 3 条） | 未实现 | `ProviderSpec` 没有这个字段 |
| 组织级模型默认 | 未实现 | 没有表、没有列、没有接口 |
| 生成前临时选择 + 界面上的模型选择按钮 | 未实现 | 没有接口，`/freeflow/models` 明确写着"选在项目设置里做" |
| 文本能力的 OpenAI 兼容自定义端点 | 未实现 | 没有表、没有虚拟 Provider |
| "可选模型 + 各自 Credits 估算"接口 | 未实现 | `GET /model-catalog` 不含任何金额 |
| 换模型必重算 Credits 预估（ADR-024 硬约束 1） | **未实现（且当前被违反）** | 见 §11.1 |
| ComfyUI / 本地 Runtime | 未实现 | ADR-003 / ADR-004 只有设计 |

---

## 4. 能力枚举与请求 / 结果契约

### 4.1 能力枚举（草案，ADR-031 第 2 条）

四个，不多不少：

| 能力 id | 中文 | 现状 | 首批 Provider |
|---|---|---|---|
| `text_generation` | 文本生成 | 已实现 | DeepSeek（OpenAI 兼容自定义端点为 P1，未实现，见 §5.2） |
| `image_generation` | 图像生成 | 已实现 | DashScope 万相 |
| `video_generation` | 视频生成（图生视频） | 未实现（Wave 2） | DashScope 万相 i2v，之后 Seedance |
| `tts` | 语音合成 | 未实现（Wave 2） | DashScope CosyVoice，之后 FishAudio / MiniMax |

图像编辑（图生图、多参考图）**不在本轮**，加能力时另提 ADR（ADR-031 第 2 条）。

### 4.2 一个必须先解掉的命名冲突

代码里现在有一组更细的能力字符串，与 ADR-031 的四个对不上：

| 出现位置 | 现有字符串 |
|---|---|
| `billing/credentials.py::CAPABILITY_LABELS` | `text_to_speech`、`image_to_video`、`text_to_video`、`image_editing`、`vision_understanding`、`speech_to_text` |
| `billing/pricing.py::_CAPABILITY_OF` | `image_to_video`、`text_to_speech` |
| `skills/builtin/novel_to_anime.yaml::model_policy.user_selectable` | `text_to_speech`、`image_to_video` |
| `gateway/router.py::_ORDER` | `text_to_speech`、`image_to_video` |

**裁决**：以 ADR-031 为准，收敛到 `video_generation` / `tts`。
"是图生视频还是文生视频"是**模型的输入形状**，不是一个新能力——
万相 i2v 和 Seedance 都要首帧图，把它写进能力名会让"同一个能力换个 Provider"
变成"换一个能力"，三层默认和 BYOK 凭证全部要跟着改键名。
输入形状放进模型声明（§5.1 的 `needs_init_image`）。

**代价与执行**：这四处字符串是**已落库的键**（`provider_credentials.capability`、
`projects.model_preference` 的 JSON key），改名要带数据迁移。
现在库里 `text_to_speech` / `image_to_video` 不可能有真实行（这两个能力
从未接入，`configurable_capabilities()` 取的是"目录里有 + Skill 声明"的交集，
配不进去），所以迁移是**只改常量、不改数据**。改之前跑一次
`SELECT DISTINCT capability FROM provider_credentials` 确认。

### 4.3 四个能力的请求 / 结果契约

**所有能力共有**（现在缺，Wave 2 补齐）：

请求侧：
- `org_id`（必填，决定用谁的 Key 与计费档；漏传的后果见 §9.3）
- `project_id`（可选，读项目覆盖）
- `override_model_id`（可选，生成前临时选择；**只对本次生效，不回写**）

结果侧（`_ResolvedMeta`，四个能力一律带）：
- `model_id`：**实际**使用的模型（不是请求里想要的那个）
- `provider_id`
- `resolved_from`：`override` / `project` / `org_default` / `catalog`（ADR-031 代价第 2 条要求可溯源）
- `key_source`：`platform` / `org`（`adapters/providers/base.py::KeySource` 已有）
- `failover_from`：发生过 failover 时列出试过并失败的 (provider, model)，
  **前端必须显示**（ADR-031 第 7 条：用了谁必须说）
- `usage`：能力相关的用量（token / 张 / 秒）

各能力私有：

| 能力 | 请求必须有 | 结果必须有 |
|---|---|---|
| `text_generation` | `system`、`user`、`json_mode`、`max_output_tokens`、`temperature` | `text`、`tokens_in`、`tokens_out`、`reasoning_tokens` |
| `image_generation` | `prompt`、`negative_prompt`、`size`、`n`、`seed` | `urls`、`actual_prompts`（上游改写过的提示词，不记就不知道画的是什么） |
| `video_generation` | `init_image_url`（首帧，i2v 必填）、`prompt`、`duration_seconds`、`size`、`seed`、`last_frame_url`（可选，首尾帧模式用） | `video_url`、`duration_seconds`（**上游实际产出的时长，不是请求值**）、`fps`、`has_audio`（一律 false，音轨由我们混） |
| `tts` | `text`、`voice_id`、`speed`、`format`、`sample_rate` | `audio_url`、`duration_ms`（**必填**）、`format`、`sample_rate` |

两个字段是硬要求，不是可选项：

- **`tts` 结果的 `duration_ms`**：音频优先时间线（ADR-011）靠它回写镜时长。
  拿不到真实时长就只能按字数估，估错了口型和画面全对不上。
- **`video_generation` 结果的 `duration_seconds`**：上游经常"要 5 秒给 4.8 秒"，
  段拼接时差值会累积。

前者今天不存在（`adapters/providers/base.py` 只有 `TextRequest` / `ImageRequest`），
所以 FR-GW-007（"视频/TTS 用独立契约，不复用图像字段硬拼"）仍然有效。

### 4.4 能力声明：视频的"单段最大时长"

ADR-032 第 3 条要求每个视频模型在 adapter 里声明**单段最大时长**，
而且 ADR-032 第 4 条要求它在**分镜阶段**就能被读到（不是出图之后）。
所以它必须挂在**目录**上而不是调用结果上：

```python
@dataclass(frozen=True, slots=True)
class ModelSpec:
    model_id: str
    priority: int
    pricing_key: str                     # 对应 model_pricing.model_id
    # --- 视频能力专用 ---
    max_segment_seconds: float | None = None   # 万相 ≈ 5，Seedance ≥ 15
    allowed_durations: tuple[float, ...] = ()  # 上游只接受枚举值时用
    needs_init_image: bool = False             # i2v = True
    # --- TTS 专用 ---
    voices: tuple[str, ...] = ()
```

`ProviderSpec.models` 从现在的 `tuple[tuple[str, int], ...]` 换成
`tuple[ModelSpec, ...]`。改动面：`catalog.model_ids()`、`service._routes_for()`、
`gateway/router.py::_item()`、`gateway/schemas.py`。

**分镜阶段怎么拿**：模块 03（分镜生成）按 §6 的三层解析出"这个项目当前的视频模型"，
读它的 `max_segment_seconds`，据此定每镜时长与段数。
换模型后段数会变，属于"上游变更"，按 ADR-033 标记过期而不是自动重跑。

---

## 5. 供应商目录

### 5.1 目录声明什么（ADR-031 第 5 条）

供应商来源是**固定目录**，每家在代码内的 adapter 里声明四件事：

1. **提供哪些能力**（一个 adapter 可以在多个能力上出现，各出一条 `ProviderSpec`）
2. **模型列表**（`ModelSpec`，含路由优先级、输入形状、视频单段上限）
3. **定价键名**（`pricing_key` → `model_pricing.model_id`；
   **不写价格数值**，ADR-014）
4. **平台 Key 从哪个配置项读**（`catalog.platform_key()` 的显式 `if` 分支，
   不要写成 `{id: lambda}` 表——lambda 捕获变量不捕获值，本仓库已因此
   让 DeepSeek 拿着万相的 Key 去请求过一次）

用户在设置页"添加供应商"= **给目录里已有的某一家填 Key**（BYOK，ADR-025），
不是新增一家。目录里没有的家，用户加不进来。

### 5.2 文本能力的 OpenAI 兼容自定义端点（唯一例外）

ADR-031 第 5 条的例外：**只有 `text_generation` 允许一个 OpenAI 兼容自定义端点**。
视频和 TTS 不允许——各家请求形状差异太大，"填个 URL 就能用"做不到，
做了就是假入口。

**在目录里怎么表示**：不进 `catalog.SPECS`（那是进程级常量，自定义端点是
每个 org 一份、随时会改的数据）。做成一条**运行期拼出来的虚拟路由**：

```
provider_id = "provider.custom.text"        # 固定串，不含 org 信息
model_id    = 用户填的模型名（原样透传给上游）
capability  = "text_generation"             # 写死，不接受参数
adapter     = OpenAICompatTextProvider(base_url=..., api_key=...)
```

数据存新表（§8.2 的 `org_text_endpoints`）。解析时机与 BYOK 相同：
在 `_resolve()` 里现查现解密，**不进 `registry()` 那张进程级单例表**
（理由同 ADR-027：那里长期存明文 Key，且失效难处理）。

**怎么隔离，让它不能冒充视频 / TTS**（五条，缺一不可）：

1. **能力写死在构造处**。虚拟路由只在 `_resolve("text_generation", ...)`
   这一个分支里被拼出来；`video_generation` / `tts` 的解析路径**根本不读**
   `org_text_endpoints` 这张表。用代码路径隔离，不是用运行时 `if capability == ...`
   ——后者迟早会被某次重构合并掉。
2. **表本身不带 capability 列**。没有这一列，就没有人能往里存 `"tts"`。
   这是 §4.2 那类命名冲突最便宜的防法。
3. **`base_url` 白名单校验**：必须 `https://`，拒绝回环 / 内网网段 / 云元数据地址
   （169.254.169.254），不跟随跳转。这是一个用户可控的出网地址，
   不校验就是一个 SSRF 入口。
4. **不采信端点的自我声明**。即使它的 `/v1/models` 返回一个叫
   `sora-video-1` 的模型，我们也只按 `text_generation` 的契约调
   `/v1/chat/completions`，结果只按 `TextResponse` 解析。
5. **计费与熔断按 BYOK 处理**：自定义端点用的是用户自己的 Key，
   走 ADR-025 折扣档，熔断按 org 隔离（ADR-027），
   失败**不回落平台 Key**——回落等于平台掏钱替他跑。

**一个 org 一个端点**（ADR-031 说的是"一个"）。要多个时另提 ADR，
因为"多个"会立刻带出"默认用哪个"，与 §6 的三层默认打架。

### 5.3 目录里的模型下线

ADR-031 代价第 1 条：模型列表暴露给用户后，上游下线一个模型会变成用户可见的错误。
所以 `ModelSpec` 要有 `available: bool`：

- `available=False` 的模型**不出现在选择按钮里**；
- 但历史任务的 `result_json.model_id` 仍要能显示出模型名，不能变成裸 id；
- 项目 / 组织默认指向一个已下线的模型时，按 `_prefer_model` 现有行为
  （匹配不上就整份原样返回、按默认优先级走）降级，并在界面上提示偏好已失效。
  代码里这条已经有了：`gateway/service.py` 的 `log.info("gateway.preference_stale")`。

---

## 6. 三层默认与解析顺序

### 6.1 顺序（**逐字引自** [DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §4）

> **三层默认**：组织全局默认（按能力）→ 项目覆盖（`projects.model_preference`，已有）→ **生成前临时选择**（只对本次生效，不回写默认）。
>
> 生成前选择必须是**界面上的模型选择按钮**，不是让用户发消息。

上面写的是**覆盖方向**（后者盖前者）。落到解析代码里，取值顺序是它的反向，
与 ADR-031 第 3 条一致：

```text
生成前临时选择  >  项目覆盖（projects.model_preference）  >  组织默认
                                                          >  目录默认优先级（catalog 的 priority）
```

第四层（目录优先级）不是决策记录里的"层"，它是**兜底**：三层都没有值时
Gateway 仍然要能跑。今天全部依赖这一层加中间那一层。

### 6.2 组织默认落在哪张表（草案）

现有表里没有位置：`organizations` 归模块 01 所有，`projects.model_preference`
是项目级。**建议新建 `org_model_defaults`**：

```sql
CREATE TABLE org_model_defaults (
    id          UUID PRIMARY KEY,
    org_id      UUID NOT NULL REFERENCES organizations(id),
    capability  VARCHAR(32) NOT NULL,   -- §4.1 的四个之一
    model_id    VARCHAR(64) NOT NULL,   -- 必须在 catalog 里存在（同 set_model_preference 的校验）
    updated_by  UUID NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    deleted_at  TIMESTAMPTZ NULL
);
CREATE UNIQUE INDEX uq_org_model_defaults ON org_model_defaults (org_id, capability)
    WHERE deleted_at IS NULL;
```

**为什么不复用"给 `organizations` 加一个 JSONB 列"**（那样与
`projects.model_preference` 对称，看起来更省事）：

- `organizations` 是模块 01 的表。模块 05 往里加列，等于让 Gateway 依赖
  账号模块的表结构，跨模块只调 `service` 层的规矩就破了一半。
- 组织默认是**会被追责的配置**：一个 org 里谁把默认模型从 flash 改成 plus，
  下个月账单翻倍时要查得出来。`updated_by` 在 JSONB 字典里没地方放。
  项目偏好不需要这个，因为项目本身有归属人。
- 一行一个能力，加能力就是加行；JSONB 字典加键要靠应用层保证键名合法，
  §4.2 的命名冲突正是这么来的。

**代价**：多一张表、多一次查询。查询代价可忽略——它与
`_load_model_preference` 合成一次调用即可（都在 `_resolve` 之前）。

### 6.3 `gateway._resolve` 现有行为要不要保留

现状（`gateway/service.py::_prefer_model`）：偏好只把选中的模型**排到最前**，
其余路由**原样留在后面**；选中的模型失败或熔断，仍按原优先级 failover 到
同能力的下一个。

**建议：保留，并补一件事——把实际用了谁回报出去。**

保留的理由：

1. **这是一条已经预扣过 Credits 的调用。** 让它因为一个本可以自动绕过的
   故障失败，用户白付一次钱（`_prefer_model` 的注释已经写明这一点）。
2. Gateway 的熔断 + failover 是 S6 最有价值的部分。为了"所见即所用"
   把它关掉，单家故障会变成全站故障。
3. 用户在下拉框里选一次，表达的是"我想用这个"，不是"坏了就报错给我看"。

补的那件事是 ADR-031 第 7 条的原话：**failover 发生时必须在任务结果里
标明实际使用的模型，前端要显示**。没有这一条，保留 failover 就会破坏
"对比不同模型效果"这个用例——用户以为在看 plus 的图，其实是 flash 的。
落点是 §4.3 结果契约里的 `model_id` + `failover_from`。

**一个例外，需要 Lead 定**（见报告"待定问题"）：生成前**临时选择**的语义
比项目默认强——用户点"用 Seedance 再跑一次"就是为了对比。
这一层要不要改成"失败即失败，不 failover"？
本文档按"仍然 failover + 显著提示"写，因为白付钱的代价更实。

### 6.4 溯源

ADR-031 代价第 2 条：每个生成任务的 `input_json` 必须记下解析出的模型
和它来自哪一层。落点：

- `tasks.input_json.model_id`：**建任务时**写入解析结果（不是 worker 里才算，
  见 §11.1 为什么）；
- `tasks.input_json.model_resolved_from`：`override` / `project` / `org_default` / `catalog`；
- `tasks.result_json.model_id` + `failover_from`：**实际**用了谁。
  两者不一致就是发生过 failover，前端据此显示提示。

---

## 7. 模型选择按钮的接口需求

ADR-031 第 4 条 + ADR-024 硬约束 1（换模型必重算 Credits 预估）要求：
**一次调用拿到"本能力下当前用户可选的模型 + 每个的 Credits 估算"**。
现有 `GET /model-catalog` 不含任何金额，不满足。

```text
GET /api/v1/model-options
    ?capability=image_generation
    &project_id={uuid}          # 可选：带上才知道项目覆盖和默认标记
    &task_type=image.generate   # 估算口径，缺省按能力取默认任务类型
    &units=1                    # 出几张 / 几秒 / 几段
```

响应（每个模型一项）：

```jsonc
{
  "capability": "image_generation",
  "current": { "model_id": "wan2.2-t2i-flash", "resolved_from": "org_default" },
  "options": [
    {
      "model_id": "wan2.2-t2i-flash",
      "label": "快速档",
      "note": "出图快，适合大批量分镜草图",
      "provider_id": "provider.dashscope",
      "provider_label": "通义万相",
      "available": true,
      "key_source": "platform",          // 这个能力配了自有 Key 就是 org
      "estimated_credits": 12,           // 点值，按 units 算
      "estimated_range": [9, 16],        // [×0.8, ×1.4]，与 POST /credits/estimate 同口径
      "is_default": true,
      "default_from": "org_default",     // org_default / project / catalog
      "max_segment_seconds": null,       // 视频能力才有值（ADR-032）
      "disabled_reason": null
    }
  ]
}
```

四条约束：

1. **金额必须由后端算**，走 `billing/pricing.py::estimate`（`model_pricing` 表 +
   `pricing_rules` 的废片系数 + BYOK 分支）。前端不得自己乘。
   ADR-024 硬约束 1 说的"重算"就是这个接口每次都真算，而不是缓存一次。
2. **`key_source` 要在选项上体现**，因为它直接改变价格：配了自有 Key 的能力
   走 `byok_unit_credits` 那一档（ADR-025），数字会差一个量级。
   用户看到"12 Credits"和"3 Credits"的差别，才知道 BYOK 生效了。
3. **`available=false` 的模型不返回**（§5.3），但 `current` 指向一个已下线模型时
   要照实返回并给 `disabled_reason`，否则界面会显示一个不存在的当前值。
4. **要登录**。目录本身对所有租户一样，但估算带 org（BYOK 折扣、
   `pricing_rules` 覆盖），必须按当前用户算。

配套的写接口：

```text
PUT   /api/v1/org/model-defaults/{capability}    # 组织默认，body: {model_id | null}
PATCH /api/v1/projects/{id}/model-preference     # 项目覆盖（已有）
```

生成前临时选择**没有写接口**——它是生成请求体里的一个字段
（`override_model_id`），随请求走完就没了，不回写任何默认。
这是决策记录 §4 "只对本次生效，不回写默认"的直接落法：
给它一条写接口，它就变成了第四层默认。

---

## 8. 数据模型

### 8.1 已有

| 表 / 列 | 归属 | 用途 |
|---|---|---|
| `model_pricing` | 09 | model、单位、provider_cost、credit_price、生效期（ADR-014） |
| `pricing_rules` | 09 | `image_retry_factor`、`byok_unit_credits`、`task_cost_cap` 等 |
| `provider_credentials` | 09 | org × capability × provider，`key_encrypted`（AES-GCM） |
| `projects.model_preference` | 02 | JSONB，能力 → 模型 id（ADR-024） |

### 8.2 需要新增

| 表 | 用途 | 说明 |
|---|---|---|
| `org_model_defaults` | 组织级默认（§6.2） | 三层默认的最外层 |
| `org_text_endpoints` | 文本自定义端点（§5.2） | `id / org_id / label / base_url / model_id / key_encrypted / enabled / created_by / timestamps`，**没有 capability 列** |

Provider 健康度与调用指标**不进业务表**，放 Redis + 指标系统
（现在 `gateway/breaker.py` 已经在 Redis 上）。

---

## 9. 计费路径

### 9.1 平台 Key：全价

```text
预估 = model_pricing.credit_price(model_id)   # 已含 provider_cost 的加价
     × units                                  # 张数 / 秒数 / 段数
     × pricing_rules.<retry_factor> / 100     # 废片率，图像是 image_retry_factor
```

代码：`billing/pricing.py::_platform_unit_price`。表里查不到该模型的价时，
按 `task_cost_cap // 10` 兜底并告警——**宁可高估拦下来，也不要低估放行**，
低估会让预扣不够，结算时差额由平台承担。

### 9.2 BYOK：按 ADR-025 打折

```text
预估 = pricing_rules.byok_unit_credits × units × 废片系数
```

不叠 `overhead_rate`（`byok_unit_credits` 本身就是那笔隐性成本，加两遍是重复计提）。
不是免费——免费等于邀请用户拿自己的低价 Key 无限占用平台存储和计算。
代码：`billing/pricing.py::estimate` 的 `byok` 分支 + `uses_own_key()`。

自定义文本端点（§5.2）走**同一条 BYOK 分支**：用户的 Key、用户的账单。

### 9.3 `org_id` 必须传到 Gateway

这是一条**已经发生过一次**的事故（CLAUDE.md 记过）：文本生成链路上
`org_id` 从未传到 Gateway，配了 BYOK 的租户实际一直在用平台的 Key，
而计费那边**已经按 BYOK 折扣算过了**——平台掏钱、用户按折扣价付款，
且不报任何错。

- 现状：图像链路 `worker/jobs/generation.py` 一直带着；
  文本链路 `agent/runner.py:300` 在 `feat/freeflow-prototype` 上修好了，
  **`main` 上还是漏传的**，合并前必须确认这条修复在里面。
- 回归防线：`tests/integration/test_byok_gateway_wiring.py::test_billing_discount_and_actual_key_agree`
  —— 断言"计费打没打折"和"实际用了谁的 Key"两件事一致。
  新接的每个能力（视频、TTS）都要补一条同形状的用例。
- `project_id` 漏传的后果轻得多：只是项目偏好不生效，没有人多付钱。

### 9.4 熔断上限

`task_cost_cap`（单任务）、`org_daily_cap`（单租户日）、项目 `budget_cap_credits`
三道在 `billing/service.py::_check_caps`。视频接进来时**必须复核 `task_cost_cap`**：
现值 2000 Credits（¥20），一个 12 秒镜头切 3 段、每段重试一次就可能顶到上限。
这个数走 `pricing_rules` 热更新，不改代码。

---

## 10. 功能需求

### 10.1 P0（挡住"逐镜 MP4"）

- **FR-GW-001**：业务调用只传 capability 和标准化 request，不出现任何厂商 SDK。（已实现）
- **FR-GW-002**：新增 `video_generation` 能力，首个 Provider 为 DashScope 万相 i2v，
  契约按 §4.3。
- **FR-GW-003**：新增 `tts` 能力，首个 Provider 为 DashScope CosyVoice，
  结果**必须**带 `duration_ms`。
- **FR-GW-004**：每个视频模型在目录里声明 `max_segment_seconds`，
  并可在分镜阶段被读到（ADR-032 第 4 条）。
- **FR-GW-005**：三层默认按 §6.1 解析；新增 `org_model_defaults` 表与写接口。
- **FR-GW-006**：生成请求支持 `override_model_id`，只对本次生效、不回写。
- **FR-GW-007**：`GET /model-options` 一次返回"可选模型 + 各自 Credits 估算"（§7）。
- **FR-GW-008**：任务的 `input_json` 记下解析出的模型和它来自哪一层，
  `result_json` 记下**实际**使用的模型与 failover 轨迹；前端显示。
- **FR-GW-009**：能力字符串收敛到 §4.1 的四个（§4.2 的迁移）。
- **FR-GW-010**：所有调用返回统一 Usage / Cost / Provider Signal，
  Provider 的临时 URL 必须转存自有对象存储（DashScope 图片链接只有 24 小时有效期）。（已实现，视频与音频要照做）
- **FR-GW-011**：测试环境强制走 Mock，禁止意外产生真实费用。（已实现，
  `tests/unit/test_no_live_provider_in_tests.py`）

### 10.2 P1

- **FR-GW-020**：文本能力的 OpenAI 兼容自定义端点（§5.2）。
  排 P1 而不是 P0：平台 Key + BYOK 两条路已经能让所有人跑通链路，
  自定义端点是"我想接自己私有部署的模型"这一类需求，不挡出片。
- **FR-GW-021**：模型 `available` 状态与下线处理（§5.3）。
- **FR-GW-022**：Provider 合同测试（每个 adapter 一套错误分类断言表）。
- **FR-GW-023**：Provider 健康度、限流、成本指标与看板。
- **FR-GW-024**：第二家视频 Provider（Seedance），验证切段逻辑在
  `max_segment_seconds` 不同的模型间成立。

### 10.3 P2

- **FR-GW-030**：图像编辑能力（图生图 + 多参考图）。
  决策记录 §3.2 明确"不取（M2 后）"：Provider 抽象里没有 `init_image`，
  做它会同时动 Gateway、计费、BYOK。
- **FR-GW-031**：同一能力多家 Provider 并存（今天 `catalog.provider_of()`
  返回的是单值）。
- **FR-GW-032**：ComfyUI Runtime / Node Agent（ADR-003、ADR-004）。冻结中。
- **FR-GW-033**：视频 / TTS 的自定义端点。**不做**——ADR-031 第 5 条明确排除。

---

## 11. 当前缺口与风险

### 11.1 ADR-024 硬约束 1 当前被违反（**最高优先级**）

预估读的模型和 Gateway 实际解析的模型是**两条独立的路**：

- `billing/pricing.py::_shape()` 取 `payload["model_id"]`，取不到就用
  `_DEFAULT_MODEL["image.generate"] = "wan2.2-t2i-flash"`；
- 而出图任务的 payload 在 `consistency/render.py` 里构造，**从来不写
  `model_id`**（`prompt` / `negative_prompt` / `size` / `n` / `seed` / `project_id` /
  `owner_user_id` / `subject_kind` / `subject_ref`，没有模型）；
- Gateway 在 worker 里才按 `projects.model_preference` 解析，可能得到
  `wan2.2-t2i-plus`。

结果：**项目偏好选了 plus，预扣按 flash 的价算**。今天两者
`model_pricing` 的差价不大，所以看不出来；接入视频后差价是数量级的。

修法：把模型解析**提前到建任务时**——`create_task` 之前先按 §6.1 解析出
`model_id`，写进 `input_json`，预估据此算，worker 直接用 `input_json.model_id`
不再重解析。副产品是 §6.4 的溯源天然成立。

### 11.2 其他缺口

- **视频 / TTS 完全没有**，主链路断在出图之后。这是 P0 的全部理由。
- **组织默认层缺失**，用户每建一个项目都要重设一次偏好。
- **没有模型选择按钮**，`/freeflow/models` 只能看不能选，
  项目设置里能选但不带价格。
- **`base_portrait_asset_id` / `base_reference_asset_id` 从不回写**
  （见 `00_CURRENT_STATUS.md` §6）——不属本模块，但候选版本（ADR-033）
  落地时会和"实际用了哪个模型"一起解决。
- **默认价格兜底常量仍在** `billing/service.py::DEFAULT_RULES`。
  它是缺配时的兜底、不是报价依据；对外报价必须以 `pricing_rules` 表为准。
- **合同测试缺失**：换一个 Provider 的错误码映射写错，只会在生产上表现为
  "该 failover 的没 failover"。

---

## 12. 安全、性能与可靠性

- **密钥**：明文只在三个瞬间存在于内存——入库前加密、调用/探测时发给上游、
  读列表时算尾号。不返回前端、不进日志、不进 `input_json`、不进
  `agent_runs.error_detail`。上游 401 常把 Key 原样贴回来，
  所以 BYOK 的错误 message 要过 `probe.redact()`（`_as_byok_error` 已做）。
- **熔断作用域**：`breaker.scope(provider_id, org_id=...)`。BYOK 与平台档
  分开算——他的 Key 被封不该拖垮平台档，平台档熔断也不该拦住他（ADR-027）。
- **不跨 Key 源 failover**：BYOK 失败绝不回落平台 Key（同上）。
- **同账号级错误不换模型**：401 / 欠费换个模型还是同一个答案
  （`_ACCOUNT_LEVEL_CODES`）。
- **自定义端点的出网**：§5.2 第 3 条的 URL 白名单，这是本模块唯一一个
  用户可控的出站地址。
- **临时 URL**：Provider 返回的链接必须转存（DashScope 图片 24 小时过期）。
  视频文件更大，转存要走流式，不能整个读进内存。
- **进程级单例不可就地修改**：`registry()` 是全局的，`_prefer_model` 必须
  在副本上排序，否则一个项目的偏好会泄漏给所有租户
  （`tests/unit/test_gateway_model_preference.py::test_preference_does_not_leak_into_process_registry`）。

---

## 13. 模块依赖

- **依赖**：09（`model_pricing` / `pricing_rules` / `provider_credentials` 与估算）、
  02（`projects.model_preference`）、10（Skill 的 `model_policy.user_selectable`）、
  core（config / errors / logging / crypto / redis）。
- **被依赖**：04 Agent（文本）、06 一致性与出图（图像）、
  12 媒体（视频 / TTS，Wave 2）、08 任务（估算与实际模型写回）。

跨模块一律只调对方 `service` 层（ruff `banned-api` 会拦）。

---

## 14. 迭代计划

1. **Wave 2-1**：能力枚举收敛（§4.2）+ `ModelSpec` 重构（§4.4）+
   模型解析提前到建任务时（§11.1）。这三件是后面所有事的地基。
2. **Wave 2-2**：`video_generation`（万相 i2v）+ `tts`（CosyVoice），
   带 `max_segment_seconds` 与 `duration_ms`。
3. **Wave 2-3**：`org_model_defaults` 表 + 三层解析 + `override_model_id` +
   `GET /model-options` + 前端模型选择按钮。
4. **Wave 2-4**：实际模型与 failover 轨迹的记录与展示（ADR-031 第 7 条）。
5. **Wave 3**：文本自定义端点、模型下线状态、Provider 合同测试与健康度看板。
6. **冻结**：ComfyUI Runtime、图像编辑、同能力多 Provider。

---

## 15. 验收标准和测试

**已可验收（现在就该绿）**

- 切换项目模型偏好不需要改任何业务代码。（`tests/integration/test_model_preference.py`）
- 首选 Provider 故障时能回退，且只结算真实成功的成本。（`tests/unit/test_gateway_model_preference.py::test_preferred_model_still_fails_over`）
- BYOK 失败绝不回落平台 Key；一个 org 的熔断不影响另一个。（`tests/unit/test_gateway_byok_routing.py`）
- 计费打的折与实际用的 Key 一致。（`tests/integration/test_byok_gateway_wiring.py::test_billing_discount_and_actual_key_agree`）
- 日志、`tasks.input_json`、`agent_runs` 中找不到明文密钥。（`tests/unit/test_logging_redaction.py`、`test_probe_redaction.py`）
- 测试环境不访问真实 Provider。（`tests/unit/test_no_live_provider_in_tests.py`）

**Wave 2 新增（做完才算 P0 完成）**

- 同一个镜头，选 flash 与选 plus，`POST /credits/estimate` 与
  `GET /model-options` 给出的数字不同，且与 `tasks` 上实际结算的一致
  （堵住 §11.1）。
- 三层默认：组织默认设 A、项目覆盖设 B、生成前选 C，实际跑的是 C；
  删掉 C 跑 B；删掉 B 跑 A；全删跑目录第一个。**四条都要有用例。**
- 生成前临时选择跑完后，`org_model_defaults` 与 `projects.model_preference`
  **一个字都没变**。
- 视频模型的 `max_segment_seconds` 能在**分镜阶段**读到；
  换一个上限不同的模型，分镜的段数随之变化。
- TTS 结果的 `duration_ms` 非空，且被回写成镜时长。
- 首选模型失败 failover 后，任务结果里的 `model_id` 是**实际那个**，
  `failover_from` 非空，前端显示"实际使用 X（Y 不可用）"。
- 自定义文本端点（P1 交付时）：往 `org_text_endpoints` 存一条，
  `GET /model-options?capability=video_generation` 与 `?capability=tts`
  的返回里**不含**它；`base_url` 填内网地址被拒。
