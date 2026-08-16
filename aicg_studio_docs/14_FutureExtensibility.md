# 长期扩展方向

## 1. Public API

预留 OpenAI-like API。

## 2. Skill Marketplace

用户可以发布 Skill，平台抽佣。

## 3. Workflow Marketplace

ComfyUI Workflow、LoRA、模型配置可以成为独立商品。

## 4. GPU Marketplace

允许其他 GPU 提供者注册机器，形成算力池。

## 5. Multi-tenant

企业团队：

```text
Organization
├── users
├── projects
├── credits
├── models
├── skills
└── runtimes
```

## 6. ~~Project Memory / 一致性~~ → 已提升为核心

> 2026-08-16 修订：原本列在这里的"人物一致性、风格约束"
> **已提升为核心模块**，见 `17_ConsistencyEngine.md` 与 ADR-012。
>
> 它不是长期扩展方向，它是产品能不能用的生死线，M1 就要交付。

本节仅保留真正属于长期的部分：

- 跨项目的世界观复用（同一 IP 的多部作品共享设定）
- 用户偏好的长期学习（镜头语言习惯、审美倾向）

## 7. ~~Automatic QA~~ → 部分提升为核心

> 一致性的**量化指标**（角色相似度、风格距离、时序稳定性、字幕对齐）
> 已移入 `17_ConsistencyEngine.md` 第 6 节，M1–M2 交付。

本节保留真正的长期方向——**多模态主观评审**：

```text
镜头语言质量（构图、景别节奏）
情绪表达准确性
叙事连贯性
整体观感评分
```

这类主观评价目前的模型还做不可靠，等能力成熟再说。
