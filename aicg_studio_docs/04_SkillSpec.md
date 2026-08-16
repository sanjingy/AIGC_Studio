# Skill 协议设计

## 1. Skill 定义

Skill = Prompt + Pipeline + Agent Policy + Tool Policy + Model Preference + Human Gates + Output Schema

## 2. 示例

```yaml
id: skill.novel_to_anime.default
name: 小说转漫剧
version: 1.0.0
status: active

input:
  text: required
  images: optional
  files: optional

route:
  type: NOVEL_TO_ANIME

# Agent 收敛为 4 个（见 03_AgentSystem.md 第 1 节）
# 注意顺序：media 内部必须 TTS 先行（ADR-011）
agents:
  - story
  - visual
  - media
  - qa

# 确定性模块，不是 Agent
pipeline:
  - consistency.freeze      # 角色资产冻结 + 风格锁定
  - timeline.assemble       # 音频优先装配
  - timeline.render         # ffmpeg

# 默认 3 道阻塞门（见 01_ProductSpec.md 第 5 节）
human_gates:
  - setup                   # 故事 + 角色基准立绘 + 场景
  - storyboard              # 剧本 + 分镜 + 预览档视频
  - final                   # 成片

consistency:
  default_tier: L1          # 见 17_ConsistencyEngine.md 第 2 节
  protagonist_tier: L2      # 主角升级（M4 起可用）

render:
  mode: tiered              # 预览档 → 确认 → 定稿档（ADR-015）

capabilities:
  - text_generation
  - image_generation
  - text_to_video
  - text_to_speech
  - audio_generation
  - video_editing

output:
  type: video
```

## 3. 用户上传 Skill

支持上传：

- YAML/JSON Skill 定义
- Prompt 模板
- ComfyUI workflow.json
- 资源包

安全策略：

用户 Skill 不允许未经授权执行任意服务器 shell。

## 4. Workflow -> Skill

用户上传 ComfyUI workflow 后：

1. 解析 workflow JSON
2. 识别输入节点
3. 识别模型依赖
4. 识别输出节点
5. 生成参数 Schema
6. 生成 Skill 草稿
7. 用户确认
8. 发布为个人 Skill

## 5. Skill 生命周期

```text
DRAFT → VALIDATING → PRIVATE → PUBLISHED → DEPRECATED
```

Skill 必须版本化。
