# Model / Provider / Runtime 统一协议

## 1. Capability

标准能力枚举：

```text
text_generation
vision_understanding
image_generation
image_editing
text_to_video
image_to_video
video_upscale
text_to_speech
speech_to_text
music_generation
sound_effect_generation
video_editing
```

## 2. Provider

示例：

```json
{
  "id": "provider.example",
  "name": "Example Provider",
  "type": "openai_compatible",
  "base_url": "https://example.com/v1",
  "auth_type": "api_key"
}
```

## 3. Model

```json
{
  "id": "model.example.video",
  "provider_id": "provider.example",
  "capabilities": ["text_to_video"],
  "pricing": {
    "unit": "second",
    "provider_cost": 120,
    "credit_price": 200,
    "effective_from": "2026-08-16T00:00:00Z"
  }
}
```

> **价格字段约定（ADR-014）**：
> - 金额一律整数，单位为最小单位（此处 120 = ¥1.20 = 120 Credits 成本），
>   **禁止浮点数**
> - 必须带 `effective_from`，支持历史订单按当时价格核算
> - 价格从 `model_pricing` 表读取，**代码中不得出现价格常量**
>
> 理由：上游调价是常态。DeepSeek 于 2026-08-17 起高峰输出价上涨 350%，
> 任何硬编码的实现会在一夜之间毛利转负。见 `19_UnitEconomics.md`。

## 4. Runtime

Runtime 是实际运行环境：

```text
cloud_api
local_api
comfyui_local
comfyui_remote
ssh_node
vllm
ollama
```

## 5. Adapter 统一接口

```python
class RuntimeAdapter(Protocol):
    async def health(self) -> HealthResult: ...
    async def submit(self, request: RuntimeRequest) -> TaskHandle: ...
    async def status(self, task_id: str) -> TaskStatus: ...
    async def cancel(self, task_id: str) -> None: ...
    async def fetch_result(self, task_id: str) -> RuntimeResult: ...
```

## 6. Provider Failover

同一 capability 可注册多个 Provider：

```text
text_to_video
 ├── provider A priority 100
 ├── provider B priority 90
 └── comfyui runtime priority 50
```

选择依据：

- 用户指定
- Skill 偏好
- **渲染档位（预览档 / 定稿档，ADR-015）**
- 可用性
- 价格
- 队列长度
- GPU 状态
- 质量评分
- 失败率

**Failover 的触发条件**（原文档缺失，导致 failover 落不了地）
见 `21_ErrorTaxonomy.md` 第 4 节：熔断阈值、DEGRADED/DOWN 状态转换、
探测恢复策略。熔断状态必须存 Redis 供全 Worker 共享，
否则每个 Worker 各自熔断等于没熔断。

## 7. Secrets

API Key 不写入数据库明文日志。

使用：

- Secret manager
- 加密字段
- 环境变量
- KMS（生产）
