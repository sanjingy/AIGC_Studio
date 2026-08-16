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

agents:
  - story
  - character
  - scene
  - script
  - storyboard
  - image
  - video
  - audio
  - editing
  - qa

human_gates:
  - story
  - characters
  - scenes
  - storyboard
  - key_visuals
  - final_video

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
