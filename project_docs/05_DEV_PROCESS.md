# 开发流程

> 更新：2026-09-17（§5 / §6 重写）。适用于所有 Lead 会话与 Worker。

## 1. 文档先行

1. 新功能先改对应模块文档（`project_docs/modules/`）的需求、接口、验收，再改代码。
2. 偏离既有 ADR 的实现先提新 ADR（`aigc_studio_docs/15_ArchitectureDecisions.md`），
   标注 supersedes 哪一条。**29 条既有 ADR 全部有效**，不允许"顺手改掉"。
3. 状态词只用五个：已实现 / 部分实现 / 预留 / 仅设计 / 未实现。页面占位、类型声明、
   路线图都不算实现。
4. 事实来源优先级：当前代码与迁移 > 自动化测试 > `project_docs` > `aigc_studio_docs` 历史文档 > `_research`。

## 2. 每个功能的实施顺序

```
Domain model → API contract → Service → Worker → Adapter → UI → Tests → Docs
```

外部 Provider 一律先有 Mock，`ENV=test` 强制走 Mock。

## 3. Lead 与 Worker

- **Lead** 持有业务方向、跨模块契约、Task 切分和最终验收权。Lead 不长期占用自己做批量编码。
- **Worker** 只做任务书划定的范围，不组织团队，不派子 Agent 改文件。
- 任务书必须写明：允许修改的文件清单、禁止事项、输入文档、验收标准、回报方式。
  第一行要求 Worker 创建 ack 文件，Lead 靠 ack 确认送达，不靠回执。
- 任务书和报告放 `orca/tasks/`（已 gitignore）。
- 模型档位按 `orca-task-router` skill：判断型与规格不闭合的实施用 Opus 5 `high`；
  规格闭合的实施在 Codex 与 Opus 4.8 `xhigh` 之间轮；只读 Review 派 `orca-opus-reviewer`；
  要跑命令的调查派 `orca-opus-investigator`。
- 同一文件、同一迁移、同一接口契约不允许两个 Worker 并行。

## 4. 验收与返工

1. Worker 报完成不是验收证据。Lead 逐项复核代码、测试、契约。
2. **合并后由 Lead 手动验收**：跑起来点一遍主链路。有问题打回**同一个** Worker 返工
   （终端不关，上下文还在），用新 Task 写明不合格项。
3. 连续两次失败或触及架构边界，交回 Lead 重新判断派谁，不换模型硬跑。
4. 验收通过、交接完成或任务放弃后，Lead 回收 Worker 终端。跑完的 Worker 不留着占分屏。

## 5. CI 地板

`.github/workflows/ci.yml` 两个 job：`backend`（ubuntu-latest + pgvector/pg16 +
redis:7 两个 service 容器）与 `web`。backend 的步骤顺序就是下表的顺序。

| 门禁 | 状态 | 说明 |
|---|---|---|
| 后端 `ruff check .` + `ruff format --check .` | 已有 | 跑在**仓库根**，范围见 §5.1 |
| 后端 `mypy apps worker packages agents adapters skills` | 已有 | runner 是 Linux，见 §6.2 |
| **数据库扩展初始化（vector / pgcrypto）** | 已有（2026-09-16 补） | 见 §5.2 |
| 后端 Alembic 升 → 降 → 升 | 已有 | `upgrade head` → `downgrade base` → `upgrade head`，只升不降等于没测回滚 |
| 后端 `pytest --cov=apps --cov=worker` | 已有 | 跑前起一次性 MinIO 容器 + 一个 arq worker，见 §5.3 |
| 前端 `typecheck + build` | 已有（2026-09-03，`web` job） | 不依赖 ESLint，两分钟 |
| 前端 `lint` | 待 ESLint 9 flat config 修好 | — |
| Playwright 黄金路径 | Wave 1 | 登录 → 新建 → 剧本确认 → 分镜确认 → 出图 |

CI 是地板不是天花板：CI 绿只说明没白屏，能不能用由 Lead 手动验收说了算。

### 5.1 lint 范围：`.claude/skills/` 不在口径内

`pyproject.toml` 的 `[tool.ruff] extend-exclude` 里有 `.claude/skills`。
那底下 74 个文件是**装进来的第三方技能包**（`ui-ux-pro-max` 是 MIT 的 vendored
设计技能，`orca-task-router` 的 `assets/` 是它自带的脚本），由上游独立维护、
随更新整包覆盖；它们是 git tracked 的，CI checkout 拿得到，不排除就会让
`ruff check .` 常年红着（实测 2026-09-17：排除前 211 条告警，其中 209 条在这个
目录里，而且有 60 条是 RUF013 / UP03x / E731 这类**格式化修不掉**的代码级告警）。

排除的只有这一层：

- **不排整个 `.claude/`**——同目录下我们自己写的 hook / 脚本仍要被检查；
- `apps` / `worker` / `packages` / `agents` / `adapters` / `skills` / `tests` /
  `tools` / `scripts` / `migrations` 的检查范围**一条没变**；
- `[tool.ruff.lint]` 的 `select`、`ignore`、`banned-api`（跨模块只调 service 的
  那套 TID251 规则）**一条没删**。

### 5.2 为什么要单独建扩展

CI 的 `services:` 起的 postgres 容器**不会**执行 `infra/docker/postgres/init.sql`——
那份文件只在 `docker-compose.yml` 里挂进 `/docker-entrypoint-initdb.d/`，
`services:` 没有这个挂载。而 `22dbb7fcecbc` 与 `d7a1e4c93b02` 两个迁移都建
`VECTOR(1024)` 列，fresh 库第一次 `alembic upgrade head` 必失败。

那一步执行的是**仓库里那份 init.sql 的原文**，不另抄 SQL（抄成两份，compose 与 CI
迟早在"哪些扩展是必须的"上分叉）；连接串取自 `get_settings().database_url`，与下一步
alembic 用的是同一个 URL；建完回读 `pg_extension` 断言 `{vector, pgcrypto}` 都在，
缺了就非零退出——否则"这一步成功但扩展没建上"比不加这一步更难查。
扩展不随 `downgrade base` 掉，所以升→降→升整套只需建一次。

### 5.3 Tests 步骤里的两个临时件

- **MinIO**：`docker run -d --name aigc-ci-minio`，健康检查过了再用 aioboto3 建桶。
  资产模块的直传链路没有 Mock 分支，去掉它整类集成用例直接失败。
- **arq worker**：后台起一个，用 `python -m arq ... --check` 轮询就绪，
  `trap ... EXIT` 收尾；失败时 `Worker diagnostics`（`if: failure()`）打 worker 日志尾。

这两件和升→降→升都**不要删**：删掉之后 CI 仍然绿，但绿的含义变了。

## 6. 提交前

### 6.1 完整门禁命令

容器在跑就用容器（和 CI 的 Linux 环境最接近）：

```bash
docker compose exec api ruff check .
docker compose exec api ruff format --check .
docker compose exec api mypy apps worker packages agents adapters skills
docker compose exec api pytest -q
cd apps/web && npm run typecheck && npm run build
```

没有 Docker 时用本机 venv 跑前三项（第四项的 pytest 需要 PG/Redis/MinIO，本机没有
就只能上容器或测试机）：

```bash
python -m venv .venv                      # .venv/ 已 gitignore
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Linux/macOS: .venv/bin/python
ENV=test .venv/Scripts/python.exe -m ruff check .
ENV=test .venv/Scripts/python.exe -m ruff format --check .
ENV=test .venv/Scripts/python.exe -m mypy --platform linux apps worker packages agents adapters skills
```

`pip install -e ".[dev]"` 会**卡在解释器版本上**：`requires-python = ">=3.13,<3.14"`，
本机解释器是 3.14 就直接被 pip 拒掉。这时要么装一个 3.13 建 venv，要么退一步只装
依赖本身（`pip install fastapi ... ruff mypy`，不带 `-e .`）——mypy 从仓库根按目录
参数走，不需要项目被 install 也能检查。2026-09-17 就是用后一种方式跑出的上述结论，
**因此它验证的是"当前依赖版本下代码干净"，不等于验证了 CI 的 `pip install -e` 那一步**。

要跑完整后端门禁又不想在本机装 Docker，走测试机：`python tools/srv_check.py all`
（把当前工作区打包传到香港测试机的 api 容器里跑，跑完恢复服务器检出）。
**同一时刻只能有一个人跑**——服务器上只有一套容器和一个库。

### 6.2 Windows 上 mypy 必须加 `--platform linux`

不加的话本机会报 6 条错，全在 `apps/local_runner/`：

```
apps/local_runner/process.py:75   Module has no attribute "killpg" / "getpgid" / "SIGKILL"
apps/local_runner/appserver.py:376  同上三条
```

这是 **mypy 按当前平台裁剪 `os` / `signal` 存根**的结果，不是缺陷：CI 跑在
ubuntu-latest，这三个符号都存在。加上 `--platform linux` 后本机复现 CI 口径，
实测 `Success: no issues found in 148 source files`。

**不要**为了让 Windows 本机变绿去改 `apps/local_runner/` 或加 `# type: ignore`——
那是把一个平台差异冻进代码。

### 6.3 CI 没有钉住 ruff / mypy 版本

`pyproject.toml` 的 dev 依赖写的是 `ruff>=0.8` / `mypy>=1.13`，CI 每次
`pip install -e ".[dev]"` 装到的都是**当天的最新版**，所以同一个 commit 的 lint
结论会随时间漂。2026-09-17 实测到两处确凿的版本差：

| 现象 | ruff 0.12.12 | ruff 0.16.8 |
|---|---|---|
| `apps/api/core/logging.py:55` 的 `isinstance(value, (list, tuple))` | 报 UP038 | 不报（UP038 已废弃移除） |
| Markdown 里的 Python 代码块 | 不解析，`ruff format --check .` 看不见 | 会格式化，4 份 `.md` 因此被判 unformatted |

**要不要钉版本是依赖策略决定，没有在这一轮擅自改**。在钉住之前：本机跑出来的
lint 结论只对**你这台机上那个 ruff 版本**成立，跟 CI 对不上时先比版本号再查代码。

### 6.4 哪些是"跑过的"，哪些不是

2026-09-17 在本机（Windows 10 + Python 3.14 venv，ruff 0.16.8 / mypy 2.3.1）
**实际跑过并通过**的：

- `ruff check .` → `All checks passed!`
- `ruff format --check .` → `297 files already formatted`
- `mypy --platform linux apps worker packages agents adapters skills`
  → `Success: no issues found in 148 source files`
- `ci.yml` 显式 UTF-8 解码 + `yaml.safe_load` 通过；抽出全部 10 段 `run` 脚本
  逐个 `bash -n`，10/10 rc=0
- 扩展步的**非数据库部分**离线复跑：`get_settings().database_url` 解析出
  `postgresql+asyncpg://…/aigc_studio`，init.sql 切出的正是那两条
  `CREATE EXTENSION IF NOT EXISTS`

**没有跑过，不得当作通过**：

- **GitHub Actions 本身一次都没触发过**。上面全部是本机等价复跑，
  不是 workflow 的真实运行记录。
- **`alembic upgrade head` / 升→降→升没有在 fresh 隔离库上跑过**（本机无 PG）。
  扩展步能不能真的救下第一次迁移，只有远端演练能证明。
- **Tests 步整段没有复跑**：MinIO 容器、建桶、arq worker 就绪轮询、
  `pytest --cov` 都没执行过。
- **`web` job 没有复跑**：`npm ci` / `typecheck` / `build` 三步这一轮没碰。

## 7. 需要负责人确认的事

只有这些要停下来问：产品方向缺口、不可逆操作、外部权限或付费、生产发布、
`git commit`、`git push`、对外发送。普通派发、重试、模型切换、读代码、跑测试不问。

## 8. 分支

2026-09-14 负责人决定：`feat/freeflow-prototype` 已合入本地 `main`，
后续默认直接在 `main` 和当前工作区开发。没有隔离需求不建新分支或 worktree。
当前机器按一次一个实施 Worker 调度，构建、浏览器验收和测试服务串行运行。

前端验收可设置 `NEXT_BUILD_DIR=.next-acceptance`，生产构建检查可设置
`NEXT_BUILD_DIR=.next-build-check`，将模块清单与已有开发服务的 `.next` 隔离。
同一轮 `build` 与 `start` 必须使用同一个目录和 `API_ORIGIN`。
