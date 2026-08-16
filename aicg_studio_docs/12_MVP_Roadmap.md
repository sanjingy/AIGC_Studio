# 里程碑路线图

> 2026-08-16 修订：原 Phase 0–8 是线性瀑布，第一条视频要到 Phase 4 才出现，
> 太晚。改为每个里程碑都交付**用户可感知的完整价值**。

---

## M0：手工验证（不写平台代码）

**时长：2 周。这是唯一一个不产出代码的里程碑，也是最重要的一个。**

用现成工具（ComfyUI / 商业 API / 剪映）**手工做出一部完整的 3 分钟漫剧**。

必须回答（对应 `17_ConsistencyEngine.md` 第 9 节）：

- 参考图条件化的实际角色相似度能到多少？
- LoRA 需要多少训练图、训练多久？
- 图生视频环节漂移有多严重？
- 哪种镜头最容易崩（换装 / 侧脸 / 远景 / 多人）？
- 真实废片率是多少？（回填 `19_UnitEconomics.md`）
- 每个环节实际耗时多少？哪一步最需要自动化？

**交付物**：一部片子 + 一份《制作流程实录》+ 修订后的成本模型。

**为什么必须做**：现有文档是从抽象往下设计的，
假设 12 个 Agent 协作就能出片，但没有任何证据表明这条流水线跑得通。
**先跑通流水线，再自动化它。**

---

## M1：纵向切片（第一条可用链路）

**时长：6–8 周**

```text
注册登录 → 创建项目 → 输入小说
  → Router → Director
  → Story Agent      → 人工确认
  → Character Agent  → 基准立绘生成 → 人工确认（一致性关键门）
  → Storyboard Agent → 人工确认
  → 真实出图（不是 Mock）
  → 资产库
```

**范围内：**

- FastAPI 模块化单体 + Arq Worker + PostgreSQL + Redis + MinIO
- Next.js 前端，7 个页面（见 `01_ProductSpec.md`）
- Auth / Project / Asset / Task 基础闭环
- AI Gateway 最小版本：2 个真实 Provider + 1 个 Mock
- SSE 实时通道（`20_Realtime.md`）
- Credits reserve / settle / release 全链路
- 一致性引擎 L0 + L1（`17_ConsistencyEngine.md` 第 8 节阶段 1–2）
- 审核中间件与水印注入点的空实现（`18_Compliance.md`）
- Agent 回归测试最低版本（`22_AgentEval.md` 第 6 节）

**范围外：** 视频生成、ComfyUI、节点、云 GPU、Skill 上传、管理后台

**验收：** 用户能在浏览器完整走一遍到分镜出图，
每步可确认、可修改、可重试、可取消，Credits 账目分毫不差。

**M1 的核心风险验证**：一致性做到 L1 够不够用？如果不够，M2 必须补 L2。

---

## M2：出片（第一部完整视频）

**时长：6–8 周。这是产品真正诞生的里程碑。**

- TTS 接入 + **音频优先的时间线**（`17_ConsistencyEngine.md` 第 5 节）
- 时间线数据模型 + ffmpeg 渲染服务
- 图生视频接入
- 字幕轨 + BGM 轨
- 分级渲染：预览档 / 定稿档（`19_UnitEconomics.md` 第 3 节）
- 一致性量化指标 + 自动重试（阶段 3–4）
- 错误降级与占位镜头（`21_ErrorTaxonomy.md` 第 6 节）

**验收：** 用户从一篇小说自助产出一部 3–5 分钟成片，
成片可用率 ≥ 60%（10 部里至少 6 部不需要大改就能发布）。

---

## M3：可以收钱

**时长：4 周**

- 充值 / 订单 / 支付
- 定价规则热更新 + 成本熔断（`19_UnitEconomics.md` 第 7 节）
- 免费额度与转化设计
- 合规最小集：实名 + 审核接入 + AIGC 标识（`18_Compliance.md` 补齐后执行）
- 基础运营后台：用户、订单、任务、成本看板
- 每周自动跑真实链路评估（`22_AgentEval.md`）

**验收：** 外部用户可付费使用，单片毛利率 ≥ 45%，
盈亏平衡目标：每天 16 部（见 `19_UnitEconomics.md` 第 8 节）。

---

## M4：ComfyUI 与用户自有 GPU

**时长：6 周**

- ComfyUI Runtime Adapter（本地 + 远程）
- Node Agent，**仅出站连接**（`06_ComfyUIAndNode.md`）
- Workflow 上传、校验、参数映射
- 一致性引擎 L2：角色 LoRA 训练

**验收：** 用户自己电脑的 ComfyUI 成为平台可用 Runtime，
主角 LoRA 可在用户 GPU 上训练。

---

## M5：Skill 生态

- 官方 Skill 库
- Workflow → Skill 转换
- 用户自定义 Skill、版本、权限

---

## M6+：待定

以下方向**暂不排期**，需要在 M3 拿到真实运营数据后重新评估：

- 云 GPU 租赁 / AutoDL 联动（`07_AutoDL.md`，方案预留）
- Skill / Workflow / GPU Marketplace
- Public API
- 多租户企业版

> 判断标准：这个功能能不能帮我们更快到"每天 16 部"？
> 不能的，就不做。

---

## 关键路径

```text
M0 手工验证 ──→ M1 纵向切片 ──→ M2 出片 ──→ M3 商业化
                                              │
                                              ├──→ M4 ComfyUI/GPU
                                              └──→ M5 Skill 生态
```

M0 → M3 是串行的关键路径，约 5 个月。M4/M5 可并行。

**任何"能不能先做 XX"的提议，先问它在不在关键路径上。**
