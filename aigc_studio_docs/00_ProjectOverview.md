# AIGC Studio 项目总览

## 1. 项目定位

AIGC Studio 是一个面向普通用户的 AI 内容生产操作系统（AI Content Production OS），统一编排：

- 文本、图片、文档等多模态输入
- Router Agent 与 Director Agent
- 专业 Agent 集群
- Skill / Workflow
- 商业模型 API
- 本地模型
- 本地 / 远程 ComfyUI
- SSH 远程 GPU
- AutoDL 等云 GPU
- 统一 Credits（Token/积分）
- 项目资产、版本、审核与最终成片

产品核心不是“模型聚合”，而是：

> 用户描述目标 → Router 判断创作路线 → Director 制定生产方案 → 专业 Agent 协作 → 每个关键阶段人工确认 → 自动完成成片。

## 2. 核心产品原则

### 2.1 目标导向，而不是模型导向
用户先说“我要做一个 5 分钟悬疑漫剧”，而不是先选择 Seedance / Flux / Wan。

### 2.2 Agent 与 Runtime 解耦
Agent 只描述能力需求，例如 `text_to_video`；Provider/Runtime 决定实际由哪一个模型或工作节点完成。

### 2.3 所有重要产物可版本化
角色、场景、脚本、分镜、Prompt、Workflow、图片、视频、音频都必须可产生版本并回滚。

### 2.4 Human-in-the-loop
用户不是被排除在生产流程之外，而是在关键门槛进行确认：路线、角色、场景、剧本、分镜、关键视觉、最终视频。

### 2.5 一切任务异步化
耗时生成任务不得阻塞 Web 请求；统一 Task/Job/Worker/Provider 生命周期。

### 2.6 一致性是生死线（2026-08-16 新增）
用户判断产品可用性的第一标准，是第 1 个镜头的主角和第 47 个镜头是不是同一个人。
编排体验可以被抄，一致性质量抄不走。见 `17_ConsistencyEngine.md`、ADR-012。

### 2.7 成本可算、可控、可参数化（2026-08-16 新增）
每个动作在执行前必须知道要花多少钱，执行后必须知道实际花了多少。
所有单价与系数走数据库，代码中不出现价格常量。见 `19_UnitEconomics.md`、ADR-014。

## 3. 首发产品范围

第一阶段采用：

1. 小范围内测 → 商业化架构
2. 文本/小说 → 漫剧为主线
3. 同时预留文本→视频、图片→视频
4. 第一版支持官方 API、OpenAI-Compatible API、ComfyUI、SSH/远程节点
5. 用户可绑定自己的电脑/5090/服务器
6. Skill：官方 + 用户上传；后期 marketplace
7. ComfyUI Workflow：允许上传并封装为可调用 Skill
8. 收费：Credits；GPU 租赁单独计费
9. API：第一版预留，后续开放公网 API
10. AutoDL：第二阶段接入账户联动自动创建实例

## 4. 产品三层

### 用户层

- 项目
- 创作工作台
- Agent 对话
- 审核确认
- 资产库
- Skill 库
- Credits
- GPU 租赁

### AI 编排层

> 2026-08-16 修订：Specialist Agent 由 11 个收敛为 4 个，
> 见 `03_AgentSystem.md` 第 1 节。

- Router Agent —— 分类与路由
- Director Agent —— 编排与调度
- Story Agent —— 故事结构 / 剧本 / 台词
- Visual Agent —— 角色 / 场景 / 分镜 / 镜头提示词
- Media Agent —— TTS / 图像 / 视频（**TTS 先行**，ADR-011）
- QA Agent —— 质量校验

### 确定性模块（不是 Agent）

- **Consistency Engine** —— 角色资产冻结、风格锁定、一致性度量
- **Timeline Assembler** —— 音频优先的时长推导与 ffmpeg 渲染

### 基础设施层

- API（模块化单体，ADR-009）
- Agent Engine
- Task Queue（Arq，ADR-010）
- Realtime（SSE，ADR-013）
- AI Gateway
- Runtime Gateway ┈ M4
- ComfyUI Adapter ┈ M4
- Node Agent（仅出站连接，ADR-007）┈ M4
- Cloud Provider Adapter ┈ 预留
- Moderation Hook ┈ M1 空实现
- Asset Storage（预签名直传）
- PostgreSQL + pgvector
- Redis
- Observability
- Billing Ledger

## 5. 产品愿景

最终用户不再理解“模型”，只需要理解“创作任务”。

AIGC Studio 应让复杂的 AI 生产链被隐藏在 Agent 编排系统之后，同时仍然允许专业用户下沉到模型、Prompt、Workflow、GPU 和节点级控制。
