# MVP / Phase Roadmap

## Phase 0：架构骨架

目标：Codex 可以跑起来并形成开发闭环。

- Docker Compose
- PostgreSQL
- Redis
- FastAPI
- Next.js
- Auth
- 基础 Project
- Asset 上传
- Task 生命周期
- OpenAPI
- CI

验收：

```text
docker compose up
→ 登录
→ 创建项目
→ 上传图片/文本
→ 创建任务
→ Worker 执行 mock provider
→ Web 实时看到任务完成
```

## Phase 1：AI Gateway

完成：

- Provider registry
- Model registry
- Capability
- API Key
- OpenAI-compatible adapter
- 基本计费
- Provider failover

验收：

一个模型调用可以完全经过统一 Gateway。

## Phase 2：Agent MVP

完成：

Router → Director → Story → Character → Scene → Storyboard

实现：

- LangGraph
- checkpoint
- approval gate
- retry
- rollback

验收：

用户输入小说后，系统能自动生成并等待：

```text
故事结构
角色
场景
分镜
```

## Phase 3：ComfyUI

完成：

- Local ComfyUI adapter
- Remote ComfyUI adapter
- workflow upload
- workflow validation
- parameter mapping
- execution progress
- result import

验收：

一张图片可以通过 ComfyUI 自动生成并进入 Asset Library。

## Phase 4：Video Pipeline

完成：

- Image-to-video
- Text-to-video
- audio
- TTS
- subtitle
- simple editor

验收：

从分镜自动生成一条完整短视频。

## Phase 5：Node Agent

完成：

- local GPU registration
- heartbeat
- runtime inventory
- secure task pull
- result upload

验收：

用户电脑的 ComfyUI 成为平台可用 Runtime。

## Phase 6：AutoDL

完成：

- account credential
- product sync
- instance create
- boot
- bootstrap
- node register
- usage meter
- stop/release

验收：

用户购买 GPU 租赁后，平台自动创建对应 AutoDL 实例并归还。

## Phase 7：Skill Ecosystem

完成：

- official skills
- custom skills
- workflow → skill
- versioning
- permissions

## Phase 8：商业化

完成：

- top-up
- refunds
- pricing rules
- audit
- admin console
- abuse control
- public API
