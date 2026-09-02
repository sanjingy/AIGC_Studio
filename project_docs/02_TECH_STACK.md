# 技术栈选择

> 最近核对：2026-09-02。口径依据 `DECISIONS_2026-09-02.md` 与 ADR-030 ~ 033。

## 1. 选择原则

技术选型服务于四个目标：快速形成完整生产链、保证异步任务可恢复、保证账务与租户安全、允许未来替换模型和运行节点。当前阶段不以"组件最多"或"微服务数量"衡量先进性。

## 2. 前端

| 技术 | 当前使用 | 选择理由 | 使用边界 |
|---|---|---|---|
| Next.js 15 | 是 | 路由、服务端渲染和前端工程一体，适合工作台 | 不承载长任务执行 |
| React 19 | 是 | 组件化工作台和复杂交互生态成熟 | 服务端数据仍由 API 提供 |
| TypeScript | 是 | 保证 API、状态和组件契约 | 禁止用 `any` 掩盖核心业务类型 |
| Tailwind CSS 4 | 是 | 快速实现统一设计 Token | 不允许页面各自发明颜色和圆角体系 |
| Lucide React | 是 | 统一图标系统 | 不用 Emoji 充当操作图标 |
| `@xyflow/react` | **冻结** | 原型已引入，适合未来可视化工作流画布 | 决策记录 §2：Canvas 搁置，路由从导航隐藏、代码保留、不接后端。**不新增依赖它的功能**，也不因为它已经在 `package.json` 里就当能力 |
| TanStack Query | 建议引入 | 统一缓存、刷新、Mutation 和失效策略 | 替代散落的 `useEffect + fetch` |
| Zustand | 建议引入 | 适合编辑器选区、抽屉等本地状态 | 不保存服务端业务真相 |

### 前端状态分层

```text
服务端状态：项目、任务、资产、余额、审核 → TanStack Query + SSE 校正
路由状态：当前项目、页面、筛选器        → Next.js URL
临时交互：选区、抽屉、草稿              → React/Zustand
```

前端只有一套壳：ADR-030 已定 `/freeflow` 替换 `(app)` 四栏工作台，`(app)` 只留 `/login`。
`mock-data.ts` 与悬浮 AI 助手（只返回本地预设回复）连路由一起摘。

## 3. 后端 API

| 技术 | 当前使用 | 选择理由 |
|---|---|---|
| Python 3.13 | 是 | 当前工程已锁定，类型与异步能力充分 |
| FastAPI | 是 | Pydantic 契约、异步 I/O、OpenAPI 和依赖注入 |
| Pydantic 2 | 是 | 请求校验、结构化 Agent 输出和配置管理 |
| SQLAlchemy 2 Async | 是 | 明确事务边界、异步 PostgreSQL、Repository 模式 |
| Alembic | 是 | 可追踪数据库迁移 |
| structlog | 是 | JSON 日志、Trace ID 和脱敏 |
| httpx | 是 | 调用模型 Provider 与外部 HTTP 服务 |
| 邮件发信（Gmail / QQ 邮箱 SMTP） | **待接入，选型已定**（决策记录 §12 第 2 条） | 邮箱验证与找回密码（决策记录 §6）需要发信。仓库里**目前没有任何发信 / SMTP / 邮件配置代码**，要从零起。落地形状是 `core/mailer` 抽象 + `ENV=test` 强制 Mock，与 Provider 一样不允许测试打真实上游。首批用 Gmail 与 QQ 邮箱的 SMTP（大陆用户默认 QQ），开放注册前换事务型服务，`mailer` 接口不变 |

后端继续采用模块化单体，不拆微服务。拆分条件不是代码行数，而是某个模块出现独立扩缩容、独立安全边界或独立团队所有权，并且当前 service 契约已稳定。

## 4. 数据与基础设施

| 技术 | 当前使用 | 选择理由 | 生产要求 |
|---|---|---|---|
| PostgreSQL 16 | 是 | 业务真相、事务、行锁、JSONB | 主备、备份、迁移演练 |
| pgvector | 是 | 角色/场景 embedding 和一致性检索 | 不作为泛用 RAG 数据库。注意：目前 `reference_embedding` 恒空（写它的 `score_shot` 没有调用方） |
| Redis 7 | 是 | Arq 队列、SSE 事件流、短期状态 | AOF、容量与故障恢复监控 |
| Arq | 是 | 与 Python async 技术栈一致，MVP 足够轻量 | Job 必须幂等，真相仍在 PostgreSQL。视频/TTS/渲染接入后要分独立队列，不与轻量文本任务争抢 |
| MinIO | 是 | 本地 S3 兼容对象存储 | 生产替换为 S3/OSS/COS/R2 均可 |
| Docker Compose | 是 | MVP 一键拉起数据库、队列、对象存储、API、Worker | 正式环境需补反向代理、TLS、备份和滚动升级 |

## 5. AI 与媒体

| 能力 | 当前方案 | 选择原因 |
|---|---|---|
| Agent 定义 | YAML + Pydantic Schema | 可审查、可热加载、不能上传任意代码 |
| Skill 定义 | YAML + 白名单 Handler | 限制第三方能力边界。运行时接线按决策记录 §9 冻结 |
| Provider 适配 | 自定义 Adapter 接口 | 解耦业务、供应商和模型 |
| **供应商来源** | **代码内固定目录**（`gateway/catalog.py`），每家 adapter 声明能力、模型列表、定价键名、以及视频的单段最大时长 | ADR-031 第 5 条。用户"添加供应商"= 给目录里的某家填 Key（BYOK） |
| **文本能力的自定义端点** | **允许一个 OpenAI 兼容端点** | ADR-031 第 5 条的唯一例外：OpenAI 兼容协议在文本上是事实标准 |
| **视频 / TTS 的自定义端点** | **不允许** | 各家请求形状差异太大，"填个 URL 就能用"做不到，做了就是假入口 |
| 能力维度 | `text_generation` / `image_generation` / `video_generation` / `tts` 四个 | ADR-031 第 2 条。图像编辑（图生图）不在本轮，加能力要另提 ADR |
| 文本模型 | DeepSeek（已接） | 成本低、结构化输出能力足够 |
| 图像模型 | DashScope 万相（已接） | 当前已打通出图纵向切片 |
| 视频模型 | **待接入**：第一条打通用 DashScope 万相图生视频；Seedance 的 Key 由负责人后续提供 | 决策记录 §3.3。adapter 必须声明**单段最大时长**（万相约 5 s、Seedance 15 s 以上），它是分镜阶段的输入 |
| TTS | **待接入**：DashScope CosyVoice | 音频优先（ADR-011）：先合成配音拿到真实时长，再反推镜时长 |
| 媒体处理 | **逐镜合成：段拼接 + 配音混音**（ffmpeg / ffprobe）。**镜像目前没有 ffmpeg**——`infra/docker/api.Dockerfile` 只装了 `build-essential` 和 `curl`，接入前必须先加 | ADR-032：终点是每镜一条带配音的 MP4，不做整集时间线、字幕、BGM、转场。ffprobe 用来读音视频真实时长 |
| ComfyUI / 自建 GPU | 不做 | 决策记录 §1 明确不做自建 GPU/ComfyUI |

## 6. 测试与质量工具

| 工具 | 当前状态 | 目标 |
|---|---|---|
| pytest / pytest-asyncio | 已使用 | 单元、集成和真实依赖测试分层 |
| Ruff | 已配置 | 格式、常规质量和跨模块禁用导入 |
| mypy strict | 已配置 | Python 核心代码严格类型检查 |
| TypeScript `tsc --noEmit` | 可用 | 前端类型门禁。决策记录 §8：GitHub Actions 增加前端 `typecheck + build` |
| ESLint 9 | 配置待修复 | flat config 修好再进 CI，不阻塞本轮（决策记录 §8） |
| Agent Eval | 已有基础集 | 硬地板是结构化输出 schema 样例；Router 黄金集与内容质量评分尚未实现 |
| Playwright | 建议引入 | 决策记录 §8：黄金路径进 Wave 1 |

## 7. 明确不选

- 当前不拆 Kubernetes 和微服务：会提前支付分布式复杂度。
- 当前不引入 Kafka：Redis Stream + Outbox 足以支撑 MVP。
- 不把 LangGraph checkpoint 当持久业务状态：任务状态归 PostgreSQL。仓库里目前也没有 LangGraph。
- 不让前端直接调用模型 Provider：密钥、计费、审计全部经过后端。
- 不自研视频编解码：统一使用 FFmpeg，且**只接受结构化 Render Spec**，禁止 Agent 生成 shell 命令（ADR-032 第 6 条）。
- 不以通用低代码画布替代明确的创作主流程。
- 不做自建 GPU / ComfyUI Runtime（决策记录 §1）。
