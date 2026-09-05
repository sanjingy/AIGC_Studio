# AIGC Studio — 开发者/AI 会话上手指南

面向普通用户的 AI 内容生产操作系统：用户描述目标，系统编排 Agent 完成
从小说到成片的整条生产链。主线是**小说 → 5 分钟悬疑漫剧**。

---

## 先读什么

不要一上来就改代码。按这个顺序读，大概 20 分钟。
**`project_docs/` 是唯一入口**（2026-09-02 起），`aigc_studio_docs/` 除 ADR 外都是历史资料：

| 顺序 | 文档 | 为什么先读它 |
|---|---|---|
| 1 | `project_docs/00_CURRENT_STATUS.md` | 前半页是产品终点：用户是谁、输入什么、终点是"逐镜带配音的 MP4"、明确不做什么 |
| 2 | `project_docs/DECISIONS_2026-09-02.md` | 本轮大改的全部决策，与任何文档冲突时以它为准 |
| 3 | `aigc_studio_docs/15_ArchitectureDecisions.md` | 33 条 ADR，全部有效。ADR-030 ~ 033 是本轮新增 |
| 4 | `project_docs/04_MODULE_ROADMAP.md` | 当前在哪一波、黄金路径是什么 |
| 5 | `project_docs/modules/` | 要改哪个模块就读哪份 |
| 6 | `aigc_studio_docs/19_UnitEconomics.md` | 钱怎么算。**盈亏平衡 = 每天 16 部成片**（"部"的定义随 ADR-032 待重估） |
| 7 | `aigc_studio_docs/17_ConsistencyEngine.md` | 核心技术难点，也是唯一的护城河。§8.1 有实测数据 |

下面"当前进度"一节记录的是 S1 ~ S14 的实施史，仍然准确，但**方向以 `project_docs` 为准**。

---

## 跑起来

需要 Docker Desktop 运行中。

```bash
cp .env.example .env      # 首次；Provider Key 见下方说明
docker compose up -d      # 后端 5 个容器
cd apps/web && npm run dev
```

| 服务 | 地址 |
|---|---|
| 前端 | http://localhost:3000 |
| API 文档 | http://localhost:8000/docs |
| MinIO 控制台 | http://localhost:9001 |

`npm run dev` 报 `EADDRINUSE` 说明已经有一个在跑，直接用即可；要重启先
`npx kill-port 3000`。

**Provider Key** 在 `.env`（已 gitignore，从未入库）。没有 Key 也能跑通全链路：
文本退回 `MockLLM`（`agent/llm.py::get_provider`），出图退回 Mock 出图
（`gateway/mock_image.py`，2026-09-05 补上，在此之前没配 DashScope Key 时出图 100% 失败）。
两处规则相同：`ENV=test` 一律 Mock、有 Key 走真实 Gateway、没 Key 用 Mock。
无 Key 时出的是标注了提示词指纹的 1024×1024 占位图，同提示词稳定同图；
它和真图一样要扣 Credits（计费链路上没有 Mock 特例），只是不产生上游费用。

常用命令：

```bash
docker compose exec api pytest -q                       # 全量测试
docker compose exec api ruff check . && docker compose exec api mypy apps worker packages agents adapters skills
docker compose exec api alembic revision --autogenerate -m "xxx"
docker compose exec api alembic upgrade head
docker compose exec api python scripts/validation_slice.py --dry-run
```

---

## 当前进度

**M1 已完成**（8 步，12 个 commit，321 个测试全绿）：

| 步骤 | 内容 |
|---|---|
| S1 | 工程骨架：FastAPI 单体 + Arq Worker + PG/pgvector + Redis + MinIO |
| S2 | 账号体系：argon2、httpOnly Cookie、refresh 轮换 + 重放检测 |
| S3 | 项目与资产：预签名直传、MIME 白名单、跨租户 404 |
| S4 | 任务流水线：状态机、幂等、Transactional Outbox、SSE |
| S5 | 计费 Ledger：reserve/settle/release、行锁、四层熔断 |
| S6 | AI Gateway：DeepSeek + 万相、failover、熔断、真实价格入库 |
| S7 | Agent 编排：声明式 AgentSpec、可插拔第三方 Agent、3 道审核门 |
| S8 | 一致性引擎：风格锁定、提示词合成、embedding 度量、验证切片 |

**S9 Skill 层（M1 之后补，ADR-020~024）**：`skills/` 声明式生产模板。
默认 Skill `skill.novel_to_anime.v1` 把主线拆成 26 个阶段 + 5 道门。

**S10 编排器改走三份提示词的步骤**：

```
路线 → 情节目录 → 剧本 →【确认剧本】→ 角色档案 → 场景档案 → 分镜 →【确认分镜】
```

对应 5 个 Agent：`story.plot_index.v1` / `story.screenplay.v1` /
`visual.character.v1` / `visual.scene.v1` / `visual.storyboard.v1`，
在 `orchestrator._SPEC_OF` 里**钉死到具体 id**（同 role 下有多个 Agent，
`default_for` 只能取一个）。分镜产出 9 列：镜号/节点/景别/角度/运镜/
画面内容/出场人物/场景/对白+音效。

`story.default.v1` 与 `visual.default.v1` 已不在主链路上，暂留作
Skill 层的 role 兜底。**Skill 运行时仍未接线**，阶段图还硬编码在
`orchestrator._NEXT` 里，只是顺序已与 Skill 声明一致。

**前端**：`/login`、`/dashboard`、`/projects/[id]`、`/tasks`、`/assets`、
`/settings/keys` 已接真实接口。

**S11 出图接上界面**：角色阶段跑完后编排器把产出写进一致性引擎
（`ensure_style` / `upsert_characters`），角色出图入口在项目页**右栏**、
分镜出图在分镜表的「画面」列，走 `POST /projects/{id}/images/characters/{ref}`
和 `.../images/shots/{index}`——提示词一律由 `consistency.compose` 合成，
前端传不了也不该传风格词。进度走项目 SSE，出图落 `assets` 表。
场景出图和视频当时不在内（场景一致性还没设计）。**`feat/freeflow-prototype`
分支上已经补上场景一致性资产流水线**（`scene_profiles` 表、
`consistency.sync_from_scenes_output`、`compose.describe_scene`/
`reference_scene_prompt`，一致性锚点用 `SceneSheet` 早就有的
`camera_axis`/`fixed_references` 两个字段），出图端点
`POST /projects/{id}/images/scenes/{ref}`，`compose_shot` 现在也会把
场景信息注入镜头提示词（可选参数，缺场景档案时优雅降级为旧行为）。
视频仍是 M2 待办。

**S12 工作台 v2 布局**：项目页从"一列面板"改成四栏工作台，
版式见 `design-system/aigc-studio/MASTER.md` §5。要点：

- 顶栏横贯整屏，面包屑是「项目 › 第 N 集」；阶段进度移到左栏「流程」，
  **只读**（阶段图在后端 `orchestrator._NEXT` 里，前端重排不了）
- 中栏**只有对话**：每跑完一步在对话流里留一张结果卡片，点开从右侧滑出
  抽屉看完整内容（`output-card.tsx` / `output-drawer.tsx`）。抽屉宽 1100，
  分镜表 10 列在里面放得下——它挤在 680 宽的对话流里只能横向滚着看
- 左栏「流程」和右栏「看档案」用 `#group-xxx` 锚点打开抽屉。外壳在项目页
  的上层，回调传不下去，锚点是两边都能写、页面能听的公共通道
- 右栏可折叠：1440 屏上中栏只有 640，收起右栏把 352 还给中栏
- 右栏是项目资产：角色可出图，道具没有。场景现在也能出图了
  （`feat/freeflow-prototype`），入口在中栏抽屉的场景档案里而不是右栏——
  右栏 `AssetPanel` 的既有规则是"出图入口只有一个"，场景没有对应的右栏卡片，
  和角色不对称，是有意的，见该分支的场景出图交付记录
- 「集」不是后端实体，只是 `screenplay.episodes` 里的一项。项目栏里选一集
  只筛中栏剧本视图的显示，不改变任何生产范围，所以也不能增删
- 设计稿原件在 `design-system/aigc-studio/项目布局导入与设计规划/`

稿子里有而**没做**的，都是因为后端没有对应能力，做了就是假入口：
拖拽编排阶段（阶段图后端硬编码）、道具（`agents/schemas.py` 里没有这个
产出）、参考图上传（advance/revise 只收文本）、视频生成（M2）。
（场景出图原本也在这个列表里，`feat/freeflow-prototype` 分支已经补上，见上。）

**S13 Skill 上传（ADR-026）**：`apps/api/modules/skill/` 补上了
service/repository/router，接口四个：

```
GET    /skills            本 org 传过的
POST   /skills            传一份 YAML
GET    /skills/{id}/spec  取回原文
DELETE /skills/{id}       软删
```

几条不能改的：

- 校验直接复用 `skills/spec.py`，**不新增豁免**。那套白名单（处理器、
  导出路径、门、能力、重试上限）本来就是为不可信输入写的
- **校验不过也是 201 入库**，`status=invalid` + 错误原文。丢掉它用户只会
  得到"传了没反应"的黑洞。真失败只有一种：根本不是 YAML（400）
- **上传不落 `skills/custom/`**。那个目录是进程级注册表，写进去等于对所有
  租户生效。用户传的一律只进 `org_skills`，按 org_id 隔离
- 前端入口在输入框的 `+` 附件菜单里，左栏「技能库」只列清单。两处都必须
  写着**运行时尚未接线**（ADR-026 的验收标准），`runtime_wired` 由后端给，
  不是前端写死的文案

**没做**：ADR-026 里的"项目内选择 Skill"。那要给 `projects` 加一列，
且选了也不生效，这次没碰。

**S14 基准图不止能生成**：角色基准立绘和场景参考图现在有三条来路——
AI 生成（原有）、从资产库选一张、从本地传一张。后两条走
`PUT /projects/{id}/images/characters/{ref}` 和 `.../scenes/{ref}`，
入参只有 `{asset_id}`。

- **PUT 不是 POST**：POST 到同一路径是"再生成一张"（每次扣 Credits、
  结果不同），PUT 是"基准图就是它"（幂等）。用同一个动词会让
  "点两下多扣一次钱"和"点两下没事"混在一起
- **这条路径一分钱不花**：不建任务、不预扣、不结算，`billing`
  在它的调用链上一行都不执行。`test_base_image_assign.py` 里每个用例都在
  断言反面（无 Task、无 Ledger 流水、余额不动），因为唯一的风险就是
  哪天有人把它接回 `create_task`
- `GET /projects/{id}/images` 现在合并两个来源：`tasks` 里的生成记录
  + 档案上钉的基准图。多出 `source`（`generated` / `assigned`），
  `task_id` 因此**可空**——钉的那条没有任务，重试按钮要靠它判断。
  合并后按时间倒序，前端"取第一条就是当前这一版"仍然成立
- 换基准图**不递增 `version`**（那是"角色设定改了"用的），
  也**不删旧资产**（用户可能想换回去）
- 前端入口是 `RenderSlot` / 右栏角色卡上的「用已有图」下拉，
  两个落点共用 `BaseImageActions`。菜单走 portal 画到 body：右栏角色卡
  是 `overflow-hidden`，绝对定位的浮层会被裁掉只剩一条边
- 上传复用资产模块现成的三段式直传（`assets.upload`），MIME 白名单、
  大小上限、容量配额全在那条链路上，前端不另写一套阈值

顺带发现的既有缺口（**没修**，不在范围内）：`base_portrait_asset_id` /
`base_reference_asset_id` 这两列在此之前**从来没有任何代码写过**——
AI 出图成功后也不回写。所以"AI 生成"那条路仍然只体现在 `tasks` 上，
`reference_embedding` 同理还是空的。要让两条路完全对称，得在出图完成
回调里补一次回写。

**2026-09-03 起旧壳已删（ADR-030）**：`app/(app)/`、`components/shell/`、四栏工作台
`project-composer` 全部删除，`/dashboard` 只剩重定向到 `/freeflow`。上面 S11 / S12 里
关于「项目页右栏 / 中栏抽屉」的入口描述是历史，现在唯一的壳是 `/freeflow`，
推进 / 审批 / 返工 / 出图 / 任务 / 上传都在里面，状态表见
`project_docs/modules/11_WEB_WORKBENCH.md` §3.1。

**`feat/freeflow-prototype` 分支（未合并 `main`）**：设计包
「分支B·自由工作流」的前端可点击原型——`/freeflow` 独立路由下的
全局两级 IA（左侧常驻导航）+ 节点画布（`@xyflow/react`）+ 素材库 +
分镜编辑 + 任务中心 + 项目设置 + 悬浮 AI 助手，主线四栏工作台
`app/(app)/...` 一个字没动。REQ-000（要不要替换主线工作台）**仍未决定**，
这条分支只验证方向，见 `design-system/项目布局导入与设计规划.zip`。
前端范围内的门禁全绿；节点图存储与执行引擎、Skill 接入执行引擎、
跨类型资产统一索引三项按需求文档本来就是后端未设计项，没做。

同一分支上顺手补了三处主线也受益的真实后端能力（都不是原型专属）：

- **项目删除**：`DELETE /projects/{id}` 软删其实早就实现了
  （`repo.soft_delete`），只是前端 `lib/api.ts` 没封装出来，界面因此
  长期显示"未接入"。
- **场景一致性资产流水线**：见上方 S11 段落更新。
- **ADR-024 项目级模型偏好接线**：`projects.model_preference` 这个
  JSONB 字段和 ADR 早就有了，从来没人接到 Gateway 的路由解析上。
  这次接上，`gateway._resolve` 按偏好重排候选路由（不是过滤，选中模型
  失败仍自动 failover 到同能力下一个）。**顺带发现文本生成链路上
  `org_id` 从未被传到 Gateway 这个既有 bug**——配了 BYOK Key 按折扣价
  计费的租户，实际调用一直在走平台的 Key，平台在倒贴钱；这次一并修了，
  是一次可感知的计费路径变化，不是纯内部重构。模型页面在
  `/freeflow/models`，视频/语音诚实标注"M2 待办"，没有假下拉框。

**M2 待办**（"出片"）：TTS → 音频优先时间线 → ffmpeg 合成，目标产出第一条完整成片。
详见 `12_MVP_Roadmap.md`。

**一件悬着的事**：真实废片率仍未测出。验证切片跑出的 3.33 无效
（它把构图差异计成了废片）。`pricing_rules.image_retry_factor = 250` 还是拍值，
**不可用于对外报价**。需要人工逐张判可用性，10 张图在 `validation/`。

---

## 不可违反的规则

违反了 CI 会拦下，或者会造成很贵的后果。**偏离必须先提 ADR。**

| 规则 | 后果 |
|---|---|
| 价格、汇率、废片率不写成代码常量，走 `model_pricing` / `pricing_rules` | 上游一调价就要改代码发版。DeepSeek 2026-08-17 涨了 350% |
| 执行状态只认 `tasks.status`，不在别处并行维护 | 多份真相必然互相矛盾 |
| 金额一律 `BIGINT` 最小单位，禁止浮点 | 浮点存钱是财务事故经典来源 |
| 改余额必先 `SELECT FOR UPDATE`，每笔带幂等键 | 并发丢更新 / 重复扣费 |
| 每个用户侧查询都要带 `org_id` | 跨租户数据泄露 |
| 跨租户访问返 404 不返 403 | 403 会确认资源存在，可枚举 |
| 跨模块只调对方 `service` 层 | ruff banned-api 会拦 |
| 包 `__init__.py` 不导入 router | 会撞循环导入 |
| 密钥不进代码、不进日志、不进 `input_json` | — |
| Agent 必须把系统给的风格词**原样照抄**进提示词，不得改写、精简或替换（ADR-036 起；此前是「不许写」） | 画风漂移头号来源 |
| 第三方 Agent / Skill 只能是 YAML，不能是代码 | 等于把服务器交出去 |
| Skill 的 `handler` 只能取白名单里的，`export` 路径按段白名单校验 | 前者是任意能力，后者会往用户磁盘任意位置写 |
| 阈值、废片率不写进会被分发的 YAML，只写键名 | 待定的数字冻进发布物，改数要发版 |
| 新 Agent 必须有 eval 用例才能上线 | 提示词退化不会让测试变红 |

---

## 踩过的坑

都是这个项目里真实踩过的，重复踩会浪费很多时间。

**环境与工具链**

- `os.environ.setdefault()` 在 compose 已注入该变量时**不生效**。踩过两次：
  一次让测试连错 MinIO 地址，一次让**整个测试套件在打真实上游花钱**。
  测试里改环境变量一律用赋值，且之后要 `get_settings.cache_clear()`。
- compose 给每个带 `build:` 的服务建**独立镜像**。api 和 worker 共用
  `image: aigc-studio-backend`，否则改依赖只重建一个，出现"api 好好的 worker 崩了"。
- Docker Desktop 内置 DNS 对 AAAA 查询不稳定，`getaddrinfo` 整体失败
  （能 ping 通但 httpx 连不上）。compose 里显式指定了 DNS。
- worker 会继承 Dockerfile 的 HTTP 健康检查但它不跑 HTTP 服务，
  已 `healthcheck: disable: true`。

**Python / 框架**

- pydantic-settings 在**读环境变量的源层**就对 list 字段做 JSON 解析，
  `mode="before"` 校验器轮不到执行。要加 `NoDecode`。
- `computed_field` 默认进 `repr()`，数据库连接串里是明文密码。要 `repr=False`。
- 自定义校验器抛 `ValueError` 时，pydantic 把异常对象放进 `errors()` 的 `ctx`，
  直接序列化会让 422 变 500。
- redis-py 的 `Redis` / `ConnectionPool` **不是泛型**（已废弃的 types-redis 才是），
  写 `Redis[str]` 能过 mypy 但导入就炸。
- `rollback()` 后读 ORM 属性会触发同步 refresh，在 async 上下文抛
  `MissingGreenlet`，把真正的业务错误掩盖成 500。要在 try 前取出需要的值。
- Alembic 不为独立 `Sequence` 生成 `CREATE SEQUENCE`，也不为 pgvector 类型
  生成 import。前者手工补，后者已加 `render_item` 钩子。
- `lambda` 捕获变量、`partial` 捕获值。工厂函数里共用变量名 + lambda =
  **DeepSeek 拿着万相的 Key 去请求**。
- arq 的 `WorkerCoroutine` 协议按参数名匹配，第一个参数必须叫 `ctx`。

**上游 Provider**

- DeepSeek `response_format: json_object` **要求提示词里出现 "json"**，
  否则 400。已由 runner 统一注入，第三方 Agent 作者不需要知道。
- `deepseek-v4-*` 是推理模型，思考 token 计入输出预算。预算给小了会返回
  **空内容且不报错**。同样的结构化任务 `deepseek-chat` 只用 11 个 token，
  V4 要 60 个——Router 分类这类活儿不需要推理。
- DashScope 默认**重写提示词**（`actual_prompt`），会覆盖系统级风格锁定。
  已关 `prompt_extend`。
- DashScope 图片链接**只有 24 小时有效期**，必须转存自有对象存储。

**业务逻辑**

- 预扣和结算的 `attempt` 编号必须一致。`begin_execution` 会自增 attempt，
  差一位会导致**任务成功了但钱没扣**，且不报任何错。
- 中文提示词清洗要**同时处理全角标点**，只清半角等于没清。

**测试**

- Worker 里的常驻中继会与测试并发跑，断言要写成"最终一致"而不是
  "本次调用搬了几条"。
- 幂等键全局唯一，测试里不能写死，否则跨轮次残留会 409。
- SSE 长连接不要用 ASGITransport 测——流式行为与真实 HTTP 有差异，
  只会得到关于测试工具的结论。拆成部件测 + 真实 HTTP 手工验证。

---

## 代码地图

```
apps/api/core/          配置、错误目录、DB、Redis、日志脱敏、ORM 基座
apps/api/modules/       auth / project / asset / task / realtime /
                        billing / agent / gateway / consistency
                        —— 每个模块只通过 service.py 对外
agents/                 Agent spec（YAML）+ registry + 输出 schema
  builtin/              平台内置，随代码发布
  custom/               第三方，丢 YAML 进去即生效，热加载
skills/                 Skill spec（YAML）+ registry —— 生产模板层
  builtin/              novel_to_anime.yaml 是主线默认 Skill
  custom/               第三方，同上
adapters/providers/     DeepSeek / DashScope
worker/                 Arq Worker + jobs
scripts/                validation_slice.py（会花真钱）
migrations/             Alembic
design-system/          前端设计系统 MASTER.md + 对比度校验脚本
validation/             10 镜验证切片的图与报告
```

`.claude/skills/ui-ux-pro-max/` 是装进来的第三方设计技能（MIT），
改前端时可用它检索配色/字体/UX 规范。

---

## 改动纪律

1. 文档是唯一真相。偏离文档的实现**先提 ADR** 再改代码。
2. 每个功能按 `Domain model → API contract → Service → Worker → Adapter → UI → Tests → Docs` 走。
3. 提交前跑全量门禁：`ruff check` + `ruff format --check` + `mypy` + `pytest`。
4. 外部 Provider 一律先有 Mock，且 `ENV=test` 时强制走 Mock。
