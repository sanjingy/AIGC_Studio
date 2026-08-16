# AIGC Studio

面向普通用户的 AI 内容生产操作系统。用户描述目标，系统编排 Agent 完成从
小说到成片的整条生产链。

**设计文档在 [`aigc_studio_docs/`](aigc_studio_docs/README.md) —— 动代码前先读它。**
架构评审记录见 [`reviews/`](reviews/)。

## 快速开始

需要 Docker Desktop 处于运行状态。

```bash
cp .env.example .env
make up
```

| 服务 | 地址 |
|---|---|
| API 文档 | http://localhost:8000/docs |
| 存活探针 | http://localhost:8000/healthz |
| 就绪探针 | http://localhost:8000/readyz |
| MinIO 控制台 | http://localhost:9001 |

`make help` 查看全部命令。

## 当前进度

| 步骤 | 内容 | 状态 |
|---|---|---|
| S1 | 工程骨架 | ✅ |
| S2 | 账号体系 | ⬜ |
| S3 | 项目与资产 | ⬜ |
| S4 | 任务流水线 + 实时通道 | ⬜ |
| S5 | 计费 Ledger | ⬜ |
| S6 | AI Gateway | ⬜ |
| S7 | Agent 编排 | ⬜ |
| S8 | 一致性 + 验证切片 | ⬜ |

## 技术栈

后端 FastAPI（模块化单体，ADR-009）+ Arq（ADR-010）+ PostgreSQL/pgvector + Redis +
S3 兼容存储；前端 Next.js + TypeScript + Tailwind + shadcn/ui。

## 开发约定

以下几条违反了会被 CI 拦下，也是过往踩坑总结，不是形式主义：

- **价格、汇率、废片率不许写成代码常量**，走 `pricing_rules` / `model_pricing`（ADR-014）
- **执行状态只认 `tasks` 表**，不在别处并行维护（ADR-008）
- **金额一律 `BIGINT` 最小单位**，不用浮点
- **跨模块只能调对方 `service` 层**，不许直接 import 对方的 `models` / `repository`
- **密钥不进代码、不进日志、不进 `input_json`**
- 偏离设计文档的实现，先提 ADR

完整规范见 [`13_CodexDevelopmentGuide.md`](aigc_studio_docs/13_CodexDevelopmentGuide.md)。
