# AIGC Studio 架构与开发文档

> **2026-09-02 起本目录为历史资料，唯一入口是 [`../project_docs/`](../project_docs/README.md)。**
> 例外：`15_ArchitectureDecisions.md` 仍是 ADR 的唯一存放处，全部有效。
> 本目录其余文档不再更新，与 `project_docs` 冲突时以 `project_docs` 为准；
> 保留是因为 ADR 和评审记录引用了它们。

第二版蓝图。基于 2026-08-16 架构评审修订，评审记录见
[`../reviews/2026-08-16_架构评审与修订记录.md`](../reviews/2026-08-16_架构评审与修订记录.md)。

## 推荐阅读顺序

### 第一层：想清楚做什么

| 文档 | 内容 |
|---|---|
| `00_ProjectOverview.md` | 项目定位与产品原则 |
| `01_ProductSpec.md` | PRD、用户类型、审核门、MVP 范围 |
| `19_UnitEconomics.md` | **成本与定价模型（先读这个再谈技术）** |
| `16_FirstMilestone.md` | 第一个里程碑与前置验证 |

### 第二层：核心技术难点

| 文档 | 内容 |
|---|---|
| `17_ConsistencyEngine.md` | **角色/风格一致性——本项目的核心难点与护城河** |
| `03_AgentSystem.md` | Agent 分层、状态机、Tool 约定 |
| `04_SkillSpec.md` | Skill 协议 |

### 第三层：系统设计

| 文档 | 内容 |
|---|---|
| `02_Architecture.md` | 总体架构、技术栈、解耦原则 |
| `05_ModelRuntimeSpec.md` | Capability / Provider / Runtime 三层协议 |
| `09_Database.md` | 数据模型 |
| `10_API.md` | REST API |
| `20_Realtime.md` | SSE 实时事件通道 |
| `21_ErrorTaxonomy.md` | 错误分类、重试、Failover、幂等 |
| `08_BillingCredits.md` | Credits Ledger |
| `11_ProjectStructure.md` | 代码仓库结构 |

### 第四层：执行

| 文档 | 内容 |
|---|---|
| `12_MVP_Roadmap.md` | 里程碑路线图 |
| `13_CodexDevelopmentGuide.md` | AI 辅助开发执行规范 |
| `22_AgentEval.md` | Agent 质量回归体系 |
| `15_ArchitectureDecisions.md` | ADR 记录 |

### 第五层：暂缓与预留

| 文档 | 状态 |
|---|---|
| `06_ComfyUIAndNode.md` | M4 |
| `18_Compliance.md` | 占位，待补 |
| `07_AutoDL.md` | 预留，方案待定 |
| `14_FutureExtensibility.md` | 长期方向 |

## 核心链路

```text
用户需求
   ↓
Router Agent                    分类与路由，不做生产
   ↓
Director Agent                  制定方案，调度 Agent
   ↓
Story / Character / Storyboard  内容生产（含人工确认门）
   ↓
一致性引擎                       角色资产冻结 + 风格锁定
   ↓
TTS 先行 → 确定镜头时长          音频优先，避免音画错位
   ↓
关键帧 → 图生视频 → 时间线装配
   ↓
ffmpeg 渲染 → 成片
```

## 三条不可动摇的原则

1. **Capability / Provider / Runtime 三者分离**
   业务层只声明"要什么能力"，不关心用哪家、在哪跑。

2. **成本模型参数化，绝不写死**
   上游价格随时变（DeepSeek 于 2026-08-17 调价 350%）。
   所有单价走 `pricing_rules` 表，支持热更新。

3. **一致性是产品生死线，不是加分项**
   编排体验可以被抄，一致性质量抄不走。

## 技术依据

- ComfyUI 提供 HTTP `/prompt` 提交接口与 WebSocket 执行事件监听，
  官方仓库附带 OpenAPI 定义，适合封装为统一 Runtime Adapter。
  参考：<https://github.com/comfyanonymous/ComfyUI>
- AutoDL 提供开发者 Token 鉴权的 Pro API，支持实例创建、状态查询、
  开关机与释放，可用于实现 Cloud Provider Adapter。
  参考：<https://www.autodl.com/docs/>

> 上述链接需在接入前重新核实接口现状与版本。
