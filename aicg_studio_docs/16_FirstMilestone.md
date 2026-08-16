# 第一里程碑：真正可运行的纵向切片

## 目标

先不要追求“很多模型”。

要完成一条完整链路：

```text
用户
 ↓
创建项目
 ↓
输入一句创作需求
 ↓
Router Agent
 ↓
Director Agent
 ↓
Story Agent
 ↓
人工确认
 ↓
Character Agent
 ↓
人工确认
 ↓
Storyboard Agent
 ↓
人工确认
 ↓
Mock Image Provider
 ↓
Asset Library
 ↓
Mock Video Provider
 ↓
最终视频 Asset
```

## 完成标准

用户可以在浏览器里完整操作一次，并且：

- 页面状态实时变化
- Agent 状态可视化
- 每步可确认
- 可修改
- 可重试
- 可取消
- 任务失败可恢复
- 所有结果进入资产库
- 所有过程可追踪
- Credits 能正确 reserve/settle/release

## 为什么先做这个

这条纵向切片可以验证产品最核心的风险：

> “Agent 自动制片 + 人工确认 + 异步生成”是否真的能跑通。

当这条主链跑通后，再不断增加真实模型、ComfyUI、GPU 和 Skill。
