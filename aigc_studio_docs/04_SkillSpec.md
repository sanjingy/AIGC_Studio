# Skill 协议设计

> 2026-08-18 修订：由概念草案改为与 `skills/spec.py` 一致的实现文档（ADR-020）。
> 原草案的 `agents: [story, visual, media, qa]` + `pipeline: [...]` 两段式
> 表达不了"哪一步停下来等人""哪一步的图谁来审""产出落到哪个文件夹"，
> 而这三件事恰恰是生产线与提示词集合的区别。

## 1. Skill 定义

**Agent 是一次 LLM 调用，Skill 是一条生产线。**

```text
Skill = 阶段序列 + 门 + 审查策略 + 模型策略 + 导出布局
```

Skill 和 Agent 一样**只能是声明，不能是代码**。第三方 Skill 跑在我们的
服务器上、花用户的 Credits、读用户的项目数据，还会往用户的本地目录写文件。

## 2. 四种阶段

| kind | 含义 | 必填 | 约束 |
|---|---|---|---|
| `agent` | 一次 LLM 调用 | `role` | `agent` 可钉死具体 spec id；可 `revisable` |
| `compute` | 确定性代码 | `handler` | 只能引用 `KNOWN_HANDLERS` 白名单 |
| `generate` | 出图/出音/出视频 | `capability`、`review` | 见第 4 节 |
| `gate` | 阻塞门 | `gate` | 只能是 `KNOWN_GATES` 五道之一 |

写错 kind 时立刻报错，不静默忽略——`kind: compute` 的阶段声明了 `role`
会直接校验失败。静默忽略会让人以为配置生效了，实际没有。

**只有 `agent` 阶段可以 `revisable`**（聊天修订）。确定性阶段没有可修订的
东西；生成阶段的返工走 `review`，不走对话。

## 3. 门

全集：`script` / `assets` / `anchor` / `storyboard` / `final`。

`required: true` 的门用户不能关。默认 Skill 里 `script` 与 `final` 是
required，其余三道可关——**门的数量是策略，不是常量**（ADR-021）。

```python
spec.effective_gates(review_mode="auto")  # → ["script", "final"]
spec.effective_gates()  # → 五道全在
```

新增一道门需要 ADR。

## 4. 审查策略（ADR-023）

```yaml
review:
  default: assisted        # manual | assisted | auto
  allow_auto: true
  metric: character_similarity
  threshold_key: consistency.character_similarity   # 只写键名，数值进库
  max_attempts: 3          # 自动重试的硬上限，1~5
```

- 允许 `auto` 就**必须**声明 `metric` 和 `threshold_key`，否则没有判定依据
- `assisted` 也需要 `metric`——没有指标就没有"筛"，它会退化成
  一个名字好听的手动模式
- 阈值不许写数值：相似度阈值仍在标定中，写死在会被分发的 YAML 里
  等于把待定的数字冻起来

## 5. 导出布局（ADR-022）

阶段的 `export` 字段声明产出落到本地目录的哪个子目录。
对象存储仍是唯一真相，本地目录是单向镜像。

路径按段白名单校验：必须相对、不含 `..`、最多 3 层、
只允许 `\w`/中文/`-`/`.`/空格。**不做"替换掉危险字符"式的清洗**——
清洗永远漏，白名单不会。

## 6. 默认 Skill

`skills/builtin/novel_to_anime.yaml`，主线：小说 → 5 分钟悬疑漫剧。

```text
一、文本层    plot_index → estimate → screenplay → 🚪script
二、语音      voice                                  ← 音频优先，排在视觉之前
三、视觉资产  style_lock → character_sheets → scene_sheets
              → portrait_prompts → portraits
              → scene_prompts → scene_concepts → 🚪assets → freeze
四、分镜      storyboard → durations → batches → anchors → 🚪anchor
              → shot_prompts → keyframes → 🚪storyboard
五、成片      clips → assemble → render → 🚪final
```

**`voice` 在视觉资产之前**是刻意的：剧本定稿时台词就定了，
此刻合成能拿到每句真实时长，`durations` 和 `batches` 从第一次算就是真值，
不需要事后回填（ADR-011 / ADR-020）。

## 7. 用户上传 Skill

支持上传：

- YAML Skill 定义（走同一套校验，内置不享受特权）
- Prompt 模板
- ComfyUI workflow.json
- 资源包

安全边界由 spec 本身保证，不靠运行时额外判断：
处理器白名单、导出路径白名单、门白名单、能力白名单、重试次数上限。

## 8. Workflow → Skill

用户上传 ComfyUI workflow 后：

1. 解析 workflow JSON
2. 识别输入节点
3. 识别模型依赖
4. 识别输出节点
5. 生成参数 Schema
6. 生成 Skill 草稿
7. 用户确认
8. 发布为个人 Skill

## 9. Skill 生命周期

```text
draft → active → deprecated
```

Skill 必须版本化，id 形如 `skill.novel_to_anime.v1`。
`for_route()` 只返回 `active` 的。
