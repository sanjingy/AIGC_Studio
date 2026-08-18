---
name: orca-task-router
version: 0.17.0
author: Rubin Wang
license: MIT
description: >
  编排Orca Terminal Worker、Agent Teams队友和Codex：判复杂度、选模型档位、建Task、派发、验收、
  返工、回收终端。以下六种必须触发，用户没说"编排""选模型""开队友"也触发。
  一、跨多仓库或模块的业务目标，措辞如"你安排一下""这轮我要一次做完""你负责到底"。
  二、把任务移交给另一个agent，措辞如"交给另一个agent做""换个worktree做""handoff"，带模型名也是移交。
  三、已派出的Worker报完成、要返工、要收尾、要回收终端，或查它还在不在跑。
  四、问某任务用哪个模型、开几个Worker、能不能并行、要不要拆。
  五、要跑测试、翻日志、查运行时状态但不改代码，需决定派只读队友、调查型队友还是Terminal Worker。
  六、本会话即将创建任何子Agent、队友或Worker，无论用户是否在场、是否提过编排。
  不用于已由上级分配且边界明确的Worker任务本身、单文件小改、普通问答。
allowed-tools:
  - Read
  - Grep
  - Glob
  - Agent
  - TeamCreate
  - TeamDelete
  - TaskCreate
  - TaskUpdate
  - TaskList
  - TaskGet
  - SendMessage
  - Bash(orca status:*)
  - Bash(orca agent-context:*)
  - Bash(orca skills get:*)
  - Bash(orca repo list:*)
  - Bash(orca worktree list:*)
  - Bash(orca worktree create:*)
  - Bash(orca terminal list:*)
  - Bash(orca terminal show:*)
  - Bash(orca terminal create:*)
  - Bash(orca terminal send:*)
  - Bash(orca terminal read:*)
  - Bash(orca terminal wait:*)
  - Bash(orca terminal close:*)
  - Bash(orca terminal stop:*)
  - Bash(orca orchestration:*)
  - Bash(~/.claude/skills/orca-task-router/scripts/install-project-guard.sh:*)
  - Bash(python3 ~/.config/claude-cc/orca-codex-usage.py:*)
---

# Orca任务自动路由

Lead自己判断复杂度、依赖、Worker数量和模型。用户只描述目标，不要求用户逐个Task选模型或确认普通派发。

> [!IMPORTANT]
> 模型档位、任务映射、派发语义、升级门槛和启动命令的唯一权威源是[Session启动指南](references/session-launch-guide.md)。本文和其它reference出现冲突时，按指南执行。

## 先判断当前角色

收到顶层目标时，作为Lead运行本流程。收到Lead发来的明确任务时，作为Worker只完成分配范围，不再次组织团队。

满足任一条件时视为顶层目标：

- 用户描述最终业务结果，而不是单个文件修改。
- 任务跨仓库、跨模块或包含多个可独立实施部分。
- 用户要求统筹、并行、分配队友、全程推进或统一验收。
- 当前会话由`claude-cc-teams`启动，并且没有上级Agent给出的Task边界。

### 排除full handoff，这一步必须在动手之前

判定要排在Lead工作流第1步之前，因为工作流里的`run-create`和`task-create`都是写操作，建下去就已经踩了官方对full handoff的禁令，撤不回来。

用户说"交给另一个agent""handoff""handover""换个worktree做""让另一个agent接手"，并且**没有**要求监督、等结果、跟踪完成、协调DAG或者设决策点时，按full handoff处理：把prompt送达就停手。不建Run、不建Task行、不接管Dispatch、不回头读Worker输出、不进下面的Lead工作流。

送达用哪条命令见[Orca操作手册](references/orca-operations.md)"第零步"。**注意`terminal create`本身送不了prompt**，它只有`--worktree/--title/--command/--focus`四个参数；`worktree create --prompt`也必须配`--agent`才有收件人。写"用terminal create把prompt送达"等于什么都没送出去。

> [!WARNING]
> **用户指定模型或effort不构成监督请求。**"用Fable跑这个""开max档""换个模型试试"这类措辞只是在选档位，官方明写`gpt-5.5`、`high`、`xhigh`这类词不会把full handoff变成supervised。本Skill天生围绕模型选择，最容易在这里判错：把一句纯粹的移交请求，因为它带了模型名，误读成要Lead全程盯着。移交措辞加模型名，仍然是移交。

排除之后进Lead流程，就意味着走supervised。官方原文禁止在full handoff里用的是`task-create`、`dispatch --inject`、`check --wait`三条；`run-create`和`worker-start`是本Skill按同样理由加上的推论，不是官方原句。本Skill的Lead按"完成标准"对每个Worker产出负验收责任，验收必须收报和跟踪状态，这就是supervised的定义。依据是Lead的验收义务，不是"用户说过supervise"——上面四条顶层判定里只有一条是显式监督请求，不能拿它替所有场景背书。

## Lead工作流

1. 读取当前目录及相关仓库的`CLAUDE.md`、`AGENTS.md`、`CONTEXT.md`和计划文档。跨仓库时逐个读取，不假设父目录已经加载子仓库上下文。
2. 读取[复杂度与模型路由](references/routing-policy.md)，在内部完成复杂度判断。不要向用户展示评分或要求用户选择模型，除非用户主动询问。
3. 建立Task DAG。每个Task写明仓库、允许修改范围、依赖、输入契约、验收条件和禁止事项。
4. 检查Orca当前Repo、Worktree和Terminal。默认复用已有feature分支和当前工作区，不因为使用多Agent就创建新分支。**Worker的Terminal默认起在Lead自己所在的worktree**，`terminal create --worktree 'path:<绝对路径>'`，后面`worker-start --worktree`填同一个值。只有同一仓库要真正并行写入、或者跨仓库时才另开worktree，那时选择器必须写成完整的`id:<repo-id>::<path>`。Lead的worktree里通常已经有别的终端，建之前先`orca terminal list --worktree 'path:<绝对路径>' --json`拍一张快照，收尾时靠它认哪些是本次建的，见[Orca操作手册](references/orca-operations.md)"Worker收尾"。
5. 按任务语义选择Fable、Opus或Codex档位，并决定串行或并行。不同Worker不得同时拥有同一文件或同一数据契约。
6. 先`orca orchestration run-current`看当前终端绑没绑Run，没绑就`run-create`建一条，本次任务的Task和消息都挂在它下面。实施型Worker自己用`terminal create --command`起，`--command`必须使用[Session启动指南](references/session-launch-guide.md)的档位命令，模型按规格闭合度选：闭合的在Codex和Opus 4.8 `xhigh`之间轮，不闭合的用Opus 5 `high`。Claude显式1M context，Codex先跑`orca-codex-usage.py`按周额度水位在Sol、Terra、Luna里定档、三档都不开Fast；等TUI就绪后用`worker-start --task <id> --worktree <worktree> --terminal <handle>`交给官方接管，`--worktree`每次都要写，漏了会因为跟Lead所在仓库不符而被拒。这条路在旧版Orca上曾全面失效，`worker-start`一律报`selector_not_found`；事实块记的版本上已恢复，2026-08-11真实派发实测走通。再撞见时退`orca orchestration dispatch --task <id> --to <handle> --run <run_id> --inject`，代价和探针矩阵见[Orca操作手册](references/orca-operations.md)"worker-start故障存档"。判据是失败之后需不需要一条可审计的返工血缘。需要就走Orca Terminal形态，能整体重派就走Agent Teams队友，队友不占Terminal。两种形态都接得上中断，判据问的是Task状态、重试计数和官方熔断这本账，不是谁能接着跑。三种形态的分工和预装角色见[Orca操作手册](references/orca-operations.md)"Claude Worker的三种形态"。操作前读取该手册。

   自己起终端再交接管，是因为官方的`worker-start --agent`传不进模型参数，而`--terminal`那条支路能接住已经带参数起好的终端，同时拿到官方的`--retry-of`重试和`worker-show`/`worker-read`/`worker-stop`/`worker-abandon`四条故障处置。参数不丢两侧都实测过：2026-08-03验Codex走的`worker-start --terminal`，2026-08-06验Claude Opus 5 `high`走的是退路`dispatch --inject`——两种接管方式下进程argv都原样，状态栏仍报`Opus 5 (1M context)·high`，`worker_done`正常结案。所以"自己起终端"这个做法本身是成立的；`worker-start`接管支路在旧版上塌过一轮，事实块记的版本上已恢复，2026-08-11实测Opus 5 Worker经它接管、`worker_done`正常结案。

   **任务书第一行必须让Worker`touch`一个ack文件，Lead轮询这个文件确认送达。**这是硬要求。`worker-start`返回的`ok: true`、`state: ready`、`stage: input_accepted`只证明输入被那个终端收下了，证明不了Agent真的跑起来——2026-08-11实测一次进程假死，这三个字段全绿、终端里前言和TASK块齐全，而进程CPU掉到0.0%、30分钟零输出、`last_heartbeat_at`始终是`None`、ack一次都没落盘；换终端重派20秒就ack。判假死要进程CPU、终端最后输出、`last_heartbeat_at`三条交叉，只看一条会判错，详见[Orca操作手册](references/orca-operations.md)"ack文件是唯一的送达证据"。

   编排协议本身以Orca自带的说明为准，派发前跑一次`orca skills get orchestration`。本Skill只定义派谁、用什么档位、怎么把参数塞进启动命令，不复制官方的协议细节——复制过一次，官方废掉coordinator loop之后我们的文档烂了半年。
7. Lead持续检查进展、处理依赖和失败升级。普通派发、重试和模型切换不询问用户。Worker用`ask`提问时Lead必须`reply`，不回它会一直阻塞到超时。Worker回报上下文触顶交接时，按[Orca操作手册](references/orca-operations.md)"上下文交接"验收前半段、起新Worker接手，不要让Worker自己开新会话。
8. Lead逐项复核代码、测试和契约，再汇总结果。Worker的完成声明不是验收证据。
9. 验收**不通过**时，终端保持不关，Lead建一条写明不合格项的新Task，用`worker-start --task <新task> --worktree <worktree> --terminal <原handle>`派回同一个终端。Worker报完成时Dispatch和Task都已结案，而`--retry-of`要求worker处于failed/stopped/abandoned**且**Task处于failed/blocked，两个条件都不满足所以会被拒；把Task改回`ready`只会更远离可重试区间。只把原Task重开再派一次也不行，注入的还是原spec，Worker会原样再做一遍。终端不关，Worker的上下文就还在，这跟关掉重起是两回事：重起会丢掉全部上下文，新Worker要从头理解任务并重踩已经排除过的坑。
10. 只有验收通过、交接完成或任务被放弃之后，Lead才回收Terminal。回收由Lead发起，Worker不自行退出——Worker无法预知验收结果，自己退了就断了返工的路。回收步骤见[Orca操作手册](references/orca-operations.md)"Worker收尾"。

## Lead职责

- Lead固定跑Opus 5的`max`档加1M上下文，持有业务方向、架构边界、Task DAG和最终验收权。`max`只属于Lead本体，派出去的Claude Worker一律`high`。
- Lead优先协调和判断，不长期占用自己做明确的批量编码。
- 判断型工作用Opus 5 `high`，由Lead本人做或派Agent Teams队友：只读Review派`orca-opus-reviewer`，需要跑命令验证的调查派`orca-opus-investigator`。实施型工作按规格闭合度派：spec把输入输出、边界、错误语义、验收全写死的算闭合，在Codex和Opus 4.8 `xhigh`之间轮着派，不全押一条路，仓库级批量改优先Codex；实现中还要做设计决策的算不闭合，派Opus 5 `high`的Terminal Worker。能用Opus 5干的优先Opus 5。Codex档位按周额度水位选，见[Session启动指南](references/session-launch-guide.md)"Codex按额度水位选档"。
- Fable有两条入口，见[Session启动指南](references/session-launch-guide.md)"Fable的两条入口"。深度只读Review与调研：要通读一个模块以上并自己决定往哪查深，派`orca-fable-architect`，入口是范围条件不是难度形容词。长期自主调查：用"Fable闸门"的四条判据，满足两条或以上才派，只满足一条先补上下文用Opus 5重试。任务同时落在架构决策、跨仓库契约、权限模型或数据迁移设计上时，按指南的拆段规则处理：调查段给Fable，决策段留Lead。Lead可以派出能力比自己强的Worker，派出去不转移验收权。
- Opus 4.8不接决策权。`xhigh`接规格闭合的实施，`max`只做灾备，在Opus 5不可用时顶上。
- 在Codex和Opus 5之间判断不清时选Opus 5。这条不适用于Fable，它只由两条入口决定。档位红线和升级门槛以[Session启动指南](references/session-launch-guide.md)为准。

## 用Agent工具派队友

Agent工具是Lead自己创建队友的那条路。它和Terminal Worker不共用一套参数，档位怎么表达也不一样，单独按这节走。

**`model`参数一律不传，没有例外。** 工具schema只收`sonnet`、`opus`、`haiku`、`fable`四个别名，团队档位要的完整模型名会被schema直接拒掉并报`InputValidationError`，连hook都到不了。别名又带不上`[1m]`，违反"所有子session显式1M上下文"。不传的时候子Agent继承本会话的`claude-opus-5[1m]`，含1M上下文，正好落在要的档位上。

别名还会漂。两个相邻小版本之间，`opus`指向的模型已经换过一次，逐值实测记录见[Session启动指南](references/session-launch-guide.md)"派队友时不要碰Agent工具的`model`参数"。继承路径不漂，所以档位靠继承和队友定义表达，不靠这个参数。

要非默认档位就换`subagent_type`，档位写在队友定义的frontmatter里，那里吃`[1m]`后缀：

| 要什么 | subagent_type |
|---|---|
| 只读Review、通读模块、契约核对 | `orca-opus-reviewer` |
| 跑测试、翻日志、执行构建、查运行时状态 | `orca-opus-investigator` |
| 过了Fable两条入口之一的深度调研或长期调查 | `orca-fable-architect` |

**裸`general-purpose`不接改文件的活。** 它继承全套工具，能写能删，但它没有Task状态流转、没有重试计数、没有官方熔断。要改文件而且失败之后需要一条可审计的返工血缘，开Terminal Worker，模型按规格闭合度选，别图省事走Agent工具。判据跟第6步那条一样，问的是账不是能力。

`orca-model-guard.py`挂在`PreToolUse`的`Agent`上，会拦下带`model`取值的调用并把整改理由回灌，显式`null`视同不传。被拦了照理由改，不要绕：它拦的是schema和档位体系之间的结构性冲突，绕过去等于自己挑一个schema允许但档位错误的值。

守卫靠matcher匹配工具名生效，而这个工具已经从`Task`改名成`Agent`一次。**Claude Code升过版之后手工验一遍**：派一个带`model=sonnet`的调用，确认被拒且理由回灌。改名或matcher失配时守卫会一声不响地全程不触发，`check-consistency.sh`grep的是我们自己写进安装器的字符串，证明不了这件事。

## 并发边界

- 默认最多同时运行3个Worker；只有任务完全独立时才增加到4个。
- 同一文件、同一数据库迁移、同一接口契约或同一权限定义必须串行。
- 跨仓库但由稳定契约隔开的任务可以并行。
- 小任务直接由一个Agent完成，不为了展示分屏而创建队友。

## 自动升级

- 实施型Worker第一次失败时，Lead补充上下文和验收条件后重试。
- 连续两次失败、发现需求歧义或触及架构边界时，交回Lead重新判断派谁，不直接换模型继续跑。Codex失败不换Opus 4.8，那是平移不是升级。两次这个数字是为了卡在官方熔断之前：同一Task连续失败3次，Orca会自己把dispatch熔断并把Task标成failed，到那一步Lead就失去了主动重新规划的机会。3是官方值，随官方变，复核时以`orca skills get orchestration`为准；2是我们自己留的余量。
- Lead产出明确实施方案后，把编码重新按规格闭合度派出去，Codex档位按当时的水位重新读一次，不要沿用上一个Worker的档。
- 任何升级路径的终点都是Lead。Lead不把任务升级出去然后失去验收权。
- Worker阻塞时先由Lead读取证据并修正Task，不把普通技术判断转给用户。

## 用户确认边界

只在以下情况暂停并询问用户：

- 产品方向存在多个会显著改变结果的选择，并且项目资料无法判定。
- 技术死路：Lead按[Session启动指南](references/session-launch-guide.md)"Lead自己卡住时怎么办"走完三个出口仍无法推进。提问时附已排除的假设、已有证据和需要用户提供什么。
- 删除数据、覆盖不可恢复状态、生产发布或其它不可逆操作。
- 需要新增外部权限、账号、付款或第三方协调。
- commit、push、创建PR或对外发送内容，除非用户已经明确授权。

不要为创建普通Task、选择模型、启动队友、读取代码、运行测试或失败升级要求确认。

## 完成标准

Lead交付前确认：

- 每个Task都有实现证据和验收结果。
- 跨仓库接口字段、类型、权限和错误语义一致。
- 没有两个Worker遗留冲突修改。
- 文档与最终代码一致。
- 本次建过的每个Worker Terminal都已回收：`orca terminal list`跟开工前的快照比没有新增，**并且**逐个`orca terminal show`确认`connected`是`false`。只看列表不够，tab已经关掉的终端根本不进列表。收Worker一律用不带`--tab`的`close`，理由见[Orca操作手册](references/orca-operations.md)"Worker收尾"。
- 未经授权没有commit或push。

维护或审查本Skill时读取[设计依据](references/design-rationale.md)。安装到新机器时使用`scripts/install.sh`，先执行`--dry-run`，再执行`--apply`和`--verify`。

改动档位、模型、阈值或版本号时，改[Session启动指南](references/session-launch-guide.md)顶部的`canonical-facts`块，然后跑`scripts/check-consistency.sh`。它会核对全Skill的重述是否跟事实块一致，并拦截已废弃的说法。加`--live`还会比对本机实际安装的CLI版本，并逐条探测Skill依赖的每个官方命令还在不在、有没有被标Retired。`scripts/verify-fixtures.sh`在跑安装器测试之外，还会注入多种真实发生过的漂移，确认校验脚本本身没有失效。

`scripts/smoke-orchestration.sh`验的是另一类东西：前两个脚本只能证明命令拼写正确和文档内部自洽，证明不了命令做的事跟手册写的一样。这个Skill栽的每一跤都栽在后者——`--retry-of`被拒、重开的Task照发原spec、`worker-start`拒收跨worktree的终端、`worker-stop`比文档写的多干两件事，四条全是照着help文本推出来的，全是错的。冒烟测试会起一个真Agent把状态机走一遍，逐条断言手册的说法。

**满足下面任一条时跑它：**

- `canonical-facts`里的`ORCA_VERSION`变了。手册全部结论都建立在那个版本的实现上，版本一动，静态检查照样全绿而行为描述可能已经失真。
- 改动新增或修改了一条**行为断言**，即"某命令在某状态下会返回什么、会把状态改成什么"。判据、角色分工、形态分类和派活边界不算，那些是我们自己的约定，不依赖Orca实现。
- 距上次跑超过一个月，当巡检。

原先写的是"改动手册里任何一条行为描述就跑"，太宽：调整分类和判据的改动也被扫进去，跑一次几分钟加额度、结果必然全绿。跑几次没意义的之后，人就开始把它当成税而不是哨兵，真该跑的那次反而会被跳过。

它慢、要Orca在跑、会花额度。但Orca升级那天，它是唯一会喊出来的东西。
