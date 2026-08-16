# 代码仓库结构

> 2026-08-16 修订：原方案在 `services/` 下拆了 7 个微服务。
> 在团队规模和验证阶段，微服务的成本（分布式事务、跨服务调试、
> 7 套部署与日志）是纯负债。改为**模块化单体**——
> 保留完全相同的领域边界，但只有 2 个进程。见 ADR-009。

---

## 目标结构

```text
repo/
├── apps/
│   ├── web/                          # Next.js
│   └── api/                          # FastAPI（唯一后端进程）
│       ├── main.py
│       ├── core/                     # 配置、DB、Redis、日志、鉴权
│       └── modules/                  # ← 领域边界在这里，不在进程边界
│           ├── auth/
│           ├── project/
│           ├── asset/
│           ├── task/
│           ├── agent/
│           ├── skill/
│           ├── billing/
│           ├── runtime/
│           ├── realtime/             # SSE 事件通道
│           ├── moderation/           # 合规审核（M1 为空实现）
│           └── cloud/                # 云 GPU（预留）
│
├── worker/                           # Arq Worker（第 2 个进程）
│   ├── main.py
│   └── jobs/
│       ├── agent_step.py
│       ├── generation.py
│       ├── render.py                 # ffmpeg 时间线渲染
│       └── maintenance.py
│
├── packages/
│   ├── contracts/                    # Pydantic schema，前后端共享的唯一真相
│   ├── domain/                       # 纯领域模型，不依赖框架
│   └── sdk/                          # 生成的 TS/Python 客户端
│
├── agents/                           # Agent 定义（提示词 + 输出 schema + 工具集）
│   ├── router/
│   ├── director/
│   ├── story/
│   ├── visual/                       # 角色/场景/分镜/提示词
│   ├── media/                        # 图像/视频/音频
│   └── qa/
│
├── consistency/                      # ★ 一致性引擎（核心资产，独立成包）
│   ├── character_pipeline/
│   ├── style_profile/
│   ├── conditioning/
│   ├── metrics/                      # 相似度/风格距离/时序稳定性
│   └── lora/                         # M4
│
├── timeline/                         # ★ 时间线与渲染
│   ├── model.py                      # EDL 数据结构
│   ├── assembler.py                  # 音频优先的时长推导
│   └── render/                       # ffmpeg 封装
│
├── adapters/
│   ├── providers/                    # 商业 API
│   │   ├── base.py
│   │   ├── mock/                     # Mock First，必须有
│   │   └── ...
│   ├── runtimes/
│   │   ├── comfyui/                  # M4
│   │   └── ssh_node/                 # M4
│   └── clouds/                       # 预留
│
├── evals/                            # Agent 回归测试集
│   ├── golden/
│   └── suites/
│
├── node-agent/                       # M4，独立可分发的 CLI
├── skills/
├── workflows/
├── migrations/                       # Alembic
├── infra/
│   ├── docker/
│   ├── nginx/
│   └── observability/
├── tests/
├── docs/
├── reviews/                          # 架构评审记录
└── scripts/
```

---

## 模块边界的强制手段

领域边界必须**被工具强制**，否则三个月后就会烂成一团。

`pyproject.toml` 中用 ruff 的 `flake8-tidy-imports` 禁止跨模块直接 import：

```toml
[tool.ruff.lint.flake8-tidy-imports.banned-api]
# 模块之间只能通过各自的 service 层交互，禁止直接摸对方的 repository/model
"apps.api.modules.*.repository".msg = "跨模块访问请走目标模块的 service 层"
"apps.api.modules.*.models".msg     = "跨模块访问请走目标模块的 service 层"
```

每个模块暴露一个显式的公开接口：

```text
modules/billing/
  ├── __init__.py      # 只导出 service 层的公开函数
  ├── router.py        # FastAPI 路由
  ├── service.py       # ← 唯一对外入口
  ├── repository.py    # 私有
  ├── models.py        # 私有
  └── schemas.py
```

**这样做的好处**：将来某个模块真的需要拆成独立服务时，
它已经是一个只通过 service 层通信的单元，把 `service.py` 换成 HTTP 客户端即可，
迁移成本接近于零。

---

## 目录原则

**不允许出现：**

```text
utils.py
common.py
helpers.py
ai.py
agent.py
misc/
```

所有能力必须落到明确的领域模块。工具函数放到它服务的那个模块里，
真正跨模块通用的（不超过 5 个）放 `core/`。

**新增目录必须回答**：它属于哪个领域？谁调用它？它调用谁？
回答不上来说明设计没想清楚，不要先建目录。

---

## 进程清单

MVP 只有这些进程，多一个都要写 ADR：

| 进程 | 数量 | 职责 |
|---|---|---|
| `apps/api` | 1（可水平扩） | HTTP + SSE |
| `worker` | 1+（按队列扩） | 异步任务 |
| PostgreSQL | 1 | |
| Redis | 1 | 队列 + 缓存 + 事件流 + 熔断状态 |
| MinIO / OSS | 1 | 对象存储 |

对比原方案的 7 个微服务 + 前后端 = 9 个进程，运维复杂度差一个数量级。
