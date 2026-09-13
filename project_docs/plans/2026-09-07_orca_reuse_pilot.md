# 改用现成 Orca 跑本地生成：修订后的交付计划（2026-09-07）

> **本文取代同日的 [2026-09-07_delivery_and_local_runtime.md](2026-09-07_delivery_and_local_runtime.md)
> 的排序与架构判断**（那份把本地运行时排在最后一波，与负责人现在的显式优先级相反；
> 它描述的自建 CLI 运行时架构已被本文的方案 B 取代）。
>
> 调研证据、逐条命令契约、以及被取代那版的待办清单在
> [`orca/tasks/LOCAL_RUNTIME_ORCA.report.md`](../../orca/tasks/LOCAL_RUNTIME_ORCA.report.md)
> （`orca/` 已 gitignore，不入库）。
>
> **这是计划，不是实施记录。截至本文，本方向零代码。**

---

## 1. 方向变更

原方向：自建一套桌面伴随进程（`apps/local_runner`），自己解析可执行文件、
起子进程、管超时、杀进程树、净化子环境。

新方向：**这台机器上已经装着一套成熟的东西在干同一件事——Orca 本身。**
它是 MIT 开源（github.com/stablyai/orca，Copyright (c) 2026 Lovecast Inc.），
公开契约就是 `orca` CLI（`orca agent-context --json`，schema v1，234 条命令）。

但要把话说准，否则会高估这次复用能拿到多少：

> **Orca 成熟的是「Agent 会话编排」，不是「文本补全」。**
> 它的工作单元是"某个 worktree 里的一个 agent 会话"，不是"一次带 schema 的生成"。
> 它**整块替换掉我们最不擅长的进程与会话生命周期层**，
> 但**不替代请求-结果信箱，也不自带结构化产出契约**——那两件仍归我们。

---

## 2. 选定的形状（方案 B）

```
香港服务器（容器）                        用户桌面（Windows，已装 Orca 1.4.196）
   │                                          │
   │  ← ① 出站拉一条文本请求  ────────────────┤  伴随进程（瘦身后）
   │                                          │
   │                                          ├─ ② orca account list      看订阅额度
   │                                          ├─ ③ orca terminal create   起 Claude（显式 Opus）
   │                                          ├─ ④ orca terminal send     送提示词
   │                                          ├─ ⑤ orca terminal wait     --for tui-idle
   │                                          ├─ ⑥ 读约定好的产出文件      ← 不是抓 TUI 文本
   │  ← ⑦ 回传结果 ──────────────────────────┤  orca terminal close
```

**服务器零新增依赖，网络方向不变（仍是桌面出站）。**

被删掉的是自建版里风险最高的约 600 行：npm shim 解析、进程树杀、管道抽干、
子环境净化、Windows 命令行 32767 上限。这些 Orca 都已解决且在被持续维护。

顺带白拿的一件事：`orca account list --json` 直接给出
**Claude 订阅额度百分比与重置时间**，能在开跑前判断会不会撞限流。自建版完全没有。

### 为什么不是另外三条

| 方案 | 判断 |
|---|---|
| A：服务器装 Orca 反向驱动桌面（`--environment`，覆盖 233/234 条命令） | 能力最全，但容器要塞 AppImage + 20 多个 X/GTK 库 + `xvfb`，且网络方向要翻转（需 `ssh -R` 把桌面运行时暴露到服务器可达）。试点期成本不划算 |
| C：服务端当 Orca 协调者（`worker-start` / `worker-read --source transcript`） | 结果面最硬（hook 上报的精确 transcript）、重试与围栏全现成，但同样要服务器装 CLI，还要一个常驻协调者终端。**记为方案 B 跑通后的演进方向** |
| D：MIT 抽取模块、不依赖安装 | 许可允许，但 CLI 只是运行时的瘦客户端（`orca.exe` 仅 18 KB），"抽出会话层"实质等于抽出整个 Electron 运行时。投入产出不成立 |

---

## 3. 动手之前必须先关掉的三件事

**契约没定死之前不写代码。** 这三件都不产生模型推理、不花额度。

### G3（头号）证明走的是订阅，不是付费 API

Lead 已核实：**全局 user settings 的 `env` 里有 `ANTHROPIC_AUTH_TOKEN` 与
`ANTHROPIC_BASE_URL`，会走按量计费**；而
`claude --setting-sources project,local auth status` 报 `claude.ai` +
`subscriptionType: pro`。

用 Orca 起终端时参数由 Orca 组装，**"Orca 起的 Claude 会不会加载 global settings"
尚未验证**。必须比对两条路径的 `auth status` 都报订阅。
**只要有一边报 API key，方案 B 就得先解决参数注入，再谈别的。**

### G1 产出契约：读文件，不读屏

Orca 官方 note 自己写明：`terminal read` 默认返回累积输出，
TUI 重绘会碎成 `cclclecleaclear` 这种堆叠片段；要看渲染结果必须 `--screen`，
而 `--screen` 与 `--cursor` 互斥。

> **结论：抓 TUI 文本不能作为产出契约。**
> 约定 agent 把结构化产出写到工作目录下的固定文件（如 `.orca-out/result.json`），
> 伴随进程只读那个文件；`terminal read` 仅用于失败诊断。
> 这与 Orca 自己的 `worker_done --report-path`（长产物走文件路径）是同一个思路。

### G2 完成判据：`tui-idle` 不等于"答完了"

`terminal wait --for tui-idle` 只证明界面不动了——不证明产出完整，
也不区分"它正卡在一个只有人能回答的提示上"。
完成判据必须是 **G1 的产出文件出现且可解析**，`tui-idle` 只作为超时闸门。

---

## 4. 交付顺序

| 波次 | 内容 | 说明 |
|---|---|---|
| **0（并行）** | **WN 复核验收** | 四道门前端 WN 已报完成。其中 `StyleOptionOut` 是否暴露 `*_tokens` 由 WN 点名要 Lead 裁决。与下面各波并行，不互相阻塞 |
| **1（首位）** | **Orca 复用试点（方案 B）** | 先关 G3 → 定 G1/G2 → 一次极小的订阅冒烟（显式 Opus，不许 Codex）→ 再谈实现 |
| **2** | `topup` 收口（FR-BIL-006） | **已上线且可被利用的风险**；任务书 `orca/tasks/WG.md` 已写好未派。**没有证据表明已经发生实际资损**——同日前一份计划写成"已经在漏"，措辞不准，此处更正 |
| **3** | ADR-036 落地 + FR-CONS-012 **合成一波** | 拆开会把提示词逻辑写两遍（一遍拼进 `compose.py`，ADR-036 落地时再搬进 Agent） |
| **4** | 项目大厅卡片状态 | 读的是两个从不变化的列，跑到 `done` 的项目仍显示「草稿」 |
| **5** | ffmpeg 进镜像 + M2 骨架 | 一次性钉死 ADR-034 / ADR-035 的形状，别留"以后再改" |
| **6** | 三项要花钱的验证 | wan2.7 档位实调、万相 3×3 宫格、试点真机端到端 |

**范围纪律**：本轮只回答"本地运行时怎么做"。第 2~6 波在此**只是保序**，
不在本轮展开，也不扩到别的产品模块。ADR-034 ~ 037 的实现约束逐条仍然有效，
细节见同日前一份计划的第 4 波与第 2 波段落——**那部分内容没有被取代，
被取代的只有排序与本地运行时的架构。**

---

## 5. 已知限制（试点期接受，但要如实标注）

| 限制 | 说明 |
|---|---|
| 桌面必须开着 | Orca 对这类会话有 interactive-session affinity，桌面关机/睡眠时返回 `*_unavailable`。`status` 接口要如实反映，不能装作在线 |
| 同步生成会撞代理超时 | Next rewrites 无 timeout override；一次几分钟的生成会先被前端到 API 的同步等待掐掉。要么记为限制，要么改成异步任务 |
| 计费口径未定 | 这条路径不耗上游 Key，但耗订阅额度。**"订阅额度怎么折算 Credits"定下来之前不对外开放** |
| 终端 handle 是 runtime-scoped | Orca 重启会返回 `terminal_handle_stale`，必须 `terminal list` 重新拿，不能缓存过夜 |
| 依赖已安装的 Orca | 方案 B 下这个依赖**只落在桌面**（已装，且本机所有 Worker 本来就跑在 Orca 里），成本近似为零。方案 A/C 才会落到服务器 |

---

## 6. 前一版的状态

`apps/api/modules/local_runtime/` 与 `apps/local_runner/` 是
**未验收草稿**，本轮按要求原样保留、未删未改。
它的服务端契约（poll / result / status 的形状）仍然可用，
但**信箱的原子性有五处待修**（出队与清理的竞态、绝对截止时间、
队列深度的原子入队、完成时对"请求已缺失/已过期"的显式拒绝、清理的事务化），
另有错误文案缺中文 `user_message`、默认 provider 应改 `claude` + 显式 Opus。
逐条清单在 `orca/tasks/LOCAL_RUNTIME_ORCA.report.md` §4。

**桌面执行层（`resolve.py` / `process.py` / `adapters.py`）在方案 B 下整块作废。**
