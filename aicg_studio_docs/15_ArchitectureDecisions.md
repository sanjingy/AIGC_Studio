# Architecture Decision Records

## ADR-001 Router 与 Director 分离

决定：分离。

原因：Router 是“路线判断器”，Director 是“项目执行者”。两者职责完全不同。

## ADR-002 Agent 不直接选择具体模型

决定：Agent 只声明 capability，Provider Registry 决策实际模型。

原因：避免模型变化导致 Agent 代码频繁修改。

## ADR-003 ComfyUI 作为 Runtime

决定：ComfyUI 属于 Runtime，不属于业务 Agent。

原因：未来可以接本地、远程、SSH、GPU 节点。

## ADR-004 AutoDL 作为 Cloud Provider

决定：AutoDL 只实现 CloudProvider Adapter。

原因：未来可加入其他 GPU 平台。

## ADR-005 Credits 使用 Ledger

决定：不可变交易流水 + reserve/settle/release。

原因：防止并发、重试、退款导致余额错误。

## ADR-006 Skill 可组合

决定：Skill 不是单一 Prompt，而是生产流程定义。

## ADR-007 Node Agent 主动连接平台

决定：长期优先采用 outbound connection。

原因：更安全，不需要平台暴露 SSH 入站端口。
