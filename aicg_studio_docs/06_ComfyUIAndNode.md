# ComfyUI / 本地节点 / SSH 节点设计

## 1. ComfyUI 支持结论

ComfyUI 可以通过 HTTP `/prompt` 提交执行请求，并通过 WebSocket 接收 executing/progress 等事件；官方仓库同时提供 OpenAPI 定义。citeturn905503search2turn905503search3

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

## 4. SSH

平台不要保存用户长期 SSH 私钥明文。

MVP 可以：

- 用户上传受限密钥
- 加密存储
- 只允许指定 host
- 只允许 Node Agent bootstrap

长期建议改为：

```text
服务器主动出站连接平台
```

即：

```text
Node Agent → Platform
```

而不是平台反向 SSH 用户服务器。

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
