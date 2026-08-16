# AICG Studio 产品需求文档（PRD）

## 1. 用户类型

### 普通创作者
目标：低门槛完成视频/漫剧。

### 半专业创作者
目标：控制角色、风格、镜头和模型选择。

### 专业用户
目标：使用自定义 Skill、ComfyUI Workflow、本地 GPU、远程节点。

### 平台管理员
目标：模型供应商、节点、价格、任务、用户、审核、财务。

## 2. 首页核心入口

核心输入框：

> “你想做什么？”

示例：

- 把这篇小说做成 5 分钟悬疑漫剧
- 给我做一个产品宣传视频
- 把这张人物立绘做成 8 秒动作镜头
- 把下面文章做成 3 分钟知识解说视频

用户提交后进入 Router Agent。

## 3. Router Agent 产品逻辑

Router Agent 是用户需求进入系统后的第一道入口。

输入：自然语言 + 附件 + 用户偏好 + 历史项目上下文。

输出：

- 内容类型
- 推荐路线
- 推荐 Skill
- 推荐 Director
- 预计复杂度
- 预计成本区间
- 是否需要用户补充信息

初始路线：

```text
NOVEL_TO_ANIME
SCRIPT_TO_VIDEO
IMAGE_TO_VIDEO
VIDEO_EXPLAINER
PRODUCT_VIDEO
SHORT_VIDEO
CUSTOM
```

Router 只负责“分类与路由”，不要承担具体生产任务。

## 4. Director Agent

Director 是整个创作项目的“总导演/项目经理”。

职责：

1. 读取 Router 输出
2. 选择或推荐 Skill
3. 制定阶段计划
4. 决定需要哪些专业 Agent
5. 决定模型能力需求
6. 判断哪些步骤需要人工确认
7. 管理重试与返工
8. 维护生产状态
9. 对结果做质量检查
10. 最终交付

Director 不直接生成所有内容，而是调度 Agent + Tools。

## 5. 审核门槛

默认 Human Gate：

```text
Gate 0 需求路线
Gate 1 故事结构
Gate 2 角色
Gate 3 世界/场景
Gate 4 剧本
Gate 5 分镜
Gate 6 关键视觉
Gate 7 视频片段
Gate 8 最终成片
```

用户可以在项目级配置：

- 严格模式：每个 Gate 都确认
- 标准模式：关键 Gate 确认
- 自动模式：仅异常时停顿

## 6. MVP 页面

1. 登录/注册
2. Dashboard
3. 新建项目
4. 创作工作台
5. Agent 运行面板
6. 角色管理
7. 场景管理
8. 分镜管理
9. 资产库
10. Skill 库
11. 任务中心
12. Credits
13. 节点/服务器
14. 管理后台基础页

## 7. MVP 不做

- 社交社区
- Marketplace 交易系统
- 完整视频编辑器
- 多租户企业组织体系
- 复杂计费订阅
- 大规模推荐系统

这些留到后续阶段。
