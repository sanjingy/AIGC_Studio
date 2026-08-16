# ComfyUI / 本地节点设计

> 目标里程碑：**M4**。M1–M3 不实现本文档内容。

## 1. ComfyUI 支持结论

ComfyUI 可以通过 HTTP `/prompt` 提交执行请求，并通过 WebSocket 接收
`executing` / `progress` / `executed` 等事件；官方仓库同时提供 OpenAPI 定义。

参考：<https://github.com/comfyanonymous/ComfyUI>（接入前需重新核实接口现状）

因此 AICG Studio 必须把 ComfyUI 封装成标准 Runtime Adapter，而不是把 ComfyUI 特殊逻辑散落到各业务模块。

## 2. Local ComfyUI

适用：平台开发机、用户本地电脑。

连接：

```text
AICG Web
  ↓
AICG Local Node Agent
  ↓
localhost:8188
  ↓
ComfyUI
```

浏览器不应该直接暴露本地 ComfyUI 给公网业务层。

## 3. Remote ComfyUI

推荐：

```text
AICG Cloud
    ↓
Node Agent / Secure Tunnel
    ↓
Remote ComfyUI
```

Node Agent 提供：

- register
- heartbeat
- GPU info
- model inventory
- workflow submission
- result upload
- log stream
- cancel
- update

## 4. SSH：不做入站（ADR-007 修订）

> 2026-08-16 决定：**取消"MVP 先做入站 SSH、长期改出站"的过渡方案，
> 只做出站一种。**

原方案打算 MVP 先托管用户 SSH 私钥、由平台反向连接用户服务器，
长期再改为出站。这会产生一笔无法回收的安全债务：
一旦保管过用户私钥，泄露风险和合规责任就已经产生，改架构也消不掉。

**唯一方案：**

```text
Node Agent → 出站连接 → Platform
```

平台永不持有用户 SSH 私钥，永不主动连接用户机器。
用户机器只需要能访问外网，不需要开放任何入站端口、不需要公网 IP。

`09_Database.md` 中的 `ssh_credentials` 表**不落地**。

## 5. Node Agent

安装方式目标：

```bash
aicg-node install
aicg-node login
aicg-node register
```

注册后生成：

```text
node_id
node_secret
```

Agent 启动：

```text
heartbeat
↓
report GPU
↓
report runtime
↓
report ComfyUI
↓
wait task
```

## 6. Node 状态

```text
PROVISIONING
ONLINE
BUSY
DRAINING
OFFLINE
ERROR
```

## 7. Workflow 提交

工作流提交必须记录：

- workflow_id
- workflow_version
- normalized_prompt
- runtime_node
- model dependencies
- input assets
- output assets
- task_id
- provider cost

## 8. 安全

不要允许用户通过 workflow 参数执行：

- arbitrary shell
- host file traversal
- 任意网络代理
- 容器逃逸

Workflow 应经过 schema validation 和 allowlist。
