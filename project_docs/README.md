# AIGC Studio 项目知识库

> 更新基线：2026-09-02，分支 `feat/freeflow-prototype`。**本目录是项目文档的唯一入口。**
>
> 本目录以当前代码、数据库迁移和测试为事实依据；`aigc_studio_docs/` 除 ADR 外均为历史资料。文档中的状态统一使用：`已实现`、`部分实现`、`预留`、`仅设计`、`未实现`。

## 1. 阅读入口

| 文档 | 用途 |
|---|---|
| [DECISIONS_2026-09-02.md](./DECISIONS_2026-09-02.md) | **本轮大改的全部决策**，与其他文档冲突时以它为准 |
| [00_CURRENT_STATUS.md](./00_CURRENT_STATUS.md) | 产品终点定义（先读）、当前完成度、边界和主要风险 |
| [01_FUNCTION_INVENTORY.md](./01_FUNCTION_INVENTORY.md) | 现有功能总清单和真实状态 |
| [02_TECH_STACK.md](./02_TECH_STACK.md) | 技术栈选择、理由和替代边界 |
| [03_ENGINEERING_DESIGN.md](./03_ENGINEERING_DESIGN.md) | 总体架构、数据、任务、实时、安全与工程规范 |
| [04_MODULE_ROADMAP.md](./04_MODULE_ROADMAP.md) | 模块依赖、优先级和版本路线 |
| [05_DEV_PROCESS.md](./05_DEV_PROCESS.md) | 文档先行、Lead/Worker、验收返工、CI 地板 |
| [modules/](./modules/) | 每个业务模块的独立需求与实施计划 |
| `../aigc_studio_docs/15_ArchitectureDecisions.md` | ADR-001 ~ 033，全部有效 |

## 2. 模块计划

| 编号 | 模块 | 当前状态（2026-09-02 核对） | 计划文档 |
|---:|---|---|---|
| 01 | 用户、登录与组织 | 部分实现（注册/登录/会话已通，邮箱验证与找回密码未做） | [01_AUTH_USER.md](./modules/01_AUTH_USER.md) |
| 02 | 项目与工作区 | 部分实现（后端已通，freeflow 未独立闭环） | [02_PROJECT_WORKSPACE.md](./modules/02_PROJECT_WORKSPACE.md) |
| 03 | 故事、剧本与内容修订 | 部分实现（结构化生成已通；字段级编辑后端未提交、前端未接） | [03_CONTENT_STORY.md](./modules/03_CONTENT_STORY.md) |
| 04 | Agent 编排与人工审核 | 部分实现（文本链路已通；不计费不幂等，媒体编排未做） | [04_AGENT_ORCHESTRATION.md](./modules/04_AGENT_ORCHESTRATION.md) |
| 05 | 模型网关与 BYOK | 部分实现（文本 + 图像已通；视频 / TTS、三层默认、选模型按钮未做） | [05_MODEL_GATEWAY.md](./modules/05_MODEL_GATEWAY.md) |
| 06 | 一致性与图像生成 | 部分实现（L0 出图已通；候选版本、质量评分未做） | [06_CONSISTENCY_IMAGE.md](./modules/06_CONSISTENCY_IMAGE.md) |
| 07 | 资产库 | 部分实现（上传/配额/聚合已通；候选版本未做） | [07_ASSET_LIBRARY.md](./modules/07_ASSET_LIBRARY.md) |
| 08 | 任务、队列与实时进度 | 部分实现（状态机/Outbox/SSE 已通；队列不分类，freeflow 任务页读错表） | [08_TASK_REALTIME.md](./modules/08_TASK_REALTIME.md) |
| 09 | Credits、充值与支付 | 部分实现（账本已通；topup 不设防，支付不做） | [09_BILLING_PAYMENT.md](./modules/09_BILLING_PAYMENT.md) |
| 10 | Skill 与工作流 | 部分实现（上传校验已通；运行时冻结） | [10_SKILL_WORKFLOW.md](./modules/10_SKILL_WORKFLOW.md) |
| 11 | Web 工作台与交互 | 部分实现（freeflow 是唯一正式壳，尚无"开始生产"入口） | [11_WEB_WORKBENCH.md](./modules/11_WEB_WORKBENCH.md) |
| 12 | 视频、音频、时间线与导出 | 未实现（M2 主目标：逐镜 MP4） | [12_MEDIA_TIMELINE_EXPORT.md](./modules/12_MEDIA_TIMELINE_EXPORT.md) |
| 13 | 运营后台、合规与风控 | 仅设计（整体 P2） | [13_ADMIN_COMPLIANCE.md](./modules/13_ADMIN_COMPLIANCE.md) |
| 14 | 平台运维与可观测性 | 部分实现（本地可跑；CI 前端地板与 ffmpeg 进镜像是 P0） | [14_PLATFORM_OPERATIONS.md](./modules/14_PLATFORM_OPERATIONS.md) |

## 3. 文档维护规则

1. 新功能先更新对应模块文档的需求、接口和验收条件，再改代码。
2. 状态只能依据可运行代码、数据库迁移和测试改变；页面占位、类型声明和路线图不算实现。
3. 跨模块设计决策继续写入 `aigc_studio_docs/15_ArchitectureDecisions.md`，本目录只引用结论。
4. 每个模块必须保持自己的范围、数据所有权、公开接口、依赖、里程碑和验收标准。
5. 不移动现有 `aigc_studio_docs/`：其中包含 ADR 和历史设计依据，直接移动会破坏已有引用；本目录作为新的统一入口。

## 4. 事实来源优先级

```text
当前代码与迁移
  > 自动化测试与可运行行为
  > 本目录模块计划
  > aigc_studio_docs 历史设计文档
  > reviews 与研究报告
```

