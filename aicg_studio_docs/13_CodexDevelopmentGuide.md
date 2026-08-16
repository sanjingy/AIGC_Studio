# Codex 开发执行规范

## 1. Codex 总规则

Codex 不允许一次生成整个系统。

必须严格按照 Phase 执行。

每次开发必须：

1. 阅读现有 docs
2. 读取当前代码
3. 输出实施计划
4. 修改代码
5. 编写测试
6. 运行测试
7. 更新 docs
8. 输出变更摘要

## 2. 不允许的行为

- 大范围删除既有代码
- 未经说明更换技术栈
- 把 Provider 写死
- 把模型 API Key 写进代码
- 把 Agent 写成超长 Prompt
- 让 Agent 直接写数据库
- 让 Web 请求同步等待视频任务
- 把余额只保存为一个字段
- 把用户上传 Skill 当成可信代码执行

## 3. 每个功能的开发顺序

```text
Domain model
→ API contract
→ Service
→ Worker
→ Adapter
→ UI
→ Tests
→ Docs
```

## 4. 测试要求

Unit：

- pricing
- credits
- routing
- provider selection
- state transition

Integration：

- database
- redis
- task worker
- provider mock
- runtime mock

E2E：

```text
register
→ create project
→ input text
→ router
→ director
→ approval
→ generate mock asset
→ final output
```

## 5. Mock First

所有外部 Provider 在 MVP 都必须有 Mock Adapter。

例如：

```text
MockVideoProvider
MockImageProvider
MockComfyRuntime
MockAutoDL
```

先用 Mock 打通业务，再接真实服务。

## 6. Definition of Done

一个模块只有在以下都满足时才能标记完成：

- 实现
- 单元测试
- 集成测试（适用时）
- 错误处理
- 日志
- 权限检查
- 文档
- migration

## 7. Codex 第一条指令

```text
你现在负责实现 AICG Studio。
先不要写业务代码。
先读取 docs/00_ProjectOverview.md 到 docs/13_CodexDevelopmentGuide.md。
然后检查仓库现状，输出：
1. 当前代码结构
2. 与目标架构差异
3. Phase 0 实施计划
4. 风险
未经我确认，不执行大规模重构。
```

## 8. Codex 后续开发方式

每次只允许一个 Phase 或一个明确子任务。

示例：

```text
实现 Phase 0 的 Auth + Project。
不要实现 Agent、Provider、Billing。
完成后运行测试并汇报。
```

## 9. Agent 开发原则

先实现 Router Agent，再 Director Agent，再 Specialist Agent。

不要一开始创建 20 个真实 Agent。

MVP 第一个闭环建议：

```text
Router
→ Director
→ Story
→ Approval
→ Character
→ Approval
→ Storyboard
→ Mock Generation
→ Final
```

## 10. 代码质量

必须：

- mypy/pyright 或等价类型检查
- ruff
- pytest
- frontend lint
- frontend typecheck
- API schema validation

生产部署前必须：

- dependency lock
- secrets audit
- database backup
- migration test
