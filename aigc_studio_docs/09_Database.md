# 数据库设计

数据库：PostgreSQL 16+ / pgvector

> 2026-08-16 修订要点：
> 1. 全表加 `org_id`（多租户是既定目标，现在加零成本，以后加是地狱级迁移）
> 2. 全表加 `deleted_at` 软删除
> 3. 补齐**时间线**模型（原文档完全缺失，但它是成片的核心产物）
> 4. 补齐**一致性**模型（见 `17_ConsistencyEngine.md` 第 7 节）
> 5. 明确 `pgvector` 的用途：角色人脸 embedding，不是 RAG
> 6. 补索引与归档策略

---

## 0. 全局约定

所有业务表必须包含：

```sql
id          UUID PRIMARY KEY DEFAULT gen_random_uuid()
org_id      UUID NOT NULL              -- 多租户预留，个人用户 = 其个人 org
created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
deleted_at  TIMESTAMPTZ                -- 软删除，NULL = 有效
```

约定：

- **金额一律 `BIGINT`，单位为最小单位（Credits 的 1/1，即分的 1/1）**，
  绝对禁止 `FLOAT`/`REAL` 存钱
- 时间一律 `TIMESTAMPTZ`，存 UTC
- 枚举用 `TEXT` + CHECK 约束，不用 PG ENUM（改起来痛苦）
- 所有外键显式建索引（PG 不自动建）
- 所有 `org_id` 建复合索引前缀：`(org_id, ...)`

---

## 1. 用户与租户

```sql
organizations
  id, name, type ('personal'|'team'), owner_user_id, status

users
  id, org_id, email UNIQUE, password_hash, status, role,
  realname_status, realname_verified_at        -- 合规预留

user_preferences
  user_id PK, language, default_mode, production_policy_json
```

---

## 2. 项目

```sql
projects
  id, org_id, owner_user_id, title,
  route_type, skill_id, status,
  style_profile_id,                    -- 风格锁定
  current_state_json,
  budget_cap_credits BIGINT,           -- 成本熔断
  spent_credits      BIGINT DEFAULT 0
  INDEX (org_id, status, created_at DESC)

project_versions
  id, project_id, version, snapshot_json, created_by
```

---

## 3. 内容实体

```sql
characters / character_versions
scenes / scene_versions
scripts / script_versions
storyboards / storyboard_versions
shots / shot_versions
```

统一版本字段：`entity_id, version, status, content_json, created_by, created_at`。

---

## 4. 一致性（新增，核心）

> 详见 `17_ConsistencyEngine.md` 第 7 节。

```sql
character_profiles
  id, org_id, project_id, character_id, version,
  appearance_json,                     -- 结构化外貌，非自然语言
  base_portrait_asset_id,              -- 基准立绘
  turnaround_asset_ids  UUID[],        -- 三视图
  expression_asset_ids  UUID[],
  outfit_asset_ids      UUID[],
  lora_artifact_id,                    -- M4
  consistency_tier TEXT,               -- L0/L1/L2/L3
  face_embedding vector(512),          -- ★ pgvector 的真实用途
  locked_at
  INDEX (project_id, character_id, version DESC)

style_profiles
  id, org_id, project_id, version,
  base_model, style_lora,
  positive_tokens, negative_tokens,
  sampler, cfg, seed_strategy,
  color_grading, line_weight, render_mode,
  locked_at

shot_conditioning
  shot_id PK,
  character_profile_ids UUID[],
  style_profile_id,
  reference_asset_ids   UUID[],
  seed BIGINT,
  resolved_prompt TEXT NOT NULL         -- ★ 必存，用于排查与复现
  negative_prompt TEXT

shot_quality_scores
  id, shot_id, asset_id,
  metric TEXT,                          -- face_similarity / style_distance / ...
  value REAL, threshold REAL, passed BOOL,
  measured_at
  INDEX (shot_id, metric)
```

---

## 5. 时间线（新增，核心）

> 原文档把最终成片当成"Video Agent 的输出"，实际上成片是**时间线渲染的产物**。
> 没有这组表，`12_MVP_Roadmap.md` 的 M2 无从实现。

```sql
timelines
  id, org_id, project_id, version,
  fps INT, resolution TEXT,
  total_duration_ms INT,
  status,                               -- draft/rendering/rendered/failed
  rendered_asset_id                     -- 成片

timeline_tracks
  id, timeline_id,
  kind TEXT,                            -- video/voice/bgm/sfx/subtitle
  index INT,                            -- 层级顺序
  muted BOOL, volume REAL

timeline_clips
  id, track_id, shot_id,
  start_ms INT, duration_ms INT,        -- 在时间线上的位置
  source_asset_id,
  source_in_ms INT, source_out_ms INT,  -- 素材内的入出点
  transition_in  TEXT, transition_out TEXT,
  transform_json,                       -- 缩放/位移/运镜
  text_content TEXT,                    -- 字幕轨专用
  is_placeholder BOOL DEFAULT false     -- 失败镜头的占位（见 21 号文档第 6 节）
  INDEX (track_id, start_ms)

audio_segments
  id, project_id, shot_id, character_id,
  text TEXT, voice_id TEXT,
  asset_id, duration_ms INT,            -- ★ 真实时长，反推镜头时长的依据
  vad_boundaries_json                   -- 用于字幕对齐
```

**约束**：`shots.duration_ms` 必须由 `audio_segments.duration_ms` 推导，
不允许先定镜头时长再配音。这是音频优先流程的落库体现。

---

## 6. Assets

```sql
assets
  id, org_id, project_id, owner_user_id,
  type,                                 -- image/video/audio/text/lora/workflow
  storage_key, mime_type, size_bytes,
  width, height, duration_ms,
  checksum,                             -- 去重
  metadata_json,
  moderation_status,                    -- 合规预留：pending/passed/rejected
  watermarked BOOL DEFAULT false,       -- 合规预留
  retention_until                       -- 生命周期
  INDEX (project_id, type, created_at DESC)
  INDEX (org_id, checksum)

asset_versions
  id, asset_id, parent_asset_id, version, derivation_json
```

---

## 7. Skills / Models / Runtime

```sql
skills, skill_versions, skill_dependencies, skill_permissions

providers, models, model_capabilities
model_pricing
  model_id, unit,                       -- token/second/image/call
  provider_cost BIGINT,                 -- 成本（最小单位）
  credit_price  BIGINT,                 -- 售价（Credits）
  effective_from, effective_to          -- ★ 支持历史价格追溯
  INDEX (model_id, effective_from DESC)

pricing_rules
  id, key, value_json, effective_from   -- retry_factor、overhead_rate、熔断阈值
                                        -- 热更新，禁止写进代码

runtime_nodes, runtime_capabilities, runtime_health, runtime_tasks
```

> `model_pricing` 带生效时间是必需的：
> 上游调价后（如 DeepSeek 2026-08-17），历史订单的成本核算必须按当时价格。

---

## 8. Tasks

```sql
tasks
  id, org_id, project_id, shot_id,
  type, status, priority,
  provider_id, model_id, runtime_id,
  input_json, output_json,
  estimated_cost BIGINT, reserved_cost BIGINT, actual_cost BIGINT,
  idempotency_key TEXT UNIQUE,
  attempt INT, max_attempts INT,
  error_code TEXT, error_detail_json,
  schedule_window TEXT,                 -- 峰谷调度（见 19 号文档 1.3）
  counts_as_waste BOOL,                 -- ★ 是否计入废片率
  created_at, started_at, finished_at
  INDEX (project_id, status)
  INDEX (status, priority DESC, created_at)     -- 队列扫描
  INDEX (created_at)                            -- 归档
```

**归档策略**：`tasks` 是增长最快的表（一部片子 200+ 行）。

```text
> 90 天且已终态 → 归档到 tasks_archive（或按月分区 + DETACH）
成本统计在归档前先聚合进 daily_cost_rollup
```

---

## 9. Agent

```sql
agent_runs
  id, org_id, project_id, agent_type, status,
  input_json, output_json, model_id,
  tokens_in, tokens_out, cost BIGINT,
  parent_run_id

agent_steps
  id, run_id, step_index, kind, content_json, duration_ms

agent_tool_calls
  id, step_id, tool_name, args_json, result_json, error

approvals
  id, project_id, gate TEXT, status,
  payload_ref, requested_at,
  resolved_at, resolved_by, decision, comment
```

> **`tasks` 是执行状态的唯一权威**（见 ADR-008）。
> `agent_runs` 记录推理过程，`approvals` 记录人工决策，
> 三者职责不重叠，不允许在两处维护同一个状态。

---

## 10. Billing

```sql
credit_accounts
  id, org_id, resource_type,            -- AI_CREDITS / GPU_SECONDS
  balance BIGINT, reserved BIGINT
  UNIQUE (org_id, resource_type)

credit_transactions                     -- 不可变，只 INSERT
  id, account_id, type,                 -- TOPUP/RESERVE/CONSUME/RELEASE/
                                        -- REFUND/ADJUST/GPU_CHARGE/ADMIN_GRANT
  amount BIGINT,                        -- 有符号
  balance_after BIGINT,
  ref_type, ref_id,                     -- 关联 task / order
  idempotency_key TEXT UNIQUE,
  is_bonus BOOL,                        -- ★ 赠送额度分账，退款只退本金
  created_at
  INDEX (account_id, created_at DESC)

orders
provider_cost_records                   -- 真实上游成本，用于回填成本模型
daily_cost_rollup                       -- 聚合表，支撑成本看板
```

**并发安全**：扣费必须 `SELECT ... FOR UPDATE` 锁账户行，
或用乐观锁 + 版本号重试。绝不允许读-改-写无锁。

---

## 11. Cloud GPU（预留）

```sql
gpu_products, gpu_instances, gpu_usage, cloud_provider_accounts
```

方案待定，见 `07_AutoDL.md`。表结构先不落地。

---

## 12. 凭证

```sql
api_credentials, node_credentials, ssh_credentials
```

要求：

- 密文字段用 `pgcrypto` 或应用层 AES-GCM 加密，密钥走 KMS / 环境变量
- 加密字段命名统一加 `_encrypted` 后缀，便于审计扫描
- 日志脱敏中间件必须覆盖这些字段名
- 明文密钥**永不出现在** `agent_steps` / `tasks.input_json` 中

---

## 13. 评估（见 `22_AgentEval.md`）

```sql
eval_runs, eval_results, eval_baselines
```

---

## 14. 实现要求

- 表结构以 **SQLAlchemy 模型 + Alembic migration 为唯一真相**，
  本文档是设计说明，不是可执行定义
- 每个 migration 必须可回滚且经过测试
- 生产环境 migration 前必须备份
