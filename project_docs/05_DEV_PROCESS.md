# 开发流程

> 更新：2026-09-02。适用于所有 Lead 会话与 Worker。

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

| 门禁 | 状态 | 说明 |
|---|---|---|
| 后端 `ruff check` + `ruff format --check` + `mypy` + `pytest` | 已有 | GitHub Actions |
| 后端 Alembic 迁移可升级 | 已有 | GitHub Actions |
| 前端 `typecheck + build` | 已有（2026-09-03，`web` job） | 不依赖 ESLint，两分钟 |
| 前端 `lint` | 待 ESLint 9 flat config 修好 | — |
| Playwright 黄金路径 | Wave 1 | 登录 → 新建 → 剧本确认 → 分镜确认 → 出图 |

CI 是地板不是天花板：CI 绿只说明没白屏，能不能用由 Lead 手动验收说了算。

## 6. 提交前

```bash
docker compose exec api ruff check . && docker compose exec api ruff format --check .
docker compose exec api mypy apps worker packages agents adapters skills
docker compose exec api pytest -q
cd apps/web && npm run typecheck && npm run build
```

## 7. 需要负责人确认的事

只有这些要停下来问：产品方向缺口、不可逆操作、外部权限或付费、生产发布、
`git commit`、`git push`、对外发送。普通派发、重试、模型切换、读代码、跑测试不问。

## 8. 分支

默认复用当前 feature 分支和当前 worktree，没有隔离需求不建新分支。
`feat/freeflow-prototype` 是当前工作分支，合并 `main` 的时机由负责人定。
