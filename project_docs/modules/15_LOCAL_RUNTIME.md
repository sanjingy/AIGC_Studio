# 15 本机运行时（试点）

> 状态：**试点，默认关闭，未部署，未接受对外开放。** ADR 待定。
> 本文档描述的是**当前工作区里真实存在的实现**（`feat/freeflow-prototype`，未合并）。
> 上一版（只有文本、走 `codex exec` 一次性子进程）已被替换，见 §9 变更记录。

## 1. 这是什么

让用户在网页上把**这一次生成**交给他自己电脑上已经登录的官方 CLI 去跑：

| 能力 | 走谁 | 产物落在哪 |
|---|---|---|
| 文本（Agent 的一步） | 本机 Claude 或 Codex | 与原来一样，`agent_runs` |
| **图片** | 本机 **Codex 原生 `image_gen`** | **项目资产库**（与平台出图同一张 `tasks`、同一条 `register_generated`） |
| 视频 / 语音 | —— | **不支持**，见 §2 |

客户端要装的是**官方 Codex CLI 并登录**，不需要装 Orca，也看不到 Orca 的任何界面。

三个方向上的边界，一个都没松：

* 服务端**不监听**桌面。是桌面上的连接器主动**出站**连服务端。
* 服务端**不持有、不复制**任何 Provider 登录态。用户的 `auth.json` 一直在他自己机器上。
* 桥接令牌只开三条路径（poll / heartbeat / result），换不出任何项目接口的访问权。

## 2. 边界：不做什么

* **不开放视频、语音。** 两条各有各的依据，不要混成一句：
  * **Codex / Claude**：本轮逐条核过 `--help`，两者都没有原生的视频或语音
    生成入口。这条是**验证过的**。
  * **Gemini**：**本轮未验证、未接入**——本机没有安装 gemini，一次实测都
    没有做过，所以这里**不对它有没有、是什么形态、怎么计费下任何结论**。
  因此 `capabilities.video/audio` 恒为 `false`，界面不显示入口。
  这是**本轮的范围决定**（没验过的不开放），不是对生态的判断；
  将来要开放先做验证，见 §8-7。
* **不做 failover。** 选中本机就只有本机：失败以失败告终，不会悄悄改调付费 API
  （那是拿用户的钱补一次他以为在用订阅额度的调用），也不会退回 Mock。
* **不自动重投。** 连接器崩了就让它超时，用户显式重试。自动重投 = 同一次生成
  在他的订阅额度上扣两遍。
* **不改资金规则。** 见 §6。
* **不进 prod。** 配置层直接拒绝启动；staging 要开还得再显式点一次头。

## 3. 契约

### 3.1 配置（`apps/api/core/config.py`）

| 变量 | 含义 |
|---|---|
| `LOCAL_CLI_ENABLED` | 总开关，默认 `false` |
| `LOCAL_CLI_ORG_ID` | 只对这一个 org 生效 |
| `LOCAL_CLI_PROJECT_IDS` | 显式项目白名单，逗号分隔，不许留空、不许写 `*` |
| `LOCAL_CLI_PROVIDER` | **文本**走哪个 CLI：`codex` / `claude`，默认 `claude` |
| `LOCAL_CLI_IMAGE_PROVIDER` | **图片**走哪个：只有 `codex` |
| `LOCAL_CLI_TOKEN` | 桥接令牌，≥ 32 个随机字符 |
| `LOCAL_CLI_TIMEOUT_SECONDS` | 文本上限，30~900，默认 180 |
| `LOCAL_CLI_IMAGE_TIMEOUT_SECONDS` | 出图上限，60~1800，默认 420 |
| `WORKER_JOB_TIMEOUT_SECONDS` | Arq 单 job 上限，默认 900 |
| `LOCAL_CLI_ALLOW_STAGING` | staging 的第二道头 |

**开着但配不全 = 拒绝启动。** 半配置比不配置危险：运行时看起来"没启用"，
但桥接端点已经挂着一个空令牌在那里等人来试。

**模型 id 不在服务端配**：它是连接器启动时的 `--model`，本机那个人才知道
自己的订阅里有哪几个模型。

### 3.2 跨层超时（**这条错了不会报错**）

出图是在**一个 Arq job 里等结果**的。所以：

```
LOCAL_CLI_IMAGE_TIMEOUT_SECONDS + 收尾预留(120s) ≤ WORKER_JOB_TIMEOUT_SECONDS
```

配置校验会拒绝违反它的组合（默认 420 + 120 = 540 ≤ 900 ✓）。超了会发生一件
没有任何报错的事：Arq 先把 job 杀掉，用户看到"失败"，而他自己那台电脑还在画，
订阅额度照烧、图画出来也没人接。

预算是**共享**的，不是每一步各给一份：

```
服务端入队 → 定下绝对截止时间 deadline_ms
出队      → 只发「剩余」秒数（在队列里躺过的时间已经扣掉）
连接器     → 握手 → thread/start → turn/start → 等产物，四步共用这一份剩余预算
```

### 3.3 四个端点

```
POST /api/v1/local-runtime/poll                     桥接令牌，取一条请求
POST /api/v1/local-runtime/heartbeat                桥接令牌，忙时续心跳 + 问"还要不要"
POST /api/v1/local-runtime/requests/{id}/result     桥接令牌，回一条结果
GET  /api/v1/local-runtime/status                   正常登录态，界面用
```

* `poll` 的入参带 `kinds`：连接器自报**真实**支持的能力（可执行文件解析到、
  且 Codex 自己在 `features list` 里报了 `image_generation true`）。
  服务端据此派活，也据此在界面上显示"本机能不能出图"——写死在服务端就是假入口。
* `heartbeat` 单独一个端点而不是 poll 上的一个 `busy` 标志：它的返回值是
  "你手上那条还有人等吗"。**回 false 时连接器必须立刻停手**——用户已经取消，
  或等待方已经超时，继续跑只是白烧他的订阅额度。
* `result` 三选一：`text` / `image_base64` / `error_code`。错误码是**有界枚举**，
  不接受自由文本，更不接受把子进程 stderr 原样回传（里面有本机路径、用户名，
  极端情况下还有登录提示里的账号）。
* `status` 只说本 org 的事，且逐个核对项目归属——配置里写错成别人的项目 id
  也不回显。不可用时带一句**中文原因**，界面原样显示给用户。

### 3.4 图片必须真的是图片

回传的字节要过四道，一道比一道贵：

1. 长度（解码前就挡）；
2. 严格 base64（宽松解码能把一段文字也解出"字节"）；
3. **字节魔数**（不信对端给的 `image_mime`，也不信扩展名）；
4. **Pillow 真解一遍**：先读 `size` 判尺寸上限（宽×高就是即将分配的内存，
   这一步必须在 `load()` **之前**，否则解压炸弹已经开始解了）→ `verify()`
   → 重开一次 `load()` 真把像素解出来 → 解出来的格式要和魔数一致。

第 4 道不是冗余：魔数只有八个字节，"PNG 头 + 一坨随机数据"能轻松骗过前三道，
然后那坨东西会被写进对象存储，在用户浏览器里显示成一个碎图标——而任务是"成功"的。

`savedPath`（Codex 说的落盘路径）**不被信任**：官方 schema 自己写了它
"not guaranteed to … exist on the filesystem"。我们只用 `result` 里的字节，
路径只做包含性校验后留作参考。

### 3.5 Redis 邮箱

它是**临时关联**，不是第二份执行状态（执行状态只认 `tasks.status`，ADR-008）。
入队 / 出队 / 完成 / 清理**各自是一个 Lua 脚本**，因为每一处的竞态后果都落在
"钱已经花掉了"那一侧：

| 动作 | 为什么必须原子 |
|---|---|
| 入队 | 深度检查与 RPUSH 分开 = 上限形同虚设 |
| 出队 | LPOP 与占租约分开 = 同一条请求被两台连接器拿走，额度扣两遍 |
| 完成 | 比对租约、删键、投递结果分开 = 结果写进没人等的邮箱 / 被回两次 |
| 清理 | LREM 与 DEL 分开 = 队列里留下指向已删载荷的 id |

队列按 `(provider, kind)` 分开，所以只跑文本的那台连接器不会捞走一条出图请求。

## 4. 出图这条闭环

```
网页选「本机」
  → POST /projects/{id}/images/{characters|scenes|shots}/... {"source":"local"}
  → 建任务之前 preflight（没开 / 没连上 / 那台不支持出图 → 409，不建任务不预扣）
  → 建 image.generate 任务，来源钉进 input_json.image_source
  → Worker 读来源：local 就只走本机（Gateway 一次都不会被调到）
  → 服务端把 compose 合成好的提示词入队
  → 桌面连接器取走 → codex app-server → 一轮对话 → item/completed(imageGeneration)
  → 图片字节 base64 回传 → 服务端四道校验
  → Worker 交给 asset_service.register_generated（**同一个**入口、同一套配额）
  → 出现在项目资产库 / 出图记录里，标着 image_source=local
```

**来源钉在任务上**而不是"执行时看谁可用"：漂移的后果不对称——说好用订阅额度的
一次重试，会悄悄变成一次付费调用。重试同一个任务，来源不变。

**提示词由服务端 `consistency.compose` 合成**，风格锁定在那一层。连接器只在外面
包一句"请调用你的内置图像生成工具"（协议层没有"生成一张图"的方法，出图是模型
自己决定调 `image_gen` 的产物，只能在提示词层请求），并明确要求**不要改写**
画面描述。模型改写后的提示词（`revisedPrompt`）如实回传，落进资产 metadata 的
`actual_prompt`——与万相那条链路同一个字段名。

**取消**：Worker 每隔几秒读一次 `tasks.status`；用户取消后清掉租约，连接器的
下一次心跳收到 `outstanding=false` 就打断那一轮（`turn/interrupt` 带
`{threadId, turnId}` 两个 id，缺一个对端会拒绝——上一版就缺 `turnId`，
于是"取消"只取消了界面）。

## 5. 桌面这一侧

### 5.1 装什么

官方 **Codex CLI**（出图）和/或 **Claude Code**（文本），并**已登录**。
不需要 Orca。连接器本身只用 Python 标准库，不需要装后端依赖。

### 5.2 跑起来

```powershell
# 1) 隧道（已有的那条就行）
ssh -N -L 3100:127.0.0.1:3000 root@<server>

# 2) 令牌只从环境变量读，不要写进命令行
$env:AIGC_LOCAL_RUNNER_TOKEN = "<与服务端 LOCAL_CLI_TOKEN 相同>"

# 3) 先体检（不连服务端、不花额度）
python -m apps.local_runner --doctor

# 4) 跑起来。出图那台用 codex；文本那台用 claude，两个进程互不干扰
python -m apps.local_runner --server http://127.0.0.1:3100 --provider codex
```

`--doctor` 会打印每个 CLI 的版本与**真探出来的**能力：

```
codex      : 版本 = codex-cli 0.153.4
             能力 = text、image
claude     : 版本 = 2.1.263 (Claude Code)
             能力 = text
```

### 5.3 执行面：**一个 provider 一条路径**

| provider | 执行面 | 理由 |
|---|---|---|
| codex | `codex app-server --listen stdio://`（官方 JSON-RPC） | 官方协议面，有打断、有原生生图产物、有账号/额度事件 |
| claude | `claude -p --output-format json`（官方 headless） | 它没有 app-server 这样的协议面 |

`codex exec` 那条一次性子进程路径**已删除**。同一个 CLI 没有理由维护两套
"怎么起进程、怎么判失败、怎么杀干净"。

**只开 stdio**，不开 `--listen ws://`：那会把一个能操作本机的 RPC 端口暴露出去，
而我们是父子进程关系。线程一律 `ephemeral: true`（不进用户的会话历史）、
`sandbox: read-only`、`approvalPolicy: never`（非交互下没人能回答审批请求，
不写死它一次审批就会把这一轮挂到超时）。

### 5.4 传输与凭据

* 传输必须 HTTPS，**唯一例外是回环 HTTP**（现成的 SSH 隧道）。
* **拒绝任何重定向**：urllib 默认会跟，并且会把 `Authorization` 头带过去。
* 子进程环境按前缀剔除 `ANTHROPIC_ / OPENAI_ / AZURE_OPENAI_ / AWS_ / GOOGLE_ /
  GEMINI_ / VERTEX_ / CLAUDE_CODE_USE_ / AIGC_LOCAL_RUNNER_`，
  **保留** `CODEX_HOME` / `CLAUDE_CONFIG_DIR` / `CLAUDE_CODE_OAUTH_TOKEN`
  （订阅登录的落点）。目的只有一个：不让这次调用悄悄变成按量付费。

## 6. 计费口径（**必须读**）

本机出图**照常走 `image.generate` 任务，照常预扣、照常结算**，与 API 出图同价。
这不是漏改，是刻意的：**"用户自己的订阅额度折算多少平台 Credits"这件事今天
没有定义**，而在没有定义之前把它当成免费，等于凭空改了资金语义。

* 失败按错误目录 `RELEASE` 退回预扣（平台侧确实没有产生上游成本）。
* 成功按预扣结算，与平台出图一样。
* **耗掉的是用户自己的订阅额度**，那笔账不在平台 Credits 体系里。
* → **对外开放之前必须先定折算规则。** 在那之前这条路径只对白名单项目开着。

## 7. 测试

```powershell
# 桌面侧：无需 Docker、无需后端依赖、不花任何额度
python -m unittest discover -s apps/local_runner/tests -t .

# 后端：服务器容器内
pytest tests/unit/test_local_runtime_config.py tests/unit/test_local_runtime_selector.py
pytest tests/integration/test_local_runtime_transport.py
pytest tests/integration/test_local_runtime_api.py
pytest tests/integration/test_local_image_source.py
```

`test_local_image_source.py` 盯的是这条路径上真正会出事的那几件事：来源钉死且
重试不漂移、失败绝不回落 Gateway、不可用时不建任务不预扣、失败释放预扣、
**成功按原价结算**（资金口径的锚，界面文案必须与它一致）、跨租户进不来、
入库必须是真图片（含"头对内容坏"和解压炸弹）、图片确实进了项目资产库，
以及返工新增的三条：**离线时重试被拒且账本不动**、**`n != 1` 在建任务前被拒**、
`usage_limit` 文案不许回退成"这条路径不消耗平台 Credits"。

`test_local_runtime_api.py::TestCancelLifecycle` 补的是取消链路的两头
（此前只有中间两截有测试）：分段等待真的会在段间问一次取消、正常路径不会被
这道闸变成"一问就退"、文本那条也走分段、Worker 的 `_cancel_watcher` 读的是
`tasks.status`、重复取消不会重复动账本。

`apps/local_runner/tests/test_lifecycle.py` 是连接器侧的对应物（`executor.py` +
`cli.py` 此前几乎零覆盖）：取消一次之后下一条仍能跑（P0-1 的红线）、Ctrl-C
就地回报一次 `cancelled`、回传超时的三条边界、`account/read` 的账号判定、
provider 被换成按量付费端点时拒绝、逐线程配置覆盖的形状。**它一条真实 CLI 都
不起，跑它不花任何额度。**

**取消那一组走的是生产入口**（2026-09-09 返工）。上一版是一个继承
`CodexExecutor`、**重写了 `run_text` 并自己调 `_begin` / `_end`** 的子类，
于是"生产的 `run_text` / `run_image` 到底有没有正确开合取消标志"根本没被覆盖。
现在换成：构造期那四次要起进程的探测（`resolve` / `--version` /
`account/read` / `features list`）用 `mock.patch` 顶掉，**只**顶掉底层的
`_turn_text` / `_turn_image`（那是唯一非起真进程不可的一段），
`run_text` / `run_image` / `_begin` / `_end` / `cancel` / `_cancel_check`
全部是生产代码本身。用例形状是"第一条**确实在途**（假 turn 已开跑并在轮询
`should_cancel`）时 `cancel()` → 断言 `cancelled` → 同一个 executor 第二条
→ 断言成功"，**文本和出图两条入口各一组**，外加"文本取消不污染随后的出图"。

这组用例**能判红**已用变异实验证过：把生产 `run_text` / `run_image` 里的
`_begin` / `_end` 全删掉之后，旧那版 3 条用例**照样全绿**（虚绿），
新这版 6 条里 3 条判红并直接指出「没有调 `_begin`」/「`finally` 没有调
`_end`」。原始输出存档在 `orca/tasks/evidence_resume3/mutation_probe.log`，
`executor.py` 变异前后的 sha256 都记在里面，实验后已按 sha256 复核还原。

本轮实跑结果（2026-09-07 起，取消那一组的数字为 2026-09-09 重跑）：

| 门禁 | 结果 |
|---|---|
| 桌面侧 `python -m unittest discover -s apps/local_runner/tests -t .` | `Ran 115 tests … OK`（2026-09-09；此前为 112，取消那一组由 3 条改写成 6 条） |
| 隔离快照 `ruff check .` | `All checks passed!` |
| 隔离快照 `ruff format --check .` | `217 files already formatted` |
| 隔离快照 `mypy apps worker packages agents adapters skills` | `Success: no issues found in 141 source files` |
| 隔离快照 本机运行时相关五个文件 | `105 passed`（72 旧 + 33 新） |
| 隔离快照 **后端全量** `pytest -q` | **1036 项收集 / 1034 passed / 2 skipped**，退出码 0，420s |

全量那一行的数字**是从进度行数出来的，不是抄的 `-q` 汇总行**——
`snap_check.py` 捕获到的 stdout 里没有那一行汇总（两次全量跑的记录都没有）。
进度行是 14 行满行（每行 72 个）+ 末行 28 个 = **1036 collected**，
其中第 13 行有两个 `s`，其余全是 `.` → **1034 passed + 2 skipped，0 失败**；
百分比逐行与 1036 这个基数完全吻合（72→6%、144→13%、…、1008→97%、1036→100%）。
当天**跑了两次全量**（后台 436s、前台 420s），两次退出码都是 0；
后台那次的原始输出留在文件里，可复核。
**没有为了确认这个数字重跑测试。** 日志路径与逐条原始输出索引见
`orca/tasks/LOCAL_RUNTIME_FINAL_DOCS.report.md`。

2026-09-09 只改了 `apps/local_runner/tests/test_lifecycle.py` 一个文件
（**生产代码零改动**，`executor.py` sha256 与改动前逐字节一致），
因此只重跑了与它相关的那几道：桌面侧全量 `Ran 115 tests … OK`、
`ruff check` / `ruff format --check`（`apps/local_runner`，19 files）、
隔离快照 `mypy apps worker packages agents adapters skills`
→ `Success: no issues found in 141 source files`（退出码 0，40s）。
**后端全量没有重跑**（本轮没有任何后端改动，重跑它只是烧时间）。
原始输出存档 `orca/tasks/evidence_resume3/gate_after_fix.log`。

## 8. 已知缺口

1. **真机验证到哪一步为止：连接器那一段 + 入库那一段各自跑通，网页真实
   E2E 仍未做。** 三段分开说，别合成一句"跑通了"。

   **（a）连接器侧出图 —— 已验证。**
   `orca/tasks/codex_smoke_result.txt`（2026-09-07 22:23:08 → 22:24:13）记录了
   一次**成功**出图：`image/png`，**2,471,345 字节**，**1254×1254**，
   **用时 62 秒**，落盘 `orca/tasks/codex_smoke_image.png`
   （魔数与 IHDR 已复核，sha256 `e6b2a196…`，是真图不是占位物）。
   它验证的范围到此为止：`resolve → app-server 握手 → thread/start →
   turn/start → item/completed → decode_image_result → 魔数嗅探`。

   **（b）同一张图走现有入库链路 —— 已验证（隔离快照，`3 passed`）。**
   证据在 `orca/tasks/LOCAL_RUNTIME_LIVE.report.md §4`。**没有再生成第二张**，
   用的就是 (a) 那份字节：

   ```
   python orca/tasks/snap_check.py \
     --extra orca/tasks/live_ingest_test.py:tests/integration/test_live_ingest.py \
     --extra orca/tasks/codex_smoke_image.png:tests/integration/_live_image.png \
     "pytest -q tests/integration/test_live_ingest.py -v"

   [snap] 在容器内 /tmp/snap_20260907-222715 执行：pytest -q …
   3 passed in 7.51s
   [snap] 退出码 0，耗时 13s
   [snap] 临时快照已清理（部署检出未被触碰）
   ```

   三条断言：字节 sha256 与本机核对值一致且 Pillow 能 open+load；
   走服务端真正那道 `lr_service.decode_image`（严格 base64 → 魔数 →
   Pillow 真解码 → 格式一致）；**完整产品路径**
   `POST /projects/{id}/images/characters/{ref} {"source":"local"}` → 201 →
   `execute_task` → `succeeded` → 资产行 `image/png` / `2471345` 字节 /
   `metadata.image_source=local` → `storage.head_object` 相符 →
   **预签名 URL 真 GET 回来 sha256 仍是 `e6b2a196…`** →
   `GET /projects/{id}/images` 认得它 → `GET /assets/{id}/download-url` 200。
   唯一的替身是 `complete_image`（把已有字节还回去），
   `gateway.generate_image` 换成会抛断言的地雷、全程没被调到。

   **（c）浏览器里的前端行为 —— 已验证，但后端是假的。**
   2026-09-08 在本机真 Chrome 上跑完 8 条独立用例、**31 条断言全过、0 失败**
   （`orca/tasks/LOCAL_RUNTIME_UI_FINAL.report.md`，脚本 `orca/tasks/ui_final.js`，
   每条用例各自落 `orca/tasks/ui_shots/<case>.{png,json}`）。Lead 已逐条核过
   `shot-local` / `shot-batch-local` / `switch-back-api` 三条抓到的**原始请求体**
   与批量弹窗截图，上一轮那个「分镜没有来源选择器、单镜出图静默走付费 API」
   的缺口**确认已关闭**（`radiogroup` 由 0 变 1，单镜与批量的 payload 都是
   `{"source":"local"}`）。覆盖到的：角色 / 场景 / 分镜单镜 / 分镜批量选本机、
   切回平台发 `{"source":"api"}`、未连接时「本机」禁用且给中文原因、
   不在白名单时整个选择器不出现、任务中心取消真的调 `/tasks/{id}/cancel`。

   **它的边界要一起记住**：前端跑的是工作区真实代码、断言依据是真实抓到的
   网络请求体，但**后端是 `orca/tasks/mock_api.py` 这个假后端**，
   连接器、Codex、入库全都不在链路上。所以它证明的是"**界面把用户的选择
   如实变成了正确的请求**"，**不是**"这条链路能出图"。
   未覆盖：SSE 实时进度（mock 只保持连接不推事件）、重试按钮的来源保持
   （后端有 pytest 覆盖，浏览器层未验）。

   **（d）网页真实 E2E —— 仍未做。**
   (b) 覆盖的是"HTTP 端点 → 任务 → Worker → 入库 → 取回"，但它跑在**隔离
   快照**里、**连接器那一跳被替身顶替**；(c) 覆盖的是浏览器到请求体那一段，
   **后端整个是假的**。两者都与真人在浏览器里点一次不是一回事。
   仍未做的是：真后端 + 真 Redis 队列 + 本机真连接器 → 真 Codex 出图 →
   回传入库 → 在浏览器里看到那张图、`tasks` 那行是 succeeded、
   Ledger 有一条 CONSUME、下载链接能取回同一份字节。
   **不要拿 (a)、(b) 或 (c) 中的任何一条代替它。**
   隔离环境下把它跑通的可执行方案（不改生产 `.env`、不部署、独立端口 +
   独立测试 org/project + 独立 Redis 队列前缀）见
   `orca/tasks/LOCAL_RUNTIME_RESUME3.report.md §3`，**尚未执行**。

   同一份冒烟记录还给出两条事实：
   * **请求 1024×1024，实际拿到 1254×1254。** `size` 对 Codex 只是提示词里的
     一句话（`executor.py::_IMAGE_INSTRUCTION`），协议层**没有**尺寸参数，
     服务端也不校验回来的尺寸与请求是否一致（只查最小边长和像素上限）。
     **`size` 是建议不是约束**；资产 metadata 里记的是真实产物的尺寸。
   * **`revisedPrompt` 与发出去的提示词确实不同**——改写是常态，见第 3 条。

2. **"能不能出图"来自特性开关，不是来自一次成功。**
   `_probe_image_feature` 读的是 `codex features list` 里的
   `image_generation ... true`，这个布尔一路点亮界面上那颗「本机」按钮。
   特性开关为 true **不等于**这个账号此刻能出图（额度、订阅档位、服务端灰度
   都可能挡）。所以界面文案只说"可以选"，并明写"额度不足时这一张会失败"，
   不暗示"一定能出图"。先试跑一次来确认要花额度，不值得。

3. **`revisedPrompt` 只存档、不校验风格词。** ADR-036 要求"原样照抄"的校验
   语义本身还没落地（排在四道门前端之后），这条链路会一起受益，不单独做一套。
   本机这条比万相那条**更容易漂**：改写是模型自己做的常态行为。

4. **`max_output_tokens` 在桌面侧没有落点**：两个 CLI 的 help 里都没有对应开关。
   服务端照常下发，连接器目前不用它。

5. **token 用量不编造**：CLI 不回报就是 `None` → 服务端记 0 并标注"未回报"。
   **不要拿这个 0 反推成本。**

6. **超时与"结果刚好回来"存在固有竞态**：BLPOP 超时的同一瞬间结果到达时，
   用户看到超时而桌面其实成功了。这是"无重投"的代价，明确接受。

7. **Gemini：本轮未验证、未接入。** 本机没有安装 gemini，一次实测都没有做过。
   因此本文档、`config.py`、连接器 `--doctor` 三处**都只说到这里为止**：
   没验过，所以不接入、界面不留入口（避免假入口）。

   早先这几处还写着"Imagen / Veo 是 MCP 外接付费 API、不是会员权益"，
   依据是官方文档而不是实测。那句话已经**删掉**——它替整个生态下了一个
   我们没有验证过的结论，而这条链路上"某个能力是不是订阅权益"直接关系到
   花谁的钱，不该由一句转述来定。要开放 Gemini（或任何第三方 CLI）之前，
   先按 Codex / Claude 那套做一遍实测：账号形态、是否走订阅、能力是不是
   真的能跑出产物。

8. **逐线程配置隔离：两项已实测有效，两项只到"官方接受"为止。**
   （2026-09-07 本机 codex-cli 0.153.4，全程只用官方查询方法 + 一次不发 turn 的
   `thread/start`，**没有消耗任何额度**。）
   * **已实测有效**：`modelProvider` 钉死（`ThreadStartResponse` 官方回显
     `modelProvider: openai`）；`mcp_servers` **按名**禁用
     （带 `threadId` 的 `mcpServerStatus/list` 回来 `node_repl` → `disabled`）。
   * **已实测无效、已改掉**：`config: {"mcp_servers": {}}` 是**合并**语义，
     两台服务器仍然 `connected`。上一版写死的那份空对象覆盖是无效的。
   * **关不掉、且仍在跑**：`codex_apps`。它不在 `config.mcp_servers` 里，
     `plugins.<名字>.enabled = false` 也不影响它，逐线程覆盖之后它仍是
     `connected` / `starting`。它由 Codex 官方分发（`serverInfo.name =
     plugin-runtime`），不是用户配的第三方服务器——**这解释了它为什么
     不在我们能按名禁用的那张表里，但不等于它没有对外能力**：它带的工具
     是什么、会不会联网、能碰哪些本地资源，本轮**没有逐项核查过**。
     现状如实记：这一跳的外部能力面**未被我们收窄，也未被我们评估**。
   * **只到"官方配置加载器接受了这份覆盖"**：`notify: []` 与 `hooks.<事件>: []`。
     它们**没有线程级的官方回读口**（`hooks/list` 只按 `cwds` 查，不认 `threadId`；
     notify 要跑完一轮才触发）。我另外试过用通知流反证：`thread/start` 之后
     4 秒内，基线与关掉 hooks 两种情况收到的通知完全相同
     （只有 `mcpServer/startupStatus/updated` 和 `thread/started`，没有任何
     hook 通知），说明 SessionStart 不在 `thread/start` 触发，这条路证不出来。
     要证明"确实没被触发"得真跑一轮 = 花额度，本轮没做。
     **所以这两项的状态是"未验证"，不是"已生效"。**

9. 文本任务在 `tasks` 上的统一仍是既有欠账，本轮没有重构。

## 9. 变更记录

* 2026-09-09（**只改一个测试文件，生产代码零改动**）：连接器取消回归改走
  **生产入口**（`run_text` / `run_image`），补上出图那条入口与"文本取消不
  污染随后出图"，并用变异实验证明这组用例真的能判红（旧那版在同一变异下
  全绿）——§7 有做法与证据路径。桌面侧 112 → **115 passed**。
  §8-1 把浏览器验收单独列成 (c)：**UI_FINAL 8 用例 / 31 断言全过，
  Lead 已核过单镜、批量、切回平台三条的原始请求体与截图**，同时写明
  它跑在假后端上、**不能当成真实 E2E**；真实 E2E 顺延为 (d)，仍未做。
  状态不变：**试点、默认关闭、未部署、未对外开放。**
* 2026-09-08（收尾，**只动文档与注释，无业务改动**）：Gemini 的措辞收敛到
  「本轮未验证、未接入」，删掉"Imagen / Veo 是 MCP 外接付费 API"这类替生态
  下结论的转述（§2、§8-7、`config.py`）；§8-1 补上 `LOCAL_RUNTIME_LIVE.report.md`
  的「同一张真实图片走现有入库链路，隔离快照 `3 passed`」证据，并把三段
  （连接器出图 / 入库 / 网页真实 E2E）拆开写，**E2E 明确仍未做**；
  §8-8 把 `codex_apps` 与 `notify`/`hooks` 的状态改成如实的"未收窄、未评估"
  与"未验证"；§7 补上精确的门禁数字（全量 **1034 passed + 2 skipped /
  1036 collected**，数字来源在正文里说明，未重跑）。
  状态不变：**试点、默认关闭、未部署、未对外开放。**
* 2026-09-07（第三版，返工）：按 `orca/tasks/LOCAL_RUNTIME_REVIEW.report.md`
  返工。连接器取消状态改成**每请求一份**（取消一次不再让整个进程作废）；
  Ctrl-C 就地回报一次 `cancelled`；结果回传超时按体积放大并受剩余预算封顶；
  逐线程配置隔离改成**按名禁用**（空对象是合并语义，实测无效）并加上
  `account/read` + `config/read` 两道免费官方校验；出图来源 preflight 从三个
  端点搬进 `task_service`（重试与 `POST /tasks` 也覆盖，不可用时不预扣）；
  本机 `n != 1` 在建任务前拒绝；文本等待改分段（不再一次 BLPOP 占住共享连接）；
  **计费文案改口径**（本机是"额外消耗你的订阅额度"，平台 Credits 仍按同价计费，
  不是免费）；分镜页面补上来源选择（此前分镜出图选不到本机，静默走付费 API）。
* 2026-09-07（第二版）：加出图闭环（Codex 原生 `image_gen` → 项目资产库）、
  网页来源选择、四个端点、Lua 原子邮箱、绝对截止时间、跨层超时校验、
  Pillow 真解码校验、取消信号；删掉 `codex exec` 那套重复执行路径；
  文本默认 provider 改 `claude`。
* 2026-09-07（第一版）：只有文本，`codex exec` / `claude -p` 一次性子进程，
  三个端点，非原子邮箱。**该版本的执行层与传输层已被本轮替换。**
