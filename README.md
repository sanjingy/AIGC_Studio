# AIGC Studio

> An AI content-production workspace that turns a novel or a story idea into a reviewed
> screenplay, character and scene sheets, a storyboard and consistent storyboard images.
> Video, voice-over and per-shot MP4 export are on the roadmap and **not available yet**.
> Licensed under Apache-2.0. Documentation is primarily in Chinese.

面向外部创作者的 AI 内容生产工作台。用户粘贴小说原文或一段创意，系统编排多个 Agent
依次产出剧本、角色档案、场景档案和分镜，并按锁定的画风生成角色、场景与镜头图片。
产品终点是「逐镜带配音的 MP4」，目前只完成到分镜出图这一段。

## 现在能用什么

| 能力 | 状态 |
|---|---|
| 注册 / 登录、个人组织、多租户隔离（`org_id`） | 已实现（无邮箱验证、无找回密码） |
| 项目、故事原文粘贴或导入 `.txt` / `.md` | 已实现 |
| 情节目录 → 剧本 → 角色 → 场景 → 分镜，四道人工确认门 | 已实现 |
| 画风锁定、提示词合成、角色 / 场景 / 镜头出图 | 已实现 |
| 基准图三条来路：AI 生成、从资产库选、本地上传 | 已实现 |
| 资产库（S3 兼容存储直传）、任务中心、SSE 实时进度 | 已实现 |
| Credits 账本（预扣 / 结算 / 释放、幂等、行锁） | 已实现（无支付通道） |
| 模型网关：DeepSeek、阿里云百炼（万相）、OpenAI 兼容 Chat / Responses / Images，失败切换与熔断 | 已实现 |
| 自带 Key（BYOK，密文存储）、项目级模型偏好 | 已实现 |
| 本机会员 CLI 试点：项目显式选择后由你电脑上的 Claude Code / Codex CLI 写文本或出图 | 试点，默认关闭 |
| Skill（YAML 生产模板）上传与校验 | 已实现；**运行时尚未接线** |
| 图生视频、TTS 配音、逐镜 MP4 合成与下载 | 未实现（M2 路线图） |
| 支付、团队成员、运营后台、整集时间线 / 字幕 / BGM | 不在当前范围 |

「已实现」指代码和自动化测试已就位；真实上游生成质量尚未完成系统验收，
废片率等定价参数仍是待测值，**不能据此对外报价**。最新状态以
[`project_docs/00_CURRENT_STATUS.md`](project_docs/00_CURRENT_STATUS.md) 为准。

## 快速开始

需要 Docker（Docker Desktop 或 Docker Engine + Compose v2）和 Node.js 20+。

```bash
cp .env.example .env          # 本地默认值即可启动
docker compose up -d          # postgres / redis / minio / api（启动时自动迁移）/ worker

cd apps/web
npm ci
npm run dev
```

| 服务 | 地址 |
|---|---|
| Web 工作台 | http://localhost:3000 （注册一个账号后进入 `/freeflow`） |
| API 文档 | http://localhost:8000/docs |
| MinIO 控制台 | http://localhost:9001 |

**模型 Key**：不配任何 Key 也能启动、注册、建项目、编辑原文，但生成会明确报
`provider.not_configured`（不会悄悄产出假内容）。要生成，在 `.env` 里填平台 Key
（`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY`），或在工作台「模型」页添加自己的
OpenAI 兼容连接。Mock 文本与占位图只在 `ENV=test`（自动化测试）下启用。
真实调用会产生上游费用；`scripts/validation_slice.py` 不带 `--dry-run` 时也会花钱。

`make help` 列出常用命令（`make up`、`make migrate`、`make test`、`make lint`）。

## 测试与检查

```bash
# 后端（容器内）
docker compose exec api pytest -q
docker compose exec api ruff check . && docker compose exec api ruff format --check .
docker compose exec api mypy apps worker packages agents adapters skills

# 前端
cd apps/web
npm test            # 纯逻辑单测（node --test）
npm run typecheck
npm run build
```

CI（`.github/workflows/ci.yml`）在 PostgreSQL + pgvector、Redis、MinIO 服务上跑同一套后端门禁，
以及前端类型检查与构建。

## 架构

模块化单体：FastAPI + Arq Worker + PostgreSQL / pgvector + Redis + S3 兼容存储；
前端 Next.js 15 + React 19 + TypeScript + Tailwind。

```
apps/api/core/        配置、错误目录、数据库、日志脱敏
apps/api/modules/     auth / project / content / asset / task / realtime / billing /
                      agent / gateway / consistency / prompting / skill / local_runtime
                      —— 跨模块只调用对方 service 层
apps/web/             Next.js 工作台（/freeflow）
apps/local_runner/    本机会员 CLI 连接器（试点）
agents/               Agent 声明（YAML）、注册表与输出 schema
skills/               Skill 生产模板（YAML）与白名单校验
adapters/providers/   上游模型适配器（DeepSeek、DashScope、OpenAI 兼容）
worker/               Arq 任务
migrations/           Alembic 迁移
tests/                单元与集成测试
project_docs/         当前产品、架构与模块文档（唯一入口）
aigc_studio_docs/     ADR（15_ArchitectureDecisions.md）与历史设计资料
```

几条贯穿全局的约束（偏离须先提 ADR）：执行状态只认 `tasks.status`；金额一律 `BIGINT`
最小单位，改余额先 `SELECT FOR UPDATE` 并带幂等键；每个用户侧查询都带 `org_id`，
跨租户一律 404；价格、汇率、废片率走数据库表而非代码常量；第三方 Agent / Skill 只能是
白名单校验过的 YAML。完整说明见 [`CLAUDE.md`](CLAUDE.md) 与
[`project_docs/`](project_docs/README.md)。

## 已知限制

- 产品还没有到成片：视频、配音、逐镜 MP4 尚未实现。
- Skill 运行时未接线，阶段图仍硬编码在编排器里；上传的 Skill 只做校验和存档。
- 本机会员 CLI 是单组织、单项目白名单的试点，生产环境禁止开启。
- 任务取消与 Worker 开始/完成在高并发下有已知竞态；数据库约束会阻止负数预扣余额，
  但已取消任务可能被覆盖回运行中，仍需修复状态更新的并发控制。
- `docker-compose.yml` 只面向本地开发，没有生产镜像、告警和备份恢复方案。
- 前端 ESLint 配置尚未完成，`npm run lint` 不是门禁。
- 大部分文档和界面文案只有中文。

## 安全

- 不要把真实密钥写进代码、日志或 Issue。`.env` 已被 `.gitignore` 排除，
  `.env.example` 里只有本地开发用的占位值；生产环境必须替换 `SECRET_KEY`、
  `CREDENTIAL_ENCRYPTION_KEY`、数据库与对象存储凭据（`ENV=prod` 时使用默认
  `SECRET_KEY` 会拒绝启动）。
- 发现安全问题请**不要**开公开 Issue，按 [`SECURITY.md`](SECURITY.md) 私下报告。

## 许可证

[Apache License 2.0](LICENSE)。移植自其他项目的代码与随仓库附带的开发工具保留各自许可证，
见 [`NOTICE`](NOTICE) 与 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
