# Codex 推荐代码仓库结构

```text
repo/
├── apps/
│   ├── web/                       # Next.js
│   └── api/                       # FastAPI
│
├── services/
│   ├── agent-service/
│   ├── task-service/
│   ├── ai-gateway/
│   ├── runtime-service/
│   ├── asset-service/
│   ├── billing-service/
│   └── cloud-service/
│
├── packages/
│   ├── contracts/                 # OpenAPI/Pydantic schema
│   ├── domain/
│   └── sdk/
│
├── agents/
│   ├── router/
│   ├── director/
│   ├── story/
│   ├── character/
│   ├── scene/
│   ├── storyboard/
│   ├── prompt/
│   ├── image/
│   ├── video/
│   ├── audio/
│   ├── editing/
│   └── qa/
│
├── adapters/
│   ├── providers/
│   ├── runtimes/
│   │   ├── comfyui/
│   │   ├── vllm/
│   │   ├── ollama/
│   │   └── ssh-node/
│   └── clouds/
│       ├── autodl/
│       └── generic/
│
├── skills/
│   ├── official/
│   └── examples/
│
├── node-agent/
│
├── workflows/
│
├── migrations/
├── infra/
│   ├── docker/
│   ├── nginx/
│   └── observability/
│
├── tests/
│
├── docs/
└── scripts/
```

## 目录原则

不要出现：

```text
utils_everything.py
ai.py
agent.py
```

所有领域能力必须落到明确模块。
