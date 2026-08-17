# AIGC Studio — 开发者/AI 会话上手指南

面向普通用户的 AI 内容生产操作系统：用户描述目标，系统编排 Agent 完成
从小说到成片的整条生产链。主线是**小说 → 5 分钟悬疑漫剧**。

---

## 先读什么

不要一上来就改代码。按这个顺序读，大概 20 分钟：

| 顺序 | 文档 | 为什么先读它 |
|---|---|---|
| 1 | `aigc_studio_docs/19_UnitEconomics.md` | 先搞清楚钱怎么算。**盈亏平衡 = 每天 16 部成片**，所有优先级都用它衡量 |
| 2 | `aigc_studio_docs/17_ConsistencyEngine.md` | 核心技术难点，也是唯一的护城河。§8.1 有实测数据 |
| 3 | `aigc_studio_docs/15_ArchitectureDecisions.md` | 19 条 ADR，全部已定决策 |
| 4 | `aigc_studio_docs/12_MVP_Roadmap.md` | 当前在哪个里程碑 |
| 5 | `reviews/2026-08-16_架构评审与修订记录.md` | 为什么是现在这个设计 |

---

## 跑起来

需要 Docker Desktop 运行中。

```bash
cp .env.example .env      # 首次；Provider Key 见下方说明
docker compose up -d      # 后端 5 个容器
cd apps/web && npm run dev
```

| 服务 | 地址 |
|---|---|
| 前端 | http://localhost:3000 |
| API 文档 | http://localhost:8000/docs |
| MinIO 控制台 | http://localhost:9001 |

`npm run dev` 报 `EADDRINUSE` 说明已经有一个在跑，直接用即可；要重启先
`npx kill-port 3000`。

**Provider Key** 在 `.env`（已 gitignore，从未入库）。没有 Key 也能跑通全链路
——`get_provider()` 会自动退回 Mock。

常用命令：

```bash
docker compose exec api pytest -q                       # 全量测试
docker compose exec api ruff check . && docker compose exec api mypy apps worker packages agents adapters skills
docker compose exec api alembic revision --autogenerate -m "xxx"
docker compose exec api alembic upgrade head
docker compose exec api python scripts/validation_slice.py --dry-run
```

---

## 当前进度

**M1 已完成**（8 步，12 个 commit，321 个测试全绿）：

| 步骤 | 内容 |
|---|---|
| S1 | 工程骨架：FastAPI 单体 + Arq Worker + PG/pgvector + Redis + MinIO |
| S2 | 账号体系：argon2、httpOnly Cookie、refresh 轮换 + 重放检测 |
| S3 | 项目与资产：预签名直传、MIME 白名单、跨租户 404 |
| S4 | 任务流水线：状态机、幂等、Transactional Outbox、SSE |
| S5 | 计费 Ledger：reserve/settle/release、行锁、四层熔断 |
| S6 | AI Gateway：DeepSeek + 万相、failover、熔断、真实价格入库 |
| S7 | Agent 编排：声明式 AgentSpec、可插拔第三方 Agent、3 道审核门 |
| S8 | 一致性引擎：风格锁定、提示词合成、embedding 度量、验证切片 |

**S9 Skill 层（M1 之后补，ADR-020~024）**：`skills/` 声明式生产模板。
默认 Skill `skill.novel_to_anime.v1` 把主线拆成 26 个阶段 + 5 道门，
配套 5 个新 Agent（情节目录 / 剧本改编 / 角色档案 / 场景档案 / 分镜）。
**只有声明和校验，运行时尚未接线**——所以它现在是 `draft`，
`orchestrator.py` 仍走原来那条硬编码的 5 阶段路径。

**前端**：`/login`、`/dashboard`、`/projects/[id]`、`/tasks` 已接真实接口。
出图有后端无 UI。

**M2 待办**（"出片"）：TTS → 音频优先时间线 → ffmpeg 合成，目标产出第一条完整成片。
详见 `12_MVP_Roadmap.md`。

**一件悬着的事**：真实废片率仍未测出。验证切片跑出的 3.33 无效
（它把构图差异计成了废片）。`pricing_rules.image_retry_factor = 250` 还是拍值，
**不可用于对外报价**。需要人工逐张判可用性，10 张图在 `validation/`。

---

## 不可违反的规则

违反了 CI 会拦下，或者会造成很贵的后果。**偏离必须先提 ADR。**

| 规则 | 后果 |
|---|---|
| 价格、汇率、废片率不写成代码常量，走 `model_pricing` / `pricing_rules` | 上游一调价就要改代码发版。DeepSeek 2026-08-17 涨了 350% |
| 执行状态只认 `tasks.status`，不在别处并行维护 | 多份真相必然互相矛盾 |
| 金额一律 `BIGINT` 最小单位，禁止浮点 | 浮点存钱是财务事故经典来源 |
| 改余额必先 `SELECT FOR UPDATE`，每笔带幂等键 | 并发丢更新 / 重复扣费 |
| 每个用户侧查询都要带 `org_id` | 跨租户数据泄露 |
| 跨租户访问返 404 不返 403 | 403 会确认资源存在，可枚举 |
| 跨模块只调对方 `service` 层 | ruff banned-api 会拦 |
| 包 `__init__.py` 不导入 router | 会撞循环导入 |
| 密钥不进代码、不进日志、不进 `input_json` | — |
| Agent 不许自己写风格词 | 画风漂移头号来源 |
| 第三方 Agent / Skill 只能是 YAML，不能是代码 | 等于把服务器交出去 |
| Skill 的 `handler` 只能取白名单里的，`export` 路径按段白名单校验 | 前者是任意能力，后者会往用户磁盘任意位置写 |
| 阈值、废片率不写进会被分发的 YAML，只写键名 | 待定的数字冻进发布物，改数要发版 |
| 新 Agent 必须有 eval 用例才能上线 | 提示词退化不会让测试变红 |

---

## 踩过的坑

都是这个项目里真实踩过的，重复踩会浪费很多时间。

**环境与工具链**

- `os.environ.setdefault()` 在 compose 已注入该变量时**不生效**。踩过两次：
  一次让测试连错 MinIO 地址，一次让**整个测试套件在打真实上游花钱**。
  测试里改环境变量一律用赋值，且之后要 `get_settings.cache_clear()`。
- compose 给每个带 `build:` 的服务建**独立镜像**。api 和 worker 共用
  `image: aigc-studio-backend`，否则改依赖只重建一个，出现"api 好好的 worker 崩了"。
- Docker Desktop 内置 DNS 对 AAAA 查询不稳定，`getaddrinfo` 整体失败
  （能 ping 通但 httpx 连不上）。compose 里显式指定了 DNS。
- worker 会继承 Dockerfile 的 HTTP 健康检查但它不跑 HTTP 服务，
  已 `healthcheck: disable: true`。

**Python / 框架**

- pydantic-settings 在**读环境变量的源层**就对 list 字段做 JSON 解析，
  `mode="before"` 校验器轮不到执行。要加 `NoDecode`。
- `computed_field` 默认进 `repr()`，数据库连接串里是明文密码。要 `repr=False`。
- 自定义校验器抛 `ValueError` 时，pydantic 把异常对象放进 `errors()` 的 `ctx`，
  直接序列化会让 422 变 500。
- redis-py 的 `Redis` / `ConnectionPool` **不是泛型**（已废弃的 types-redis 才是），
  写 `Redis[str]` 能过 mypy 但导入就炸。
- `rollback()` 后读 ORM 属性会触发同步 refresh，在 async 上下文抛
  `MissingGreenlet`，把真正的业务错误掩盖成 500。要在 try 前取出需要的值。
- Alembic 不为独立 `Sequence` 生成 `CREATE SEQUENCE`，也不为 pgvector 类型
  生成 import。前者手工补，后者已加 `render_item` 钩子。
- `lambda` 捕获变量、`partial` 捕获值。工厂函数里共用变量名 + lambda =
  **DeepSeek 拿着万相的 Key 去请求**。
- arq 的 `WorkerCoroutine` 协议按参数名匹配，第一个参数必须叫 `ctx`。

**上游 Provider**

- DeepSeek `response_format: json_object` **要求提示词里出现 "json"**，
  否则 400。已由 runner 统一注入，第三方 Agent 作者不需要知道。
- `deepseek-v4-*` 是推理模型，思考 token 计入输出预算。预算给小了会返回
  **空内容且不报错**。同样的结构化任务 `deepseek-chat` 只用 11 个 token，
  V4 要 60 个——Router 分类这类活儿不需要推理。
- DashScope 默认**重写提示词**（`actual_prompt`），会覆盖系统级风格锁定。
  已关 `prompt_extend`。
- DashScope 图片链接**只有 24 小时有效期**，必须转存自有对象存储。

**业务逻辑**

- 预扣和结算的 `attempt` 编号必须一致。`begin_execution` 会自增 attempt，
  差一位会导致**任务成功了但钱没扣**，且不报任何错。
- 中文提示词清洗要**同时处理全角标点**，只清半角等于没清。

**测试**

- Worker 里的常驻中继会与测试并发跑，断言要写成"最终一致"而不是
  "本次调用搬了几条"。
- 幂等键全局唯一，测试里不能写死，否则跨轮次残留会 409。
- SSE 长连接不要用 ASGITransport 测——流式行为与真实 HTTP 有差异，
  只会得到关于测试工具的结论。拆成部件测 + 真实 HTTP 手工验证。

---

## 代码地图

```
apps/api/core/          配置、错误目录、DB、Redis、日志脱敏、ORM 基座
apps/api/modules/       auth / project / asset / task / realtime /
                        billing / agent / gateway / consistency
                        —— 每个模块只通过 service.py 对外
agents/                 Agent spec（YAML）+ registry + 输出 schema
  builtin/              平台内置，随代码发布
  custom/               第三方，丢 YAML 进去即生效，热加载
skills/                 Skill spec（YAML）+ registry —— 生产模板层
  builtin/              novel_to_anime.yaml 是主线默认 Skill
  custom/               第三方，同上
adapters/providers/     DeepSeek / DashScope
worker/                 Arq Worker + jobs
scripts/                validation_slice.py（会花真钱）
migrations/             Alembic
design-system/          前端设计系统 MASTER.md + 对比度校验脚本
validation/             10 镜验证切片的图与报告
```

`.claude/skills/ui-ux-pro-max/` 是装进来的第三方设计技能（MIT），
改前端时可用它检索配色/字体/UX 规范。

---

## 改动纪律

1. 文档是唯一真相。偏离文档的实现**先提 ADR** 再改代码。
2. 每个功能按 `Domain model → API contract → Service → Worker → Adapter → UI → Tests → Docs` 走。
3. 提交前跑全量门禁：`ruff check` + `ruff format --check` + `mypy` + `pytest`。
4. 外部 Provider 一律先有 Mock，且 `ENV=test` 时强制走 Mock。
