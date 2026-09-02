# 14 平台运维与可观测性

> 状态：**部分实现**（本地 Compose 全链路可跑、CI 后端门禁齐；生产化、集中可观测性、备份恢复都没有）
> 优先级：**P1**（只有"CI 前端地板"和"ffmpeg 进镜像"两条是 P0——它们挡住产品闭环；其余生产化项按决策记录 §9 不进 P0）
> 负责人：待定
> 最近核对：2026-09-02
> 权威顺序：[DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §8 / §9 > 当前 `docker-compose.yml` / `.github/workflows/ci.yml` / `infra/docker/` > `aigc_studio_docs/02_Architecture.md`、`11_ProjectStructure.md`、`13_CodexDevelopmentGuide.md`

---

## 1. 模块目标与边界

保证 Web、API、Worker、PostgreSQL、Redis、对象存储和上游 Provider 在开发、
测试、（未来的）生产环境里**可部署、可观察、可恢复**。

**拥有**：容器与镜像、Compose 与环境矩阵、CI 流水线、健康探针、日志与 Trace、
迁移的发布方式、备份与恢复策略。

**不拥有**：任何业务状态语义。本模块不改 `tasks.status`、不改账务、不改
Credits 规则；它只保证承载这些语义的进程活着、能观测、能恢复。

**优先级边界**（[决策记录](../DECISIONS_2026-09-02.md) §9）：四把尺子里只把
「① 产品闭环到逐镜 MP4」变绿，「③ 工程门禁」**只修挡住 ① 的部分**。
因此本模块里的生产化项（TLS、集中指标、IaC、备份演练、K8s）**一律 P1 / P2**，
不写成 P0——把它们提前会挤掉出片的工期，而今天平台还没有外部用户。

---

## 2. 用户与使用场景

今天本模块的"用户"是开发者和 AI Worker，不是运维团队。

1. 新克隆仓库的人（或新开的 Worker 终端）按文档三条命令把全链路跑起来。
2. 改完代码在本地跑与 CI 同一套门禁，不出现"本地绿 CI 红"。
3. 一次任务失败，能用一个 Trace ID 从 API 日志串到 Worker 再串到 Provider 调用。
4. 数据库改了 schema，能确认 `upgrade` 和 `downgrade` 都不炸。
5.（未来）生产上线一次不重复执行任务、不丢账、不泄露密钥。

---

## 3. 当前真实能力

状态词按 [决策记录](../DECISIONS_2026-09-02.md) §0。

| 能力 | 状态 | 证据 |
|---|---|---|
| Compose 拉起 5 个容器：postgres(pgvector/pg16) / redis(7-alpine) / minio / minio-init / api / worker | 已实现 | `docker-compose.yml` |
| api 与 worker **共用一个镜像** `aigc-studio-backend` | 已实现 | `x-backend` 锚点里显式 `image:`；不指定会各建一个，出现"api 好好的 worker 崩了" |
| Python 版本锁定 3.13 | 已实现 | `infra/docker/api.Dockerfile`（注释：3.14 过新，AI 生态库滞后） |
| 依赖单独成层，改代码不触发重装 | 已实现 | Dockerfile 先 `COPY pyproject.toml` 再 `pip install -e .[dev]` |
| 显式 DNS（绕开 Docker Desktop 的 AAAA 查询问题） | 已实现 | `dns: [223.5.5.5, 119.29.29.29, 8.8.8.8]`；不加会表现为"能 ping 通但 httpx 连不上" |
| worker 关掉继承来的 HTTP 健康检查 | 已实现 | `healthcheck: disable: true`（worker 不跑 HTTP，探针必然失败并掩盖真实问题） |
| MinIO 桶自动创建（用轮询而不是探针） | 已实现 | `minio-init` 服务 + `depends_on: service_completed_successfully` |
| 三个持久卷 `pgdata` / `redisdata` / `miniodata`，Redis 开 AOF | 已实现 | `docker-compose.yml` volumes / `--appendonly yes` |
| Alembic 在 API 启动时 `upgrade head` | 已实现（**但不适合生产**） | api 的 `command`；多副本会并发跑迁移 |
| `/healthz` 存活探针 | 已实现 | `apps/api/main.py:158`，不查依赖 |
| `/readyz` 就绪探针 | 部分实现 | `main.py:164` 只探 **PostgreSQL + Redis**，**不探对象存储**；S3 挂了服务仍报 ready |
| `X-Trace-Id` 贯穿：请求头透传或生成，回写响应头，进日志与错误 payload | 已实现 | `main.py:69` / `:77`；错误 payload 带 `trace_id` |
| 结构化日志 + 敏感字段脱敏 | 已实现 | `apps/api/core/logging.py`（`SENSITIVE_KEYS` + `_REDACTED` 递归 scrub） |
| 生产模式输出 JSON 日志 | 已实现 | `configure_logging(json_output=settings.is_production)` |
| 定时维护任务（回收超时未完成的上传） | 已实现 | `worker/jobs/maintenance.py::purge_abandoned_uploads`，`worker/main.py` 里 `cron(minute=17)` |
| 发件箱中继随 Worker 启动 | 已实现 | `worker/main.py::startup`（API 会水平扩容，多副本中继没必要） |
| `ENV=test` 强制走 Mock Provider | 已实现 | `gateway/probe.py:118`、`agent/llm.py::get_provider()` |
| 测试分层 unit / integration / eval | 已实现 | `tests/unit` 23 个 `test_*.py`、`tests/integration` 24 个、`tests/eval`（`eval_suite.py`、`router_golden.yaml`、`agent_evals/`） |
| CI 后端门禁 | 已实现 | `.github/workflows/ci.yml` 单个 `backend` job：ruff check + ruff format --check → mypy（`apps worker packages agents adapters skills`）→ alembic `upgrade head` / `downgrade base` / `upgrade head` → `pytest --cov` |
| CI 前端门禁（typecheck / build） | **未实现** | ci.yml 里没有任何 node / npm 步骤 |
| E2E（Playwright） | 未实现 | 仓库里没有 E2E 套件 |
| 镜像构建与漏洞扫描进 CI | 未实现 | — |
| 集中 Metrics / Tracing / 告警 | 未实现 | 没有 Prometheus / OTel 依赖；`X-Trace-Id` 只进日志，没有采集端 |
| 生产 TLS / 反向代理 / 限流 | 未实现 | — |
| 备份、PITR、恢复演练 | 未实现 | 只有 Docker 卷 |
| Web 的生产容器 | 未实现 | `apps/web` 只有 `npm run dev`，没有 Dockerfile |
| IaC、staging 环境 | 未实现 | — |
| 账务自查 | 部分实现 | `billing/service.py:390::audit()` 存在，但**没有调度、没有告警**，没人定期跑它 |

---

## 4. 功能需求

### 4.1 P0（挡住"逐镜 MP4"，本轮必须做）

| 编号 | 需求 | 依据 |
|---|---|---|
| FR-OPS-001 | **CI 增加前端 job：`npm ci` → `npm run typecheck` → `npm run build`**（工作目录 `apps/web`）。**不加 `lint`**——`apps/web` 下没有任何 ESLint 配置文件，加了必然红；等 flat config 修好再进 | 决策记录 §8 |
| FR-OPS-002 | **ffmpeg 进后端镜像**：`infra/docker/api.Dockerfile` 的 `apt-get install` 里加 `ffmpeg`。因为 api 与 worker 共用 `image: aigc-studio-backend`，**必须一起重建**，不能只 build worker | ADR-032；合成阶段跑在 Worker 上 |

这两条之外，本模块**没有 P0**。生产化项按决策记录 §9 全部押后。

### 4.2 P1

| 编号 | 需求 |
|---|---|
| FR-OPS-010 | Playwright 黄金路径 E2E 进 CI（登录 → 建项目 → 剧本确认 → 分镜确认 → 出图），先作为独立 job，稳定后再设为必须通过（决策记录 §8「进 Wave 1」） |
| FR-OPS-011 | 迁移从"API 启动时自动 upgrade"拆成**独立发布步骤**：先备份 → 再 `upgrade` → 再滚动启动应用。多副本环境下当前做法会并发跑迁移 |
| FR-OPS-012 | `/readyz` 增加对象存储探测。视频与 MP4 全部落 S3，S3 不通时服务不该报 ready |
| FR-OPS-013 | 队列拆分后的部署形态：按 [`08_TASK_REALTIME.md`](08_TASK_REALTIME.md) §6 起多个 Worker 服务（`default` / `video` / `render`），并**把发件箱中继固定在其中一个**——多副本 Worker 各起一份中继会重复投递同一批事件 |
| FR-OPS-014 | 恢复 lint 门禁：补 ESLint 9 flat config，再把 `npm run lint` 加进 FR-OPS-001 的 job |
| FR-OPS-015 | 固定第三方镜像版本：`minio/minio:latest` 和 `minio/mc:latest` 换成固定 tag（`latest` 让"昨天能跑今天不能跑"无法复现） |
| FR-OPS-016 | `billing.audit()` 接定时调度 + 不平时告警（现在有函数无调度） |
| FR-OPS-017 | 基础指标：API 的 QPS / P95 / 5xx / DB pool、Worker 的队列深度与等待时长、Provider 的延迟 / 错误率 / failover 率 / 花费。先落到日志字段可聚合，不强求上 Prometheus |
| FR-OPS-018 | 新增顶层目录时同步改 Compose 挂载（见 §11.1），或写一条 CI 检查防止再犯 |

### 4.3 P2

| 项 | 说明 |
|---|---|
| 集中 Metrics / Tracing / 告警平台（Prometheus + OTel） | 有真实用户后再上 |
| staging 环境 + 小额度真实 Provider 定时验证 + 硬预算 | 同上 |
| 生产 TLS、HSTS、反向代理限流、KMS / Secret Manager | 同上 |
| PostgreSQL 每日全量 + WAL/PITR、对象存储版本策略、季度恢复演练 | 需要先定 RPO / RTO，而 RPO / RTO 取决于商业化承诺 |
| 镜像 / 依赖 / 基础设施漏洞扫描进 CI | — |
| Web 生产容器与 IaC | — |
| Kubernetes | **商业化之后再评估，不以技术偏好提前迁移** |

---

## 5. 环境矩阵

```text
local        Compose + MinIO；Provider Key 可有可无（无 Key 自动退回 Mock）
test         隔离 PG / Redis；ENV=test 强制 Mock，禁止打真实上游
staging      与生产同拓扑，小额度真实 Provider + 硬预算上限     （未实现）
production   托管 PG / Redis / 对象存储，或受控自建            （未实现）
```

- 配置一律走环境变量；`.env` 已 gitignore，从未入库；`.env.example` 是唯一模板。
- **`ENV=test` 是硬约束**：CI 的 `backend` job 里 `ENV: test`，Provider 一律 Mock。
  这条不是风格问题——`os.environ.setdefault()` 在 Compose 已注入变量时不生效，
  曾经让**整个测试套件在打真实上游花钱**（见 §11.2）。
- 生产密钥进 KMS / Secret Manager，不进镜像、不进日志、不进 `input_json`。

---

## 6. 构建、部署与迁移

- **一个后端镜像**：`image: aigc-studio-backend`，api 与 worker 共用。
  加系统依赖（如 ffmpeg）时两个服务一起重建。
- 生产用不可变镜像，**不挂源码 volume、不开 `--reload`**。今天的 Compose
  两样都用了，因为它是开发环境。
- **迁移**（FR-OPS-011）：今天由 api 容器的 `command` 在启动时 `alembic upgrade head`。
  单副本没问题，多副本会并发跑。生产要拆成独立步骤：备份 → upgrade → 滚动启动。
- 迁移必须可回滚，或有明确的 forward-fix 方案。CI 已经跑
  `upgrade head → downgrade base → upgrade head`，回滚路径是被验证过的。
- Worker 升级前停止拉新任务，等待或移交运行中的任务；否则会出现"任务成功了但
  结算落在旧进程"。
- 前后端 API 契约至少支持一个滚动升级窗口。

---

## 7. 可观测性

**今天有的**：结构化日志（生产 JSON）、敏感字段脱敏、`X-Trace-Id` 贯穿请求与
错误响应、`tasks` 与 `agent_runs` 两张表本身就是可查询的执行记录。

**今天没有的**：任何采集端。`X-Trace-Id` 只写进 stdout，没有地方能按 trace
把 API、Worker、Provider 三段串起来查——只能 `docker compose logs | grep`。

需要的指标（FR-OPS-017，按重要性排序）：

| 面 | 指标 | 为什么这条重要 |
|---|---|---|
| Provider | 延迟、错误率、限流、**花费**、failover 率、临时 URL 转存失败率 | 这里直接对应真金白银；DashScope 图片链接只有 24 小时有效期，转存失败等于资产丢了 |
| Worker | 队列深度、等待时长、运行时长、成功率、重试次数、失联任务 | 视频任务动辄几分钟，堆积没有告警就只能靠用户投诉发现 |
| Billing | 预扣未结算、流水不平、日消费、毛利异常 | `audit()` 已有实现，缺调度与告警（FR-OPS-016） |
| API | QPS、P50/P95/P99、5xx、DB pool、SSE 连接数 | — |
| Storage | 容量、pending 垃圾、下载失败、生命周期清理 | 配额走 `pricing_rules.user_storage_quota_bytes`，不是代码常量 |

端到端：project / task / run / asset / 账务流水共用同一个 Trace / Correlation ID。

---

## 8. 备份与恢复（P2，但先把口径写下来）

- PostgreSQL：每日全量 + WAL / PITR。**RPO 24 小时 / RTO 4 小时**（决策记录 §12.2）：每日全量备份 + 对象存储版本控制，开放注册前做一次恢复演练。
  用户承诺什么，不是工程能单方面定的。
- 对象存储：版本化或跨区域复制，配生命周期策略。生成的图片和视频重做要花钱，
  丢了不是"重跑一下"就能补的。
- Redis：**不是业务真相**（`tasks` + `outbox_events` 才是）。整个丢掉重启后
  仍能恢复出正确状态；AOF 只是减少队列恢复成本。
- 每季度做一次真实恢复演练。只验证"备份文件存在"不算演练。

---

## 9. CI 质量门

### 9.1 现状（一个 job）

```
backend（ubuntu-latest，Python 3.13，服务容器 pgvector/pg16 + redis:7-alpine，ENV=test）
  ruff check . && ruff format --check .
  → mypy apps worker packages agents adapters skills
  → alembic upgrade head → downgrade base → upgrade head
  → pytest --cov=apps --cov=worker
```

### 9.2 本轮地板（决策记录 §8）

**只加一个 `frontend` job，只有两步**：

```yaml
frontend:
  runs-on: ubuntu-latest
  defaults: { run: { working-directory: apps/web } }
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4      # 带 npm 缓存
    - run: npm ci
    - run: npm run typecheck           # tsc --noEmit
    - run: npm run build               # next build
```

- **不加 `lint`**：`apps/web` 下没有任何 ESLint 配置文件（`package.json` 里有
  `lint: eslint .` 脚本，但 flat config 没写完），加进去必然红。
  决策记录 §8 原话："`lint` 等 flat config 修好再进"。
- **不加 E2E 到地板**：Playwright 黄金路径进 Wave 1（FR-OPS-010），
  先做独立 job，稳定后再设必须通过。
- **CI 只当地板不当天花板**：合并后仍由 Lead 手动验收，不通过打回同一个 Worker
  返工（决策记录 §8）。

### 9.3 目标流水线（P1 / P2，不是本轮）

```
ruff → mypy → pytest unit → integration → agent eval
  → npm typecheck + build（本轮）→ eslint（FR-OPS-014）
  → Playwright 黄金路径（FR-OPS-010）
  → 镜像构建 + 漏洞扫描（P2）
```

**真实付费 Provider 的测试永远不进普通 PR CI**，只在受控 staging 定时执行并设硬预算。

---

## 10. 安全运维

- 密钥：不进代码、不进日志、不进 `input_json`。日志层已有递归脱敏
  （`core/logging.py::SENSITIVE_KEYS`）。
- `computed_field` 默认进 `repr()`，数据库连接串里是明文密码——所有含密码的
  computed field 必须 `repr=False`。这是踩过的坑，不是理论风险。
- 生产：TLS、HSTS、严格 CORS / Cookie、反向代理限流（P2）。
- 数据库最小权限；API 与 Worker 用独立身份更好（P2）。
- 审计日志与备份的访问权限独立管理（P2）。
- 镜像、依赖、基础设施漏洞扫描（P2）。

---

## 11. 本地开发的已知陷阱（都真实踩过，重复踩很浪费时间）

### 11.1 Compose 是**逐目录**挂载，仓库根新建的目录容器看不见

挂进容器的只有：`apps` `packages` `worker` `agents` `skills` `adapters`
`scripts` `migrations` `tests`，外加只读的 `pyproject.toml` 与 `alembic.ini`。
**`.git` 没挂**，`_research/` `design-system/` `project_docs/` `validation/`
`orca/` 也都没挂。

后果：在仓库根新建一个目录后，`docker compose exec api pytest` 里
`import 它` / 读它的文件会 `ModuleNotFoundError` / `FileNotFoundError`；
容器内跑 git 命令也拿不到 sha。

做法：要被容器内测试导入 / 读取的新文件，放进已挂载的目录（如 `tests/`）；
否则给 api 和 worker **各加一行挂载**并重建容器。

### 11.2 `os.environ.setdefault()` 在 Compose 已注入该变量时不生效

踩过两次：一次让测试连错 MinIO 地址，一次让**整个测试套件在打真实上游花钱**。
测试里改环境变量一律用赋值，改完 `get_settings.cache_clear()`。

### 11.3 在 worktree 里跑 `npm ci` 会让 api 容器无限重启

Compose 把 `./apps` **整个**挂进 api / worker，所以 `apps/web/node_modules`
对容器可见。uvicorn `--reload` 的 WatchFiles 会看见里面的 `.py` 文件
（例如 `apps/web/node_modules/flatted/python/flatted.py`），判定源码变了 →
重启 → 健康检查转 unhealthy → 8000 端口连不上。

**表现极具误导性**：`docker compose ps` 显示 api 是 Up，但 `curl /healthz`
直接超时，很容易误判成防火墙或 Docker 网络问题。真正的线索只在
`docker compose logs api` 里：`WatchFiles detected changes in 'apps/web/node_modules/...'`。

做法：装前端依赖之前先别起后端，或装完 `docker compose restart api`
（`node_modules` 装完就不再变，重启一次就稳）。

### 11.4 其他

- 每个带 `build:` 的服务默认各建一个镜像。api 与 worker 必须显式共用
  `image: aigc-studio-backend`，否则改依赖只重建一个。
- Docker Desktop 内置 DNS 对 AAAA 查询不稳定，`getaddrinfo` 整体失败，
  表现为"能 ping 通但 httpx 连不上"。Compose 已显式指定 DNS。
- Alembic 不为独立 `Sequence` 生成 `CREATE SEQUENCE`，也不为 pgvector 类型
  生成 import。前者手工补，后者已加 `render_item` 钩子。
- arq 的 `WorkerCoroutine` 协议按参数名匹配，**第一个参数必须叫 `ctx`**。
- 新增任务函数必须登记进 `worker/main.py::WorkerSettings.functions`，
  否则 Worker 收到任务报 unknown function。
- SSE 长连接不要用 ASGITransport 测（流式行为与真实 HTTP 有差异），
  拆成部件测 + 真实 HTTP 手工验证。

---

## 12. 模块依赖

| 依赖 | 关系 |
|---|---|
| 08 任务与实时 | 队列拆分会改变 Worker 的部署形态与中继归属（FR-OPS-013） |
| 12 媒体与合成 | ffmpeg 进镜像（FR-OPS-002）；渲染进程的资源上限与临时目录清理 |
| 05 模型网关 | Provider 侧的延迟 / 花费 / failover 指标 |
| 09 计费 | `audit()` 的调度与告警（FR-OPS-016） |
| 11 Web 工作台 | CI 前端 job（FR-OPS-001）与 E2E（FR-OPS-010） |
| 07 资产库 | 对象存储容量、pending 清理、`/readyz` 探 S3（FR-OPS-012） |

---

## 13. 当前缺口与风险

1. **CI 完全不看前端**：`apps/web` 改坏了合并进去也不会红。这是 FR-OPS-001
   要修的，也是本模块唯一真正紧急的一条。
2. **ffmpeg 不在镜像里**，合成阶段一写就跑不起来（FR-OPS-002）。
3. **API 启动时自动迁移**在多副本生产会并发执行（FR-OPS-011）。
4. **`/readyz` 不探对象存储**：S3 挂了服务仍报 ready，流量照进（FR-OPS-012）。
5. **没有任何采集端**：一次跨 API / Worker / Provider 的失败只能靠
   `docker compose logs | grep trace_id` 定位。
6. **`minio/minio:latest` / `minio/mc:latest`** 让环境不可复现（FR-OPS-015）。
7. **`billing.audit()` 有函数无调度**：账不平了没人知道（FR-OPS-016）。
8. **没有 RPO / RTO，没有备份，没有演练**。今天没有外部用户所以代价为零，
   有了用户之后这条会立刻变成 P0——**要在开放注册之前补上**。
9. **本机与容器工具链不完全一致**，容易出现"本地跑不全套"。命令一律以
   `docker compose exec api ...` 为准。

---

## 14. 迭代计划

**Wave 0 / 1（跟着产品闭环走）**

1. CI 加 `frontend` job：`typecheck + build`（FR-OPS-001）。
2. ffmpeg 进后端镜像，api / worker 一起重建（FR-OPS-002）。
3. Playwright 黄金路径进 CI，先做独立 job（FR-OPS-010）。

**Wave 2（跟着队列拆分走）**

4. 多 Worker 服务 + 中继归属固定（FR-OPS-013）——**必须与队列拆分同一次改动**。
5. `/readyz` 探对象存储（FR-OPS-012）；固定第三方镜像版本（FR-OPS-015）。
6. `audit()` 接调度与告警（FR-OPS-016）；关键指标先落成可聚合的日志字段（FR-OPS-017）。

**开放注册之前（不是"有空再做"）**

7. 迁移拆成独立发布步骤（FR-OPS-011）。
8. 定 RPO / RTO，做备份与一次真实恢复演练。
9. 生产 TLS、限流、Secret Manager。

**商业化之后**

10. 集中 Metrics / Tracing / 告警、staging、IaC、漏洞扫描。
11. 再评估 Kubernetes，**不以技术偏好提前迁移**。

---

## 15. 验收标准和测试

### 15.1 本轮（P0）

- CI 上存在 `frontend` job，`apps/web` 的类型错误或构建失败会让 PR 变红。
- `frontend` job **不含 `lint`**（flat config 修好之前加它必然红）。
- 后端镜像里 `ffmpeg -version` 有输出，且 api 与 worker 两个容器都有
  （它们共用同一个镜像，重建时不能只建一个）。
- 后端 `backend` job 保持全绿：ruff / mypy / alembic up-down-up / pytest。

### 15.2 常设

- 新克隆仓库后按 `CLAUDE.md`「跑起来」三条命令即可启动全链路，**不需要手工改文件**。
- 本地跑 `docker compose exec api pytest -q` 与 CI 结果一致。
- 任一失败任务能用一个 Trace ID 串起 API 日志 → Worker 日志 → Provider 调用记录。
- 日志里 grep 不到任何 Provider Key、数据库密码或 Cookie 值。
- `alembic upgrade head → downgrade base → upgrade head` 在 CI 上通过。
- 新增顶层目录后，若容器内需要读它，Compose 的 api 与 worker **两处**都加了挂载。

### 15.3 未来（生产就绪时）

- 恢复演练满足已定义的 RPO / RTO。
- 发布期间不重复执行任务、不丢账、不泄露密钥。
- 队列拆分后同一条 Outbox 事件只被投递一次（中继不重复）。
