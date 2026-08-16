# 数据库设计（初版）

数据库：PostgreSQL。

## 1. 用户

### users

```text
id UUID PK
email
password_hash
status
role
created_at
updated_at
```

### user_preferences

```text
user_id PK/FK
language
default_mode
production_policy
```

## 2. 项目

### projects

```text
id
user_id
title
route_type
skill_id
status
current_state_json
created_at
updated_at
```

### project_versions

保存项目关键快照。

## 3. 内容实体

```text
characters
character_versions
scenes
scene_versions
scripts
script_versions
storyboards
storyboard_versions
shots
shot_versions
```

所有内容实体必须具备：

```text
version
status
created_by
created_at
```

## 4. Assets

### assets

```text
id
project_id
owner_user_id
type
storage_key
mime_type
size
metadata_json
created_at
```

### asset_versions

保存衍生版本和父资产。

## 5. Skills

```text
skills
skill_versions
skill_dependencies
skill_permissions
```

## 6. Models

```text
providers
models
model_capabilities
model_pricing
```

## 7. Runtime

```text
runtime_nodes
runtime_capabilities
runtime_health
runtime_tasks
```

## 8. Tasks

```text
tasks
  id
  project_id
  type
  status
  priority
  provider_id
  model_id
  runtime_id
  input_json
  output_json
  estimated_cost
  reserved_cost
  actual_cost
  idempotency_key
  retry_count
  error_code
  created_at
  started_at
  finished_at
```

## 9. Agent

```text
agent_runs
agent_steps
agent_messages
agent_tool_calls
approvals
```

## 10. Billing

```text
credit_accounts
credit_transactions
orders
pricing_rules
provider_cost_records
```

## 11. Cloud GPU

```text
gpu_products
gpu_instances
gpu_usage
cloud_provider_accounts
```

## 12. 凭证

```text
api_credentials
node_credentials
ssh_credentials
```

敏感字段加密，不进入普通日志。
