# AICG Studio Codex 开发文档包

这是 AICG Studio 的第一版架构与开发蓝图。

## 推荐阅读顺序

1. 00_ProjectOverview.md
2. 01_ProductSpec.md
3. 02_Architecture.md
4. 03_AgentSystem.md
5. 04_SkillSpec.md
6. 05_ModelRuntimeSpec.md
7. 06_ComfyUIAndNode.md
8. 07_AutoDL.md
9. 08_BillingCredits.md
10. 09_Database.md
11. 10_API.md
12. 11_ProjectStructure.md
13. 12_MVP_Roadmap.md
14. 13_CodexDevelopmentGuide.md
15. 14_FutureExtensibility.md
16. 15_ArchitectureDecisions.md
17. 16_FirstMilestone.md

## 当前核心思想

```text
用户需求
   ↓
Router Agent
   ↓
选择路线 + Skill + Director
   ↓
Director Agent
   ↓
动态编排 Specialist Agents
   ↓
Human Approval Gates
   ↓
Task Queue
   ↓
AI Gateway / Runtime Gateway
   ↓
商业 API / 本地模型 / ComfyUI / SSH / 云 GPU
   ↓
Asset + Billing + QA
   ↓
最终内容
```

## 现实技术依据

ComfyUI 当前官方仓库提供工作流 Prompt API/OpenAPI，并有 WebSocket 执行监听示例，因此适合作为统一 Runtime Adapter 的实现目标。citeturn905503search2turn905503search3

AutoDL 当前官方 Pro API 支持使用开发者 Token 创建实例、查看状态、开机、关机和释放，可用于实现 Cloud Provider Adapter；使用该 API 需要相应认证。citeturn905503search0
