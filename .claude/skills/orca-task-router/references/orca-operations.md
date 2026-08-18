# Orca操作手册

本手册基于Orca 1.4.179的本机CLI，核验于2026-08-11。升级前旧版的失效结论降级为存档，标注在对应小节。

**本手册只写Orca官方协议管不了的东西**：怎么把模型档位塞进启动命令、上下文交接、Worker回收、跨仓库所有权。编排协议本身以Orca自带的说明为准，派发前读一次：

```bash
orca skills get orchestration
orca agent-context --json
```

不要把官方那份内容抄进本手册。抄过一次的下场是：官方废掉了coordinator loop，我们的文档还在教它，烂了半年没人发现。运行时读取的好处是官方改协议我们不用跟。

## 目录

不必通读。按当前要做的动作跳到对应小节。

| 当前动作 | 去哪节 |
|---|---|
| 判断这是移交还是supervised，以及移交怎么送达 | [第零步：确认这是supervised，不是移交](#第零步确认这是supervised不是移交) |
| 建Run、探实验开关、接回已有Run | [派发前的前置检查](#派发前的前置检查) |
| 决定复用现有Worktree还是新建 | [复用当前feature分支](#复用当前feature分支) |
| 起一个带模型档位的Worker终端 | [创建Worker子session](#创建worker子session) |
| 建Task、交给官方接管、记dispatchId | [派发：自己起终端，交给官方接管](#派发自己起终端交给官方接管) |
| 决定派只读队友、调查型队友还是Terminal Worker | [Claude Worker的三种形态](#claude-worker的三种形态) |
| 派发完了，怎么确认Worker真的跑起来了 | [ack文件是唯一的送达证据](#ack文件是唯一的送达证据回执和input_accepted都不是) |
| Worker上下文触顶要交接 | [上下文交接](#上下文交接) |
| 收报、回Worker的提问、给Worker补约束 | [进度和回报](#进度和回报) |
| 命令返回成功但看起来没生效 | [本机环境的坑](#本机环境的坑) |
| 验收不通过要返工 | [Worker报完成的那一刻，Task已经结案了](#worker报完成的那一刻task已经结案了) |
| 关终端、收Dispatch、核对零遗留 | [Worker收尾](#worker收尾) |
| 分配多仓库的写入范围 | [跨仓库所有权](#跨仓库所有权) |

## 第零步：确认这是supervised，不是移交

`run-create`是写操作。判定必须排在它前面，一旦建了Run就已经踩了官方对full handoff的禁令，撤不回来。判定标准见[SKILL.md](../SKILL.md)"先判断当前角色"，一句话版本：用户用移交措辞并且没要求等结果，就把prompt送达然后停手，不进本手册后面任何一步。

### 移交怎么送达

三条配方，按场景选。**`terminal create`不在其中**——它只有`--worktree/--title/--command/--focus`，没有任何送prompt的入口，`worktree create --prompt`也必须配`--agent`才有收件人。早先本手册写"用`terminal create`把prompt送达"，照做的结果是建完终端就停手，自认已移交而实际什么都没送出去。

**新工作区，用户没点名模型**：

```bash
orca worktree create --name '<task-name>' --no-parent --agent codex --prompt '<任务书>' --setup run --json
```

**送给已经跑着的agent终端**：

```bash
orca terminal send --terminal '<handle>' --text '<任务书>' --enter --json
```

**用户点名了模型或effort**（本Skill最常撞到的情况，因为它天生围绕模型选择）：`worktree create --agent`收不了`--model`和`-c`，只能拆成四步。

```bash
orca worktree create --name '<task-name>' --no-parent --setup run --json
orca terminal create --worktree 'id:<返回的完整worktree id>' \
  --title '<task-name>' --command '<带模型参数的完整启动命令>' --json
orca terminal wait --terminal '<handle>' --for tui-idle --timeout-ms 60000 --json
orca terminal send --terminal '<handle>' --text '<任务书>' --enter --json
```

这里的`terminal wait`是唯一允许的"回头看"，目的是别让prompt撞上还没起来的TUI而丢掉。送完就停，不要再读输出。

## 派发前的前置检查

创建任何Terminal之前先跑这三条，否则Worker会全部起好占着额度，然后在`task-create`那一步失败：

```bash
orca status --json
orca orchestration run-current --json
# 上一条的 result.run 为 null 时，且只在为 null 时，才跑这一条：
orca orchestration run-create --objective '<本次任务的总目标>' --json
```

1. **Runtime在跑**。`orca status`要能返回。不通说明Orca没开，让用户打开Orca再继续，Lead不自己启动GUI应用。
2. **先用`run-current`看当前终端绑没绑Run**。这是只读命令，`result.run`为`null`说明没绑。别拿有副作用的`run-create`当探针问"绑了没"。
3. **没绑就建一个**。官方要求每条Task和每条消息都属于一个显式绑定的Run。没绑Run时不带`--run`的`task-create`和`task-list`会报`run_required`（2026-08-03本机实测的错误码，官方文档里没有这个字符串，复核时以实测为准）。

`run-create`同时充当实验开关探针：Orchestration实验开关关着或者runtime不支持时它会失败。

**别用`orca status`的`capabilities`判断实验开关。**2026-08-03实测那里有31项，其中3项带orchestration前缀（`contract.v1`、`federation.v1`、`federation-control-mail.v1`），但它们报的是协议版本和联邦能力，不是开关状态，没有验证过能不能当探针。（更早的一版手册写"24项里没有任何orchestration条目"，那是2026-07-31的数字，已经过期。）开关关着时`terminal create`照样成功，只有orchestration那一层才报错，所以必须提前探。

Lead在同一次任务里只建一个Run。中途换终端或者Lead重开时接回已有Run：

```bash
orca orchestration run-list --json
orca orchestration run-use --id '<run_id>' --json
```

普通`run-use`接不回来时才加`--takeover-legacy`，它会fence掉原来的coordinator。**自己重开的Lead用它是对的**，官方为的就是"原coordinator不可用或无法证明持有权限"这个场景。别拿它去绑别人正在协调的Run，那会把对方踢下线。

绑不上也不想takeover时，`task-create`和`task-list`都支持`--run <run_id>`显式指定，可以不依赖终端绑定继续干活。

开关关着且用户不方便开时，降级方案是只用`terminal create`加`terminal send`把任务送达，Lead改用`terminal read`轮询收报，不建Run也不建Task行。

## 复用当前feature分支

多Agent不等于必须创建新分支。先查询现有工作区：

```bash
orca repo list --json
orca worktree list --json
orca terminal list --json
```

当`erp-admin`和`lighthouser`已经分别位于目标feature分支时，直接在两个现有Worktree中创建Terminal。只有需要隔离实验、并行修改同一仓库或用户明确要求时才创建新Worktree。任务独立、想并行、觉得分开更清爽，这些都不构成隔离需求。

### 真需要新Worktree时

官方推荐`worktree create --agent`一步到位，agent直接起在第一个终端里，不会多出壳。**但那条路传不进模型参数**，我们必须走两步：

```bash
orca worktree create --name '<task-name>' --setup run --json      # 不加 --agent
orca terminal create --worktree 'id:<返回的完整worktree id>' \
  --title '<task-name>' --command '<带模型参数的完整启动命令>' --json
```

三件事要注意，第一条是2026-08-03实测撞出来的：

- **两步路会留下壳，数量看仓库配置。**实测同一天：knowledge-base里不带`--agent`建出来就有2个终端，lighthouser-prd里是1个。别假设固定数量，也别假设一定有。收尾时**先用`terminal list`或`terminal show`确认某个终端确实没用过再关**，仓库配置的默认tab长得跟多余的壳一样，误关会砍掉真在跑的东西。壳不清掉会踩[SKILL.md](../SKILL.md)完成标准里"没有遗留Terminal"那条。
- `--worktree`必须用返回值里完整的`<repo-id>::<path>`，光给repo id定位不到新建的那个。
- 决定lineage：新工作依附于当前worktree就用子级，独立的加`--no-parent`。lineage不决定Git基线，不写`--base-branch`就用仓库默认基线，除非用户明确要求从当前分支叠。

## 创建Worker子session

所有Worker都在Orca Terminal里起独立子session，`--command`用[Session启动指南](session-launch-guide.md)的对应档位命令，全部显式1M context。按使用频率排列：

```bash
# Claude Opus 5（high＋1M）：Claude实施主档，接规格不闭合的实施型工作
# max只属于Lead本体，Worker不抄Lead的命令
orca terminal create \
  --worktree 'path:/absolute/path/to/repo' \
  --title 'opus5-<task>' \
  --command 'claude --model '\''claude-opus-5[1m]'\'' --effort high --dangerously-skip-permissions' \
  --json

# Claude Opus 4.8（xhigh＋1M）：规格闭合实施的分摊档，跟Codex三档轮着用
orca terminal create \
  --worktree 'path:/absolute/path/to/repo' \
  --title 'opus48-<task>' \
  --command 'claude --model '\''claude-opus-4-8[1m]'\'' --effort xhigh --dangerously-skip-permissions' \
  --json

# Codex实施主力，三档按周额度水位选，派之前先跑
#   python3 ~/.config/claude-cc/orca-codex-usage.py
# 水位低于60%用Sol，60%到85%用Terra，高于85%用Luna。三档都不开Fast。

# Sol（ultra）：水位低于60%
orca terminal create \
  --worktree 'path:/absolute/path/to/repo' \
  --title 'codex-<task>' \
  --command 'codex --model gpt-5.6-sol -c model_context_window=272000 -c '\''model_reasoning_effort="ultra"'\'' --dangerously-bypass-approvals-and-sandbox' \
  --json

# Terra（max）：水位60%到85%
orca terminal create \
  --worktree 'path:/absolute/path/to/repo' \
  --title 'codex-<task>' \
  --command 'codex --model gpt-5.6-terra -c model_context_window=272000 -c '\''model_reasoning_effort="max"'\'' --dangerously-bypass-approvals-and-sandbox' \
  --json

# Luna（max）：水位高于85%
orca terminal create \
  --worktree 'path:/absolute/path/to/repo' \
  --title 'codex-<task>' \
  --command 'codex --model gpt-5.6-luna -c model_context_window=272000 -c '\''model_reasoning_effort="max"'\'' --dangerously-bypass-approvals-and-sandbox' \
  --json

# Claude Fable 5（high默认档）：只在识别为长期自主调查时用
orca terminal create \
  --worktree 'path:/absolute/path/to/repo' \
  --title 'fable-<task>' \
  --command 'claude --model '\''claude-fable-5[1m]'\'' --effort high --dangerously-skip-permissions' \
  --json
```

**创建Terminal之前**，先给目标worktree铺上下文守卫，否则Worker跑到触顶也不会提醒：

```bash
~/.claude/skills/orca-task-router/scripts/install-project-guard.sh /absolute/path/to/repo
```

这个脚本往目标仓库的`.claude/settings.json`合并一条PostToolUse hook，已有配置会保留，重复跑会跳过。

守卫必须放项目级，不能放全局。本机每条启动路径最后都汇到`claude-cc`，它必须用`--settings`注入API Key Helper，而那个flag会让`~/.claude/settings.json`里的hooks不生效。项目级配置在它之上被读取，能正常触发。2026-08-01实测确认。

Codex没有等价的hook机制，所以Codex Worker不受守卫覆盖，见下方"上下文交接"。

Opus 4.8的`max`灾备命令见[Session启动指南](session-launch-guide.md)，只在Opus 5不可用时使用。

Claude实施型Worker另有三条注意。守卫必铺：上下文守卫只对Claude生效，Claude实施型正是它的受益者，Codex Worker反而不受覆盖。Worker不自派subagent干实施活：subagent不受守卫覆盖，也不在Orca的账上，实施活要拆就交回Lead拆成新Task。返工和交接协议按本手册走：`escalation`交接那套只有Claude接得上，这是Claude实施档独有的退路。

危险权限参数只在可信项目目录使用。启动后让Worker先跑`/status`（Codex另查Fast状态），实际模型、档位或上下文与命令不符时停止任务并报告。

记录返回的Terminal handle。**派发前必须等TUI就绪**，否则送进去的内容会被还在启动的CLI吞掉：

```bash
orca terminal wait --terminal '<terminal_handle>' --for tui-idle --timeout-ms 60000 --json
```

Codex和Claude的TUI都要十几秒才起来。跳过这一步的典型症状是Worker停在空提示符，Lead用`terminal read`只看到启动横幅，一直等不到回报。

## 派发：自己起终端，交给官方接管

等到就绪后创建Task，然后用`worker-start --terminal`把这个已经起好的终端接进官方的Dispatch生命周期。

> [!WARNING]
> 这条路在旧版Orca上曾全面失效，`worker-start`一律报`selector_not_found`，八种选择器形式全试过。`1.4.179`上可用：2026-08-11这轮的Worker全部由它派出，终端`reused`、supervised Dispatch正常建立、`worker_done`结案，冒烟测试也断言了同一条路径。再撞见这个错就按本节末尾"worker-start故障存档"退`dispatch --inject`，不要反复试选择器。

```bash
orca orchestration task-create \
  --task-title 'Implement metrics endpoint' \
  --display-name 'ERP metrics' \
  --spec '<完整Task契约>' \
  --json

orca orchestration worker-start \
  --task '<task_id>' \
  --worktree 'path:/absolute/path/to/repo' \
  --terminal '<terminal_handle>' \
  --json
```

> [!WARNING]
> **`--timeout-ms`不是Worker的任务预算，是等TUI就绪的超时**，默认60秒，实现里只喂给一次`waitForTerminal`，之后原样回显在回执里。写成`1800000`不会给Worker半小时干活，只会让**Lead自己**在终端迟迟不就绪时阻塞半小时。多数情况下不写，让它用默认值；只有明知目标终端启动特别慢时才小幅调高。

**`--worktree`每次都要写。**不写的话`worker-start`按Lead自己所在的worktree解析那个handle，Worker只要不在同一个worktree就报`terminal_worktree_mismatch`。这不是只有新建worktree才会踩：Lead在A仓库、Worker起在B仓库同样会踩，而跨仓库派发正是本Skill最常见的场景。2026-08-03实测确认，值跟`terminal create`用的那个保持一致即可。

**必须从返回值里记下`dispatchId`**，后面给Worker补约束、返工、查状态、重试全要用它。忘了记可以补查：

```bash
orca orchestration dispatch-show --task '<task_id>' --json
```

`worker-start`在这里不新建终端，返回的`effects`里会写`"kind":"terminal","action":"reused"`。它做的是绑Task、注入生命周期前言、设超时、建Dispatch。前言自带回报要求，所以**Worker不需要自己去读官方skill**，只有Lead需要。

### 为什么是这条路，而不是`worker-start --agent`

`worker-start`有两种起法。`--agent <id>`那种一条命令搞定建worktree加起agent，但它启动的是预设agent，传不进`--model`、`--effort`和Codex的`-c`参数，而这三样正是本Skill全部的价值。所以我们用另一种：自己用`terminal create --command`把带参数的Worker起好，再用`--terminal <handle>`交给它接管。

2026-08-03实测：Codex用完整自定义命令起好之后被`worker-start --terminal`接管，终端是复用不是新建，Worker自报仍是`gpt-5.6-sol / ultra / fast`，参数一个没丢，同时拿到了Dispatch和下面那套故障处置命令。

比只用`dispatch --inject`多拿到的东西：`--retry-of`带血缘重试，以及`worker-show`/`worker-read`/`worker-stop`/`worker-abandon`四条故障处置。这些是我们自己写不出来的。（早先这里还列了"超时管理"，那是把`--timeout-ms`读成了任务预算，见上面的警告。）

`dispatch --inject`仍然可用，官方也保留了它。同样的自定义argv，`worker-start --terminal`是它的超集，正常情况下没有理由再用它；它是再次撞上`selector_not_found`时的退路，故障存档见下一节。

### worker-start故障存档：旧版曾一律报`selector_not_found`

<!-- consistency:allow-start -->
**2026-08-10在Orca 1.4.177上确认已恢复**：真实派发直接走通，`worker-start --terminal`接住带自定义argv的Claude终端，返回supervised Dispatch（terminal `reused`、`dispatch_input: accepted`），Worker后续`heartbeat`和`worker_done`全部正常结案。根因始终没有查清，恢复对应的是Orca从1.4.164升到1.4.177，所以本节降级为故障存档，记录当时的探针矩阵，供再次撞见`selector_not_found`时使用。

2026-08-05和08-06连着三次派发全部撞上这个错，08-06专门做了一轮探针把变量隔离干净。

**当时的结论：本机（Orca 1.4.164）的`worker-start`在Task解析之后返回裸`selector_not_found`，对所有能传的参数都不敏感。改用`orca orchestration dispatch --task <id> --to <handle> --run <run_id> --inject`，一次成功。**

探针矩阵，每一格都实测过，全部返回同一个`{"code":"selector_not_found","message":"selector_not_found"}`：

| 变量 | 试过的取值 |
|---|---|
| 仓库 | `lighthouser`、`erp-admin`，两个都在`worktree list`里、都是`isMainWorktree: true` |
| `--worktree` | `path:`、`id:<repo-id>::<path>`、`name:`、`branch:`、`current`、`active`、整个不传、传一个不存在的路径 |
| `--run` | 传Task所在的Run、不传 |
| `--terminal` | 真handle、假handle |
| 终端形态 | 跑着Claude TUI的、跑着Codex TUI的、裸shell |

**判定失败点靠的是这一条**：`--task`给一个不存在的id，报的是`{"code":"task_not_found","message":"Task task_000000000000 was not found in Run run_5be638d4ab62."}`，带上下文、指名Run。所以Task查得到、Run也解析对了，失败发生在Task解析之后，而且跟worktree选择器、终端handle都无关——传假的和传真的一模一样。

顺带推翻本文另一条：下面"五、supervised那一组命令只认被监督的Worker"写着`worker-start --terminal`指向裸shell报`agent_unconfigured`。08-06拿裸shell实测，报的是`selector_not_found`，没有`agent_unconfigured`。那条结论是照help文本推的，没实测过。

没有定位到根因。`orca environment list`是空的（本机没有远程runtime，本地跑不该需要），`orca status`报`appVersion: 1.4.164`，跟`canonical-facts`里记的版本一致，所以**不是版本升级导致的漂移**——2026-08-03那次Codex接管成功的记录与今天的实测在同一个版本上互相矛盾，说明当时的成功要么依赖了某个没记下来的前置条件，要么当时的记录本身有问题。哪一种都还没查清。

**Lead现在怎么做**：正路是`worker-start --terminal`，`1.4.177`起可用。只有再次撞见`selector_not_found`时才退`dispatch --inject`，并且不要在`worker-start`上反复试选择器，八种形式当年都试过。退路的代价要认清——`--retry-of`带血缘重试和`worker-show`/`worker-read`/`worker-stop`/`worker-abandon`四条故障处置**全都拿不到**，因为它们只认`worker-start`建出来的supervised Dispatch。`dispatch`建的轨道上，Worker仍然能合法发`worker_done`结案，Lead仍然能`check --wait`收，验收不通过的返工只能靠"终端不关、建新Task、再`dispatch`一次同一个终端"这条手动路径。

再撞见这个错时值得试的方向：`worker-start`的usage写着"Start one supervised worker on the Run home or a connected Orca server"，怀疑跟Run的home或者supervised worker的落位有关，还没验。
<!-- consistency:allow-end -->

### ack文件是唯一的送达证据，回执和`input_accepted`都不是

**任务书第一行必须让Worker`touch`一个ack文件，Lead轮询这个文件。**这是硬要求，不是可选的谨慎做法。派发路径上没有任何一个回执字段能替代它，正路和退路都一样。

`worker-start`那条正路给的信号最像证据，也最骗人。2026-08-11实测一次Worker进程假死，全部信号都是绿的：`worker-start`返回`ok: true`、`state: ready`、`stage: input_accepted`；`terminal read`能读到完整的生命周期前言和TASK块，说明输入确实送进了那个终端。然后进程CPU掉到0.0%，30分钟零输出，`worker-show`的`last_heartbeat_at`始终是`None`，stage再没往前推过一格，任务书第一行要求的ack文件一次都没被touch。换一个终端重派，20秒就ack了。

所以`worker-start`的返回值和`input_accepted`证明的是**输入被接收**，不是**Agent跑起来了**。两者之间隔着一个进程，那个进程可以收下输入然后再也不动。

判定假死要三条交叉，只看一条都会判错：

| 看什么 | 怎么看 | 假死时的样子 |
|---|---|---|
| 进程还在不在干活 | `ps`查Worker进程的CPU | 掉到0.0%并且不再回升 |
| 终端最后一次输出 | `orca terminal read --terminal <handle> --cursor 0 --json` | 停在preamble和TASK块，之后没有新内容 |
| 编排层收没收到心跳 | `orca orchestration worker-show --dispatch <ctx_id> --json` | `last_heartbeat_at`是`None`，`stage`不推进 |

三条里任意单独一条都有正常解释：Worker在长时间思考时CPU也可能很低，终端在读文件时也可能久不输出，心跳字段在刚派发那几十秒里本来就是空的。三条同时成立、并且ack文件没落盘，才判假死。判了就换终端重派，不要接着等——上面那次等了30分钟，重派只花20秒。

`last_heartbeat_at`为空还有第二种成因，跟假死长得一模一样。2026-08-11实测：Worker发`heartbeat`时漏了`--dispatch-capability`，Orca不静默丢弃，而是往Run里记一条`Rejected heartbeat: <原subject>`，`payload`带`_orcaLifecycleRejection`、`code`是`dispatch_capability_invalid`，而`worker-show`的`last_heartbeat_at`**照样停在`None`**。也就是说这个字段为空可能是Worker根本没跑，也可能是Worker活得好好的、只是每条心跳都被拒。**区分这两种的正是ack文件**：没跑的不落盘，活着的落盘。这是ack硬要求的第二条理由。

ack这个做法在本Skill的实践里已经用了几轮，一直没写进手册当硬要求，代价就是这30分钟。

<!-- consistency:allow-start -->
2026-08-08实测，Orca 1.4.164：`dispatch --inject`对Codex TUI的送达时好时坏。同一天、同一个版本、同一条命令，第一次送到了，Worker 145秒就交了活；第二次静默失效，回执照样是`ok: true`，可Codex的上下文停在100%、标题退回默认、干坐5分钟没有任何动作。同样这两轮打给Claude TUI都送达了。结论是`--inject`的回执证明不了送达，它只证明Orca执行了注入这个动作，注入的内容有没有落进目标CLI是另一回事。这条在1.4.177上没有复测；带确认的回路照走，它不依赖注入路径的好坏，`worker-start`正路派发同样适用。
<!-- consistency:allow-end -->

送达一律走带确认的回路，不拿回执当证据：

1. 任务书写成文件落到工作目录，`terminal send`只发一句指针，指向那个文件。
2. 任务书第一条指令要求Worker先创建ack文件，调度方轮询这个ack来判断送达。
3. 轮询超时就重发指针句，重发次数用尽还没有ack就停下来上报，不假装已经送达。

判据是Worker侧的可观测动作，不是回执。`ok: true`只说明Orca执行了注入，ack文件落盘、答案文件落盘这些Worker真动过的痕迹才算送达。指针句重发没有副作用，任务书里写明ack已存在就忽略重复通知，重发几次都不会让Worker重做一遍。

可执行实现在knowledge-base仓库的`wechat-clean/_tools/dispatch_worker.py`。这个脚本正在实施，可能晚于本节落地，指路以该文件实际存在为准，还没有就按上面三步手动走。它跟本文「本机环境的坑」第一条那个zsh自动纠错坑连着：指针句是中文时发给裸shell会被纠错拦下，终端停在`correct`提示等回答，live验证得用`--pointer-template`把指针句换成纯shell命令绕开纠错。

## Claude Worker的三种形态

判据是**失败之后需不需要一条可审计的返工血缘**。需要就走Orca Terminal形态，能整体重派就走Agent Teams队友。任务重要程度和哪个形态更强都不是判据。

不要把判据读成"哪个形态接得上"。两个形态都接得上：队友停在半路时用`SendMessage`发一条，它会从自己的transcript接着跑。判据问的是出事之后有没有一本账——Task状态流转、`--retry-of`的重试计数、连续失败3次的官方熔断。

| 形态 | 工具 | 拿得到orchestration | 用途 |
|---|---|---|---|
| Agent Teams只读队友 | `Read, Grep, Glob` | 否 | Review、风险检查、跨仓库契约核对 |
| Agent Teams调查型Worker | 上面三个加`Bash` | 否 | 跑测试、翻日志、执行构建、查运行时状态 |
| Orca Terminal子session | 全权限 | 是 | 改文件的实施型工作，以及需要返工血缘的长跑任务。Claude和Codex都走这个形态，模型按规格闭合度选 |

### 预装角色

- `orca-opus-reviewer`：跑Opus 5的默认只读队友，承担Review、风险检查和跨仓库契约核对。
- `orca-opus-investigator`：跑Opus 5，比上一个多一个`Bash`。答案读源码读不出来、必须实际跑一下才知道时派它。
- `orca-fable-architect`：跑Fable 5，在任务过了[Session启动指南](session-launch-guide.md)"Fable的两条入口"之一后才派——深度只读调研的范围条件，或长期自主调查的闸门。

三个角色的`tools`字段里都没有`Write`和`Edit`，这是工具白名单层面的硬约束。`orca-opus-investigator`还在系统提示里被禁止借Bash绕道改仓库，包括`git commit`、重定向写入仓库路径、`sed -i`和打补丁，临时文件只允许落在`/tmp`。这一层是提示词约定，期望它真需要改文件时停下来回报，强度不如工具白名单，不要当成保证。

Agent Teams队友共享Claude Teams任务和消息，不要在其Terminal里再手动运行`claude`或`claude-cc-teams`。

### 什么时候仍然必须用Terminal形态

Agent Teams队友拿不到Orca的orchestration，这是选型时唯一要认真权衡的代价。上一节列过的那份`worker-start --terminal`独有能力，只有Terminal形态吃得到。

判断型和调查型工作用不上这些：它们跑完给结论，失败了整体重派一个即可。实施型工作用得上，因为改到一半的工作树、返工路径和上下文交接都依赖Dispatch这条线。

判据有一个必须写明的例外：`orca-fable-architect`跑的是深度只读调研或跨工作时段的长期自主调查，后者按需不需要可审计的返工血缘来判，它该走Terminal形态，但它现在就是Agent Teams角色，拿不到orchestration。Fable角色当前的定义就是这样，判据在这里不成立。派它之前要清楚中途失败只能整体重派，没有带血缘的返工路径。

Codex Worker只有Terminal形态一条路，Agent Teams起不了Codex。

## 上下文交接

Worker跑久了会滑出Smart Zone。理论上有效工作区间是100到120k，超过之后注意力分散、输出质量下降。团队取的交接阈值写在[Session启动指南](session-launch-guide.md)的`canonical-facts`块里。

### 触发

守卫是目标仓库`.claude/settings.json`里的一条PostToolUse hook，由`scripts/install-project-guard.sh`铺设。它在每次工具调用后算已用量，越线时向Worker注入一次提醒。

阈值按context window的百分比算，不是固定token数。固定值会在小窗口会话上永远触发不了：按1M调的300k，在200k的会话里到不了。窗口大小由statusline发布，守卫读它。

同一个会话同一个阈值只提醒一次，不会刷屏。守卫只在Worker主线程触发，Worker自己派出的subagent不会误触发，也不会替主线程消耗掉那次提醒。

用户侧还有一层：statusline在越线时把上下文段显示成`⚠ 该交接`，不依赖hook，任何会话都能看到。

Codex Worker没有等价机制。Codex的上下文由它自己管理，Lead在长任务里主动用`orca terminal read`观察它的回报节奏，发现它开始重复劳动或丢失早期约束时手动收回任务。

### Worker收到提醒后做什么

1. 停在当前这步，不要开始新的子任务。
2. 写交接文档，格式见下。
3. 用`escalation`回报给Lead，把交接文档放进`--body`：

   ```bash
   orca orchestration send --type escalation \
     --subject '上下文触顶，请求交接' \
     --body '<交接文档全文>' \
     --task-id '<task_id>' --dispatch-id '<dispatch_id>' --json
   ```

   **不要用`worker_done`**。带有效ID的`worker_done`会把Task和Dispatch一起标成结束，而触顶交接的任务并没有做完，标完成会让Lead在任务列表里看不到它还欠着东西。`escalation`的语义正好是所有权仍然有效、需要Lead介入。
4. 回报后**结束当前回合，停在提示符**，不要退出会话，不要自己创建新Terminal，也不要自己派新Worker。会话真关掉Lead就读不到你的输出、也没法回头问交接细节了。什么时候关由Lead决定，见下方"Worker收尾"。

### 交接文档格式

```text
目标：原Task目标，原样带过去，不要改写
已完成：具体产出，带文件路径和验证结果
剩余：还没做的部分，按执行顺序排
下一步：接手者要做的第一个动作，具体到命令或文件
已排除：试过但走不通的路子，附证据
风险：已知的坑和未决问题
验收：原Task的验收标准
```

"已排除"这一条最值钱。没有它，接手的Worker会把前一个Worker踩过的坑再踩一遍，交接就白做了。

### Lead收到交接后做什么

1. 先验收已完成部分，别直接转发。前一个Worker是在Smart Zone之外写的这份文档，可能有遗漏或乐观描述。
2. 判断要不要调整Task边界或换档位。反复触顶通常说明Task切得太粗。
3. 用`task-create`建一条**新Task**，spec里带上交接文档和剩余范围，再按上面"派发"那一节起新Worker、`worker-start --terminal`接管。不要把新Worker接到旧Task上：旧Task还挂着旧Dispatch，两个Dispatch抢一条Task会让完成状态互相覆盖。
4. **先把旧Worker的输出读出来**，`worker-stop`会直接kill掉那个终端，读晚了就没了：

   ```bash
   orca terminal read --terminal '<旧terminal_handle>' --cursor 0 --limit 200 --json
   ```
5. **收口旧Dispatch。**Worker发的是`escalation`不是`worker_done`，2026-08-03实测确认这条消息不结案：Task停在`dispatched`，Dispatch也停在`dispatched`、`completed_at`为空。不收口的话Lead的`check --wait`会一直挂着它：

   ```bash
   orca orchestration worker-stop --dispatch '<旧dispatch_id>' --json
   ```

   顺利时这一条命令把三件事一起做了，实测返回`processAction: closed_agent_terminal`、`ptyKilled: true`：Dispatch变`failed`、Task自动变`blocked`、agent终端被关掉。它只关那一个被监督的agent终端，worktree、setup终端和别的pane都不动。

   **但它不是无条件的三件套。**Orca会先把Task标blocked，再去核对进程：进程对不上或者已经不在运行时，它直接返回一个unknown回执，**终端不关、Dispatch也不settle**。所以要看回执再决定收不收工：拿到`closed_agent_terminal`才算收干净；回执里没有它，就得自己`terminal close`补上，并且回头确认Dispatch状态。这种半完成状态恰好是"自称零遗留、实际留了一堆还在跑的Worker"那次事故的形态。

   **`stop_unknown`加`tab_not_found`是这个半完成态最常见的长相。**2026-08-11撞到两次，两次的实际结果都是进程已经停干净了，但回执自己说不出这一点。这个组合下不要二选一地猜，用三条交叉判定真实状态：`worker-show --dispatch <id>`看`worker.state`和`dispatch.status`，`terminal list --worktree`看它还在不在列表里，`ps`看进程还在不在。三条都指向已停才算收干净；有一条说它还活着，就按还活着处理。回执的`ptyKilled`不进这三条，它报的不是事实，理由见下方"Worker收尾"里的警告。

   如果连Worker到底停没停都确认不了，用`worker-abandon --dispatch <id>`把它fence掉。那条命令不做任何远程、进程或文件系统操作，代价是资源可能还活着。
6. 确认新Worker已经起来并拿到交接文档之后收工。旧终端已经被`worker-stop`关掉了，不需要再走"Worker收尾"。

Lead本身触顶时不适用这套流程，Lead没有上级可以回报。当前由用户手动压缩或交接。

## 进度和回报

**收报协议整套以`orca skills get orchestration`为准**，读它的Messaging和Preferred Supervised Worker Loop两节。等待、批次确认、消息类型、心跳语义、超时怎么解读，那边都写全了，本手册不复述——复述过一次的下场见本文开头。

本节只写官方那份里没有、而本机会踩的坑。

**Worker提问必须回。**Lead等待时如果把`question`放进`--types`过滤器，就得知道怎么回，否则Worker用`ask`阻塞后会一直干等到超时：

```bash
orca orchestration reply --id '<msg_id>' --body '<答复>' --json
```

`gate-create`是另一回事，那是Lead自己给Task DAG设决策点用的，不能拿来回Worker的提问。

**给某个Worker补约束用Dispatch地址，不要用Terminal handle。**handle是路由元数据会变，Dispatch ID是稳定身份：

```bash
orca orchestration send --to 'dispatch:<dispatch_id>' \
  --subject 'Contract update' --body '<更新后的约束>' --json
```

### 本机环境的坑

**一、Orca终端的zsh开着自动纠错，会拦下`terminal send`发过去的命令。**2026-08-03实测：发`which -a claude`被拦成`zsh: correct 'claude' to '.claude' [nyae]?`，终端就停在那等回答，Lead从外面看像是Worker没动静。这只影响`terminal send`给shell发命令的场景，`terminal create --command`是直接执行不过纠错。被拦住时发一个`n`（不要带`--enter`）就能继续。

**二、`terminal read`不带`--cursor`可能返回空。**第二次读同一个终端时`tail`会是空数组。要看完整输出加`--cursor 0`。

**三、`--json`输出是多行缩进JSON，不是JSON Lines。**按行解析会全部失败并且静默返回空，看起来像命令没结果。整体解析。`check --wait`的keepalive走stderr，跟stdout分开。

**四、`worker_done`的权威绑在Dispatch的assignee终端上，不在参数上。**从别的终端发一条带着正确`--task-id`和`--dispatch-id`的`worker_done`，命令返回`ok: true`，但Task和Dispatch状态纹丝不动；同一条命令从assignee终端发出去，两者立刻结案。2026-08-03实测，两个方向都验过。所以Lead不能靠补发完成消息给卡住的Worker收尾，那条路只有`worker-stop`。

**五、supervised那一组命令只认被监督的Worker。**`worker-start --terminal`指向裸shell报`agent_unconfigured`，2026-08-11冒烟测试实测。这条结论被推翻过一次又被恢复：2026-08-06在`worker-start`整体失效的那一版上拿裸shell实测，报的是`selector_not_found`，当时据此判定它是照help文本推的（版本号见上方"worker-start故障存档"）。现在看那是被整体失效盖住了——所有调用都停在同一个错上，看不见后面那一步。`worker-abandon`指向一条用`dispatch`建的轨道报`dispatch_not_found`，因为那条轨道没有supervised worker。`worker-stop`和`worker-abandon`都只能用在`worker-start`建出来的Dispatch上。反过来，`orchestration dispatch`能给裸shell建轨道，而那个shell也能合法发`worker_done`结案——轨道和监督是两件事。

**六、`orchestration check`的超时参数是`--timeout-ms`，没有`--timeout`。**写`--timeout`直接被拒，返回`invalid_argument`加`Unknown flag --timeout for command: orchestration check`，2026-08-11实测。这类回执的`error.data.validFlags`会把该命令认的全部flag列出来，撞到`invalid_argument`时先读它，比翻help快。

**七、`--types`决定哪种消息能唤醒这次等待，不决定批次里会出现什么。**2026-08-11实测：Lead用`check --wait --types worker_done,escalation,question`等着，被一条`escalation`唤醒，交付回来的批次是3条——那条`escalation`，加上跟着一起来的2条`heartbeat`。过滤器挡不住心跳，收报循环拿到批次之后必须自己按`type`分流。反过来也成立：只有心跳、没有匹配类型的消息时，这次等待不会被唤醒，心跳自己满足不了过滤器，等到超时返回的是空批次。要单独查心跳走`check --all --types heartbeat`，`--all`按handle列全部历史消息并且不标已读。

**八、`worker-show`只吃`--dispatch <ctx_id>`。**传`--task`报`invalid_argument`，跟第六条同一个形状。Dispatch ID没记住就先`dispatch-show --task <task_id>`换回来，再拿去查。`worker-stop`、`worker-abandon`、`worker-release`同样只认dispatch，不认task。

<!-- consistency:allow-start -->
Coordinator loop已经被官方废弃。`orchestration run`、`run-stop`、`coordinator-start`、`coordinator-stop`四条命令仍然存在于CLI里，但只返回`orchestration_migration_required`（`data.reason`是`command_retired`），不产生任何效果，`--max-concurrent`跟着一起没了。2026-08-03实测四条全部确认。Task DAG的并发和依赖由Lead自己按下方"跨仓库所有权"和SKILL.md的并发边界控制，Orca不做调度。
<!-- consistency:allow-end -->

## Worker报完成的那一刻，Task已经结案了

Worker怎么发`worker_done`由官方前言注入，Worker不读本手册，所以这里不写它的命令。但**结案这个后果直接决定Lead怎么返工**，Lead必须知道。

带有效`taskId`加`dispatchId`的`worker_done`会让Orca自动把Task和Dispatch一起标成完成。Lead收到完成消息时，那个Dispatch已经settled。

### 验收不通过怎么返工

**建一条新Task写明哪里不合格，派给同一个终端。**

```bash
orca orchestration task-create \
  --task-title '<原标题>-rework' \
  --spec '返工：上一轮你交的是<具体描述>，不符合<具体要求>。<明确的期望结果>。' --json

orca orchestration worker-start \
  --task '<新task_id>' \
  --worktree 'path:/absolute/path/to/repo' \
  --terminal '<原terminal_handle>' \
  --json
```

`--worktree`同样不能省，规则见上面"派发"那一节。返工发生在别的仓库是常态，漏了直接报`terminal_worktree_mismatch`。

关键在`--terminal`指回原来那个终端。**终端不关，Worker的上下文就还在**，它记得上一轮做了什么，只改不合格的部分。这跟关掉重起是两回事：重起会丢掉全部上下文，新Worker要从头理解任务并重踩已经排除过的坑。

2026-08-03实测这条路径：Worker第一轮回了一整句话不符合"只回数字"的要求，按上面的方式返工后回了纯数字，且明确引用了上一轮的内容。

### 返工路径的两个坑，都是实测撞出来的

**一、`--retry-of`挑Dispatch状态，也挑Task状态。**Orca的判定条件是：worker必须是`failed`、`stopped`或`abandoned`之一，**并且**Task必须是`failed`或`blocked`之一，`--retry-of`还必须指向该Task最新的那条Dispatch。任何一条不满足都报`task_not_startable`。

验收不通过时Worker是自认成功的，Dispatch结案、Task也结案，两个条件都不满足，所以这条路走不通。**但不要因此去改Task状态**：把Task改成`ready`只会让它离可重试区间更远，`ready`恰好不在允许值里。这一点跟直觉相反，早先的版本把原因写成"判定只看Dispatch不看Task"，方向是反的。

`--retry-of`真正的用武之地是`worker-stop`或`worker-abandon`之后：那两条命令会把Dispatch settle 成失败、把Task标成`blocked`，正好落进可重试区间。重试时`--terminal`或`--agent`必须显式给，placement不会自动继承。

"验收不通过时被拒"这条是2026-08-03手工验的，自动化测不了——构造一个completed的Dispatch必须让agent自己发`worker_done`，那就把测试押在模型愿不愿意听话上了。冒烟测试断言的是能确定构造的另一半：`--retry-of`在`worker-stop`产生的failed Dispatch上被接受。

**二、别只把原Task重新打开就再派一次。**`task-update --id <id> --status ready`确实能把Task改回可派发，而且**不会动已经结案的Dispatch**（冒烟测试断言了这一点）。但再派一次时注入Worker的还是**原来那份spec**，Worker不知道哪里不合格，会原样再做一遍、给出一模一样的回答。实测确认。返工要求必须进新Task的spec。

同一条Task连续失败3次，Orca会熔断这个dispatch上下文并把Task标成failed。返工次数有限，[SKILL.md](../SKILL.md)的自动升级规则把交回Lead重新判断卡在第2次，就是为了留出余量。

## Worker收尾

Worker跑完不会自己消失，也**不应该**自己消失。Worker无法预知Lead的验收结果，自行退出就断了返工的路。回收一律由Lead发起。

关掉重起是最差的选项：Worker的全部上下文随Terminal一起消失，新Worker要从头理解任务、重新踩一遍已经排除过的坑。返工永远比重起便宜。

### 什么时候该关

| 情况 | 回收前先做什么 |
|---|---|
| 验收通过 | 复核完代码和测试，确认不需要再问Worker任何问题 |
| 上下文触顶交接 | 收完交接文档，确认新Worker已经起来并拿到内容 |
| 任务放弃或Worker失败 | 把失败证据和已排除的假设读出来存进Task记录 |

关之前先确认Dispatch的状态，别凭"好久没动静"下判断：

```bash
orca orchestration worker-show --dispatch '<dispatch_id>' --json
```

返回值里有**两个不同的状态字段，别只看一个**：

`result.dispatch.status`是Dispatch的记账状态，取值`pending`/`dispatched`/`completed`/`failed`/`circuit_broken`：

- `dispatched`：还挂着。可能在跑，也可能发过`escalation`但没人收口。
- `completed`：Worker报过`worker_done`。
- `failed`：失败，或者被`worker-stop`收掉了。
- `circuit_broken`：同一条Task连续失败3次触发的熔断。**这个值不要当成"没见过，再等等"**，它意味着Orca已经不再接受这条Task的新尝试。

`result.worker.state`才是决定能不能重试的那个，取值`starting`/`ready`/`start_unknown`/`failed`/`succeeded`/`stopping`/`stop_unknown`/`stopped`/`abandoned`。`--retry-of`只认其中的`failed`、`stopped`、`abandoned`。

官方文档讲恢复处置时说的`ready`和`outcome_unknown`指的是**worker**那一侧，不是dispatch。早先本手册让Lead只读`dispatch.status`并说"官方那两个值本机没见过"，那是找错了字段。遇到`stop_unknown`或`start_unknown`按官方处置走：先`worker-stop --dispatch <id>`再查一次，还是不确定就`worker-abandon`，那条只在编排层fence，不碰进程和文件，要接受资源可能还活着。

### 两条回收路径，别混用

**验收通过的正常回收**走`terminal close`。它不可逆，关掉之后读不到任何输出、也没法再让它返工，所以顺序不能反：

```bash
orca terminal read --terminal '<terminal_handle>' --cursor 0 --limit 200 --json
orca terminal close --terminal '<terminal_handle>' --json
```

`--cursor 0`不能省。同一个终端读第二次时，不带cursor拿到的`tail`会是空数组，看起来像Worker什么都没输出。

**`worker-release`不是这条路的替代品，本Skill用不上它。**它的说明写着只关"coordinator自己拥有的那个agent终端"，明确排除reused和pre-existing的终端。而本Skill全部Worker都是我们自己`terminal create --command`起好、再交给`worker-start --terminal`接管的，回执里那句`"action": "reused"`正是模型档位没丢的证据，也正是`release`拒绝碰它的理由。2026-08-11两次实测都返回`retained`加`external_terminal`，终端一个没关，回收还得退回上面那两条`terminal read --cursor 0`加`terminal close`。

另外它挑Dispatch状态，比`--retry-of`还窄：2026-08-11实测，`stopped`和`abandoned`的Dispatch都被`dispatch_inactive`挡掉，报"only a succeeded or failed worker can release"。也就是说`worker-stop`收过的Worker根本轮不到它。

**Dispatch还挂着没settle的**走`worker-stop`，别用`terminal close`。`terminal close`只关终端，编排层那条Dispatch会永远挂着，Lead的`check --wait`一直等它。`worker-stop`把Dispatch标failed、Task标blocked、终端一起关掉。同样要先`terminal read`，它是直接kill。

> [!WARNING]
> **收Worker一律用不带`--tab`的`close`，并且不要拿`ptyKilled`当证据。**
>
> `--tab`关掉的不是一个终端，是整个tab容器，2026-08-04实测的代价有三层。同一个tab里的其它pane跟着一起没，包括不属于本次任务、正在跑的东西——那次连带关掉了用户一个无关的shell，事后才发现。这个worktree之后建的终端**拿不到tab**，`create`照样返回`surface: visible`，而任何`close`都报`tab_not_found`，直到界面里重新开一个。最后，这些终端不进`terminal list`，收尾核对看起来干干净净。
>
> `ptyKilled`那一层同样不可信。同一天实测三种进程，`close --tab`全部返回`ptyKilled: false`，而三个进程**全部已经死了**：普通`sleep`、用`trap "" HUP`忽略挂断信号的`sleep`、以及按本手册配方起的真Opus 5 Worker连同它的`caffeinate`子进程。这个字段报的不是事实。
>
> 更早一版手册写的是`--tab`只关容器、里面的东西还在跑，依据是2026-08-03的观察。那次的代价是十几个以为已经回收的Worker其实一直在跑，其中一个在测试结束几十分钟后还在往Run里发`worker_done`，被Orca以capability已撤销退回，噪音落到了用户的收件箱。事故是真的，但今天复现不出来：要么运行时在这两天里变了，要么当时的存活另有原因。**在重新复现之前，两种收法都不提供保证**，区别只是不带`--tab`那条的`ptyKilled: true`跟实测对得上。
>
> 所以判断收干净了没有，不看`ok`，也不能只看`ptyKilled`，要回头查终端本身：
>
> ```bash
> orca terminal close --terminal '<terminal_handle>' --json   # 一律不带 --tab
> orca terminal show  --terminal '<terminal_handle>' --json   # connected 必须是 false
> ```
>
> `tab_not_found`有两种成因，不要混。同一个tab里有多个pane时，关掉一个会连带关掉整个tab，之后对同tab其它handle的close报这个，那是**已经收掉了**，复查`terminal list`确认即可。而目标worktree在Orca界面里没有打开时，那里建的终端从来就没有tab，`create`照样返回`surface: visible`，`close --tab`报的也是这个——那是**从来没有过tab**。2026-08-04冒烟测试因为这条误报过两次FAIL。

一个worktree里起了多个Worker、任务整体结束时，可以按worktree批量停：

```bash
orca terminal stop --worktree 'path:/absolute/path/to/repo' --json
```

`terminal stop`会停掉该worktree下的全部Terminal，包括不属于本次任务的。用之前先`orca terminal list --worktree ... --json`看清楚里面有什么。

交付前确认没有本次任务遗留的Terminal。这是[完成标准](../SKILL.md)的一条。**光跑`terminal list`不够**，原因见下面三条坑里的第一条。

**三个命令回答的不是同一个问题。**

| 命令 | 回答的问题 | 真正说话的字段 |
|---|---|---|
| `terminal list` | 界面里现在挂着tab的终端有哪些 | 整个列表 |
| `terminal show --terminal <handle>` | 这个handle的记录还在不在 | `connected` |
| `terminal read --terminal <handle>` | 这个终端的缓冲里有什么 | `status` |

tab关掉之后`list`就不再列它，而`show`仍然返回`ok: true`。**只看`ok`会把它读成还活着**，真正说话的是`connected: false`；`read`同理，`ok: true`配的是`status: exited`。2026-08-04踩过一次，把一个已经收掉的终端读成了漏网的。

这条直接影响完成标准：一个tab已经关掉的Worker**不出现在`terminal list`里**，列表干净证明不了收干净。所以收尾要两步走，快照对比找出本次新增的，再对每个建过的handle跑`show`确认`connected: false`。

`terminal list`还有注册延迟。终端建出来之后几秒内可能查不到，收尾核对前先`sleep`一下，否则会把还没注册的读成已经收掉的。

**核对时按handle，不要按标题过滤。**`terminal create --title`设的名字只在返回值里，`terminal list`显示的是实际运行的命令；Claude Worker还会自己把标题改成任务描述。按标题找"我起的那些"会一个都找不到，看起来干干净净。

**但handle也会变。**同一个tab里关掉一个pane，幸存的pane会被重新分配handle，原来记的那个再去close就报`tab_not_found`——而终端还活着，只是换了身份。所以只按记下来的handle核对同样漏。

可靠的做法是**开工前先给worktree拍一张快照**，收尾时再拍一张，多出来的就是本次任务留下的，不管它现在叫什么handle：

```bash
orca terminal list --worktree 'path:/absolute/path/to/repo' --json
```

这三条坑合起来的代价：一次自称"零遗留"的收尾，实际留下了十几个还在跑的Worker，其中一个在收尾几十分钟后仍在往Run里发消息。

**快照法也有失效的一刻，2026-08-06实测。**收泳道1的Worker时`close`报`tab_not_found`，按handle`show`得`connected: false`，`terminal list --worktree`返回空——三条证据齐了，判定已收，还向用户汇报了"零遗留"。几小时后同一条worktree查询列出了它：**新handle、`connected: true`、Claude还停在`等Lead决定是否push`的recap上**，从头到尾没死过。

拼起来是上面第505行那个状态：那个worktree的tab被关掉之后，里面的pane既拿不到新tab、`close`必报`tab_not_found`、也**不进`terminal list`**。于是快照法看到的"没有新增"是假的——它根本列不出来。等界面里那个worktree重新有了tab，它又回到列表里，换了handle照常在跑。

所以三种核对方式在这个状态下同时失效：按旧handle`show`（handle已退休，报`connected: false`）、`close`回执（报`tab_not_found`）、worktree快照（列不出来）。

**能确定收干净的只有一条：`terminal list --worktree`列出来的那些，逐个用列表里当前的handle去`close`，直到这条查询返回`totalCount: 0`。**列表为空且此前刚成功close过，才算收干净；单独一次返回空不算，因为它跟"tab没了所以列不出来"长得一模一样。这也意味着**收尾要在Orca界面里那个worktree还开着tab的时候做**。

同一天还有一个连带结论：既然close报`tab_not_found`时终端可能根本没被碰过，那"我的close是不是连带关掉了别人的终端"这个怀疑，多数时候答案是没有——看起来消失的终端更可能只是掉出了列表。

## 跨仓库所有权

推荐一个Worker只写一个仓库：

```text
Opus 5 Lead（max）
├── Codex ERP Worker：只写erp-admin，spec闭合
├── Opus 5 Lighthouser Worker（high）：只写lighthouser，实现中还要定错误语义
└── Opus 5 Reviewer（high）：只读两个仓库并检查契约
```

两个实施位的模型是按各自Task的规格闭合度选出来的，不是固定搭配。同一轮里两个都闭合时，一个走Codex一个走Opus 4.8 `xhigh`，不全押一条路。

由Lead先定义接口，再允许两个实施Worker并行。接口还在变化时，先完成ERP契约Task，再派发Lighthouser接入Task。
