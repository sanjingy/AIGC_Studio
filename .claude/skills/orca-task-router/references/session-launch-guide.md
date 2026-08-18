# ClaudeTeamAgents Session启动与模型选择指南

本文是Orca任务路由的唯一权威源。档位定义、任务映射、派发语义、升级门槛和启动命令都以本文为准。Skill内其它文档只做语义补充，出现冲突时按本文执行。

这里的模型排序是Rubin基于当前实际使用效果制定的团队内部规则，不代表通用模型评测结论。

## 目录

不必通读。按当前要做的判断跳到对应小节。

| 当前判断 | 去哪节 |
|---|---|
| 为什么Lead是Opus 5而不是能力第一的Fable | [两张表：能力排序不等于使用顺位](#两张表能力排序不等于使用顺位) |
| 有哪些不可越过的档位红线 | [硬约束](#硬约束) |
| 要一条可直接复制的启动命令 | [直接复制的启动命令](#直接复制的启动命令) |
| 这个任务该派哪个档位 | [按任务选模型](#按任务选模型)、[Agent执行前的判断顺序](#agent执行前的判断顺序) |
| 实施型该派Claude还是Codex | [按任务选模型](#按任务选模型)开头的规格闭合度判据 |
| 该不该派Fable | [Fable的两条入口](#fable的两条入口)、[Fable闸门：什么算"长期自主调查"](#fable闸门什么算长期自主调查) |
| 任务既过了闸门又属于架构决策 | [闸门和"决策权不外派"重叠时怎么裁](#闸门和决策权不外派重叠时怎么裁) |
| 派只读队友、调查型队友还是Terminal Worker | [Agent Teams队友的context](#agent-teams队友的context) |
| Worker反复失败要不要升档 | [派发语义与升级](#派发语义与升级)、[Fable xhigh硬门槛](#fable-xhigh硬门槛)、[Fable max硬门槛](#fable-max硬门槛) |
| Lead自己卡住了 | [Lead自己卡住时怎么办](#lead自己卡住时怎么办) |
| 确认起好的session档位对不对 | [如何确认Session开对了](#如何确认session开对了) |
| 某个启动参数是干什么的 | [参数说明](#参数说明) |

适用环境：

| 工具 | 本机版本 | 本文使用的模型 | 核验日期 |
|---|---:|---|---|
| Claude Code | `2.1.226` | Claude Opus 5、Claude Fable 5、Claude Opus 4.8 | 2026-08-11 |
| Codex CLI | `0.147.0` | GPT-5.6 Sol | 2026-08-11 |
| Orca | `1.4.179` | 不适用 | 2026-08-11 |

三个版本号的来源不一样，复核时别用错命令。Claude Code和Codex CLI读`claude --version`和`codex --version`。**Orca没有可用的`--version`**：本机跑它只打印一行`orca`加usage，不带版本号，准确值在`orca status --json`的`result.runtime.appVersion`里，代价是要求Orca桌面端正在运行。`scripts/check-consistency.sh --live`就是按这三条取的值。

<!-- consistency:allow-start -->
2026-08-11这轮三侧一起重核，下面三个旧版本号是漂移记录，不是现行事实。Claude Code从2.1.222升到2.1.226，用`claude -p --output-format json`的`modelUsage.contextWindow`重跑`[1m]`后缀探针，结论没变。Codex CLI从0.146.0升到0.147.0，冒烟测试起了一个真Sol session读它自己的状态栏，档位和上下文上限都没变。Orca从1.4.177升到1.4.179，冒烟测试把整套状态机重走一遍，详见[Orca操作手册](orca-operations.md)。这轮没有重测的是别名解析去向、Agent工具`model`参数的取值范围和队友的模型继承，那三条沿用2026-08-05的核验。
<!-- consistency:allow-end -->

本文里所有跟context上限、模型别名解析、effort档位有关的结论都是版本条件性的，随CLI版本会变。核验日期距今超过一个月时，重跑一遍下方"如何确认Session开对了"的探针再用。

下面这段是机器可读的事实源。`scripts/check-consistency.sh`读它去核对全仓库，改档位或模型时改这里，其余文件的重述会被校验脚本比对。不要手工让两边不一致。

<!-- canonical-facts
LEAD_MODEL=claude-opus-5[1m]
LEAD_EFFORT=max
WORKER_MODEL=claude-opus-5[1m]
WORKER_EFFORT=high
FABLE_MODEL=claude-fable-5[1m]
FABLE_DEFAULT_EFFORT=high
BACKUP_MODEL=claude-opus-4-8[1m]
IMPL_BACKUP_EFFORT=xhigh
BACKUP_EFFORT=max
CODEX_MODEL=gpt-5.6-sol
CODEX_EFFORT=ultra
CODEX_MODEL_DAILY=gpt-5.6-terra
CODEX_EFFORT_DAILY=max
CODEX_MODEL_THRIFT=gpt-5.6-luna
CODEX_EFFORT_THRIFT=max
CODEX_CONTEXT_WINDOW=272000
CODEX_TERRA_AT=60
CODEX_LUNA_AT=85
GATE_THRESHOLD=两条或以上
HANDOFF_THRESHOLD_TOKENS=300000
CLAUDE_CODE_VERSION=2.1.226
CODEX_CLI_VERSION=0.147.0
ORCA_VERSION=1.4.179
VERIFIED_ON=2026-08-11
-->

<!-- Deprecated claims that must never reappear anywhere in this Skill.
BANNED=Fable是Lead
BANNED=主Agent固定使用Fable
BANNED=Fable原生
BANNED=原生使用100万
BANNED=Codex XHigh
BANNED=Codex xhigh
BANNED=两条以上才
BANNED=侧向派
BANNED=没有明确保证生效
BANNED=coordinator-start
BANNED=coordinator-stop
BANNED=--max-concurrent
BANNED=连tab一起收
BANNED=队友没有Bash
BANNED=派Codex Worker或Lead自己跑
BANNED=从中断点继续
BANNED=实施型工作用Codex
BANNED=Opus 5额度吃紧
BANNED=Opus 4.8不接实施活
-->


`1M context`指100万token上下文。不要写成`100M context`，后者表示1亿token。

<!-- consistency:allow-start -->
> [!IMPORTANT]
> Claude Code `2.1.226`的内置模型表里没有Opus 5，它按自定义模型处理，默认上限200k。必须写`claude-opus-5[1m]`才拿得到100万上下文。`claude-opus-5`不加后缀开出来的session，`/context`显示的是200k。服务端`/v1/models`报的`max_input_tokens`是100万，这个上限是客户端加的，不要用服务端数据推断。
>
> 用`claude -p --output-format json`的`modelUsage.contextWindow`跑过三轮：2026-07-31首测，2026-08-05在2.1.222上重跑，2026-08-11在2.1.226上再跑，三次都是`claude-opus-5`拿200000、`claude-opus-5[1m]`拿1000000。跨了两个小版本结论没变。查资料时会反复遇到"Opus 5原生1M、不需要后缀"这个说法，它是错的，探针每次都推翻它，别照抄。这段引用错误说法是为了让人认得出它，不要因为它出现在本文里就把它当成本文的主张删掉。
<!-- consistency:allow-end -->

## 两张表：能力排序不等于使用顺位

<!-- consistency:allow-start -->
团队用两张表描述模型，用途不同，不要混用。原先只有一张表，同时承担"谁更强"和"该派谁"两个问题，结果Fable在能力上排第一被读成了Fable是Lead，规则从此分裂。
<!-- consistency:allow-end -->

### 表A：能力排序

回答"谁更强"，只用于决定卡住之后往哪升。

| 顺位 | 档位 | 工作模式 | 擅长 |
|---:|---|---|---|
| 1 | Fable 5，`high` | 长跑型，自主修正计划 | 跨工作时段调查、根因可能同时落在代码配置基建、深度只读调研、无人值守 |
| 2 | Opus 5，`max`加1M | 深推理型，单次质量最高 | 架构决策、跨仓库契约、最终验收、Task DAG |
| 3 | Opus 5，`high`加1M | 深推理型，吞吐优先 | 判断型Worker、带设计决策的实施 |
| 4 | Opus 4.8 `xhigh`/`max`加1M ≈ Codex三档 | 执行型 | 规格闭合的实施、重构、批量修改、补测试；Codex Sol的`ultra`可自动委派子任务 |

第4位是并列，带来一条升级修正：Codex实施失败之后升到Opus 4.8是平移，没有收益，正确去向是Opus 5。

### 表B：使用顺位

回答"默认派谁"，日常路由看这张。

| 使用频率 | 档位 | 场景 |
|---|---|---|
| 最高 | Opus 5，`max` | 只有Lead本体 |
| 最高 | Opus 5，`high` | 全部判断型Worker，以及规格不闭合的实施型Worker |
| 次之并列 | Codex三档，按额度水位选 | 规格闭合的实施型工作；仓库级批量改优先它，Sol的自动委派是它真正的护城河 |
| 次之并列 | Opus 4.8，`xhigh` | 规格闭合实施型的分摊腿，跟Codex轮着用 |
| 少而准 | Fable 5，`high` | 深度只读Review与调研，以及过了下方"Fable闸门"的长期自主调查 |
| 备用 | Opus 4.8，`max` | 灾备，只在Opus 5不可用时 |

### 为什么两张表顺序不同

Lead位子要的是稳定协调和验收，不是最强推理。Lead持有业务目标、Task DAG、跨仓库契约和最终验收权，这些是记账和判断，不是攻坚。Lead也是用户实际敲字交互的那个session，需要随时应答，而Fable的优势在无人值守的长跑，放在这个位子上是浪费。

由此得到一条反直觉但正确的结论：**Lead可以派比自己更强的Worker**。Lead不需要自己是能力第一，它只需要认得出什么时候该请Fable，并且全程保留验收权。

Opus 5同时能力靠前、使用最高，因为它便宜且能力强，适合承担绝大部分工作量：全部判断型工作，加上规格不闭合的实施。规格闭合的实施在Codex和Opus 4.8 `xhigh`之间轮着派，不全押一条路——不是为了省谁的额度，是一条从不被行使的派发路径会静默腐坏，出问题的那天才发现它早就不通。仓库级批量改是例外，优先Codex，Sol的`ultra`能把工作切成互不覆盖的模块自行委派和汇总，这个能力Claude侧没有。

## 硬约束

- 所有Claude子session显式配置1M context，模型名必须带`[1m]`。Codex不适用：5.6系列的上限就是272000，配更大的值会被截断。
- Opus 5的`max`只属于Lead本体。派出去的Opus 5 Worker一律`high`，判断型和实施型都是。理由是吞吐和过度思考，不是省额度，见下方"为什么Worker是high"。
- 实施型Worker按规格闭合度选档：闭合的在Codex三档和Opus 4.8 `xhigh`之间轮着派，不闭合的用Opus 5 `high`。同一批Task不全押同一档。
- Codex按额度水位在Sol、Terra、Luna三档之间选，effort钉在各档最深一档，三档都不开Fast。降档换模型不换推理强度，理由见下方"Codex三档"。`xhigh`及以下的推理档仍然不使用。
- Fable默认`high`。`xhigh`和`max`需要满足本文的硬门槛才能开，不写进常规模板。
- 在Codex和Opus 5之间判断不清时选Opus 5，不从低档试错再用失败证明任务复杂。这条不适用于Fable：Fable由下方的两条入口决定，判断不清不构成派Fable的理由。
- Opus 4.8不接决策权，那是Lead的位置。`xhigh`接规格闭合的实施，`max`只做灾备。

## 直接复制的启动命令

按使用频率排列。所有命令都以全权限模式启动，只能在可信项目目录或外部隔离环境中运行。

### Opus 5 `max`：只有Lead本体

```bash
claude --model 'claude-opus-5[1m]' --effort max --dangerously-skip-permissions
```

使用完整模型名锁定Opus 5，不要改成`opus`：别名指向哪个模型会跨版本漂移，漂移实测见下方"所有Claude档位统一写完整模型名加`[1m]`"那条。理由是它会变，不是它当前指向谁——按当前指向判断安全性，下次升级就失效。`[1m]`显式要求100万token上下文，Opus 5不加这个后缀会被客户端压到200k。单引号用于防止zsh把方括号当成通配表达式。

### Opus 5 `high`：判断型Worker与规格不闭合的实施型Worker

```bash
claude --model 'claude-opus-5[1m]' --effort high --dangerously-skip-permissions
```

团队主流Worker档。判断型工作通常走Agent Teams队友，档位写在队友定义的frontmatter里；这条命令用于Terminal形态——实现过程中还要做设计决策的实施型Worker，用`terminal create --command`起，再交给`worker-start --terminal`接管，拿Task状态和返工血缘。

#### 为什么Worker是`high`而Lead是`max`

省额度不是理由，也省不到：effort影响的`reasoning_output_tokens`在总token的0.4%里面，这个数字在下方Codex那节算过。真正的理由有两条。`max`更慢，Worker做的是有验收标准的执行，要的是吞吐；`max`在长任务里容易过度思考和收益递减，这个现象在下方Fable `max`硬门槛那节记过。Lead做的是一次性高杠杆判断，值得慢。Worker卡住的正确动作是交回Lead，不是原地升`max`。

### Opus 4.8 `xhigh`：规格闭合实施的分摊档

```bash
claude --model 'claude-opus-4-8[1m]' --effort xhigh --dangerously-skip-permissions
```

跟Codex三档轮着接规格闭合的实施型工作，同样走Terminal形态。它存在的理由是不让闭合型实施全押在Codex一条路上：从不被行使的派发路径会静默腐坏。仓库级批量改不轮，优先Codex Sol，自动委派在那类任务上比推理深度值钱。

Lead本体不手敲这条命令，由`claude-cc-teams`按受管配置启动，模型和档位各有一个文件锁定：

| 配置文件 | 内容 | 谁读它 |
|---|---|---|
| `~/.config/claude-cc/orca-lead-model` | `claude-opus-5[1m]` | `claude-cc-teams`传给`--model` |
| `~/.config/claude-cc/orca-lead-effort` | `max` | `claude-cc-teams`传给`--effort` |

两个文件都由本Skill的`scripts/install.sh`铺设。`claude-cc-teams`拒绝从命令行覆盖`--model`和`--effort`，改档位要改配置文件再重启Lead。effort文件缺失时回退到`max`，取值只接受`low`、`medium`、`high`、`xhigh`、`max`，写错会拒绝启动而不是静默降级。

### Codex三档：实施主力

三条命令按周额度水位选，判据和阈值见下方"Codex按额度水位选档"。都不开Fast。

```bash
# 主力档，水位低于60%
codex --model gpt-5.6-sol -c model_context_window=272000 -c 'model_reasoning_effort="ultra"' --dangerously-bypass-approvals-and-sandbox

# 日常档，水位60%到85%
codex --model gpt-5.6-terra -c model_context_window=272000 -c 'model_reasoning_effort="max"' --dangerously-bypass-approvals-and-sandbox

# 省额度档，水位高于85%
codex --model gpt-5.6-luna -c model_context_window=272000 -c 'model_reasoning_effort="max"' --dangerously-bypass-approvals-and-sandbox
```

**降档换的是模型，不是推理强度。**三档的effort都钉在各自能达到的最深一档：Sol和Terra支持到`ultra`，Luna最高是`max`。Sol用`ultra`是因为大型任务用得上它的自动委派；Terra和Luna用`max`，推理深度跟`ultra`相同，只是不自动派子任务，省掉委派带来的额外消耗。

之所以不靠降effort省额度，是因为它省不到：2026-08-05统计本机08-05全部大Session，input占总token 99.6%，output只占0.4%，而effort影响的`reasoning_output_tokens`在那0.4%里面。消耗来自每个turn把整段context重送一遍，单个Session跑了294个turn、累计3800万token。真正的杠杆是turn数、并行Worker数和模型单价，不是推理档位。

Fast三档都不开。本机模型目录把它标为`1.5x speed, increased usage`，它乘在全部token上，是纯粹的速度换额度。

> [!IMPORTANT]
> `model_context_window`写272000是模型实际上限，不是我们选的值。`codex debug models`报`gpt-5.6-sol`、`terra`、`luna`的`max_context_window`都是272000、`effective_context_window_percent`是95，实际可用258400，跟Session记录里的数字逐位对上。本文早期版本写1000000并声称"100万token配置用于长Session"，那是无效参数，被模型上限直接截断，据此推出的"Codex装得下整个仓库"一类结论都不成立。整个5.6系列都没有1M，`gpt-5.4`才有。

### Codex按额度水位选档

派Codex Worker之前跑一次：

```bash
python3 ~/.config/claude-cc/orca-codex-usage.py
```

它读`~/.codex/sessions`里最新Session的`rate_limits.primary.used_percent`，直接给出该用哪档。Codex没有`usage`子命令，但TUI状态栏那个百分比每个turn都会写进rollout日志，所以读文件就够，不需要有终端在跑。周窗口是`window_minutes: 10080`。

| 水位 | 默认实施档 | 越级条件 |
|---|---|---|
| 低于60% | Sol `ultra` | 无，这是常态 |
| 60%到85% | Terra `max` | 大型重构、跨模块实现可以用Sol，在Task里写明理由 |
| 高于85% | Luna `max` | 用Sol或Terra都要写明理由，并先问Lead能不能等重置 |

阈值按周额度线性推：7天100%，每天约14%，60%落在第4天，留40%给剩下3天。两个阈值可以用环境变量`ORCA_CODEX_TERRA_AT`和`ORCA_CODEX_LUNA_AT`临时调，长期要改就改本文顶部的事实块。

**水位低时不要为了省而降档。**这套判据存在的唯一理由是额度不够，前提不成立时降档只是白白牺牲质量。原先Codex收敛成一档的核心论证是"保留两档会让Lead每次派发多一次无谓判断，而'边界清不清楚'这个触发条件本身不可靠、判错要重跑"，那个论证今天依然成立，所以判断成本只在水位真的紧的时候才付。

派发之后水位跨过阈值，不要中途换掉正在跑的Worker。上下文重建的代价通常高于省下的额度，等这个Task验收完再按新水位派下一个。

### Fable 5默认档：深度只读调研与长期自主调查

```bash
claude --model 'claude-fable-5[1m]' --effort high --dangerously-skip-permissions
```

Fable有两条入口，见下方"Fable的两条入口"：深度只读Review与调研走`orca-fable-architect`队友形态，长期自主调查按闸门判。它适合超过单次工作时段的任务，也适合一开始没有明确修复方案、需要自行调查和验证的工作。`high`已经能发挥Fable的长期自主能力。

<!-- consistency:allow-start -->
> [!IMPORTANT]
> Fable和Opus一样，不加`[1m]`就是200k。2026-07-31实测：`--model fable`和`--model claude-fable-5`的`contextWindow`都是200000，`--model 'fable[1m]'`和`--model 'claude-fable-5[1m]'`才是1000000。本文早期版本写过"Fable原生1M不需要后缀"，那句话是错的，已按实测更正。
>
> 所有Claude档位统一写完整模型名加`[1m]`，不用`fable`、`opus`这类别名。别名有解析漂移风险，而且这个风险已经兑现过：`opus`在Claude Code 2.1.217里解析到Opus 4.8，2026-08-05在2.1.222上重测解析到`claude-opus-5[1m]`。同一个别名跨一个小版本换了模型，这正是不能拿它表达档位的理由。别名指向哪儿由客户端版本和网关下发的模型表共同决定，两边都会在我们不知情的时候变。
<!-- consistency:allow-end -->

### Opus 4.8 `max`：灾备档

```bash
claude --model 'claude-opus-4-8[1m]' --effort max --dangerously-skip-permissions
```

只在Opus 5不可用时使用：网关故障、模型下线、账号异常这类让Opus 5起不来的情况。它顶替的是Opus 5的位置，判断型和不闭合实施都接，但不接决策权——Opus 5恢复后决策补做。早期版本给这一档写过按额度触发的条件，已废弃：额度不再是本Skill的路由输入，Codex水位除外。

### Fable升级档

只有满足下文硬门槛时才允许开：

```bash
claude --model 'claude-fable-5[1m]' --effort xhigh --dangerously-skip-permissions
claude --model 'claude-fable-5[1m]' --effort max --dangerously-skip-permissions
```

不得把这两条写进常规Agent启动模板。

> [!WARNING]
> `--dangerously-skip-permissions`会跳过Claude Code的权限确认。`--dangerously-bypass-approvals-and-sandbox`会同时关闭Codex审批和沙箱。两个参数都允许Agent直接执行命令和修改文件。目录不可信、代码来源不明或机器上存在重要未隔离数据时，不得使用这些命令。

## 按任务选模型

先问一个问题：**规格闭合了没有**。判据是剩下的工作是不是照着写——输入输出、边界、错误语义、验收全部写死，剩下是转录，就是闭合；实现过程中还要做设计决策，就是不闭合。这件事Lead在派发前就知道答案，因为spec是Lead自己写的。

一句话版：能用Opus 5干的优先Opus 5；闭合的活在Codex和Opus 4.8 `xhigh`之间轮着派，不全押一条路。

| 任务情况 | 使用档位 | 判断依据 |
|---|---|---|
| 深度Code Review、方案调研，要通读一个模块以上并自己决定往哪查深 | Fable 5，`high` | 深度只读道，见下方"Fable的两条入口" |
| 根因候选跨两个以上层面，且无法预先排除到一个层面 | Fable 5，`high` | 需要自主修正计划，且必须同时满足下方闸门的两条 |
| 只能说清最终结果，给不出可信执行步骤 | Fable 5，`high` | 步骤要Agent自己长出来，同样受闸门约束 |
| 中途持续冒出新信息，证据互相冲突 | Fable 5，`high` | 长跑型的主场，同样受下方闸门约束 |
| 架构决策、跨仓库契约、权限模型、数据迁移设计 | Opus 5，`max`，由Lead本人做 | 决策权不外派 |
| 关键Review、方案比较、疑难Bug根因，范围可控 | Opus 5，`high` | 判断型Worker档，深推理但边界可控 |
| 规格不闭合的实现：实现中还要做设计决策 | Opus 5，`high`，Terminal形态 | 设计判断不交给执行型模型 |
| 规格闭合的大型实现、重构、批量修改、补测试 | Codex按水位，或Opus 4.8 `xhigh`，轮着派 | 剩下的工作是转录；仓库级批量改优先Codex |
| 单文件小改、格式调整、文档同步 | Lead直接做 | 开Worker的成本高于工作本身 |
| Opus 5不可用 | Opus 4.8，`max` | 灾备，见上方灾备档 |

### Fable的两条入口

| 入口 | 条件 | 形态 |
|---|---|---|
| 深度只读道 | 要通读一个模块以上，并且要由它自己决定往哪查深 | `orca-fable-architect`队友，`Read/Grep/Glob` |
| 长期自主调查道 | 下方闸门四条满足两条或以上 | 同上，接受中途失败只能整体重派 |

深度只读道的入口是**范围条件，不是难度形容词**。"这个Review很有挑战性"不构成入口；"要通读支付模块加网关适配层，查到哪算哪由它自己定"才构成。写成难度形容词的入口会把Opus 5的判断道整个抽空——本Skill刚修完一次同型的病：一个档位借一个模糊标签吃掉一整类工作。

方案设计不整体给Fable，沿用下方的拆段规则：Fable出现状、约束、可选方案和代价，Lead选方案、定契约、写验收。

### Fable闸门：什么算"长期自主调查"

长期自主调查道的判定用下面四条，**满足其中两条或以上才成立**，只满足一条不派：

1. Lead列不出执行步骤，只能说清最终结果。
2. 根因候选跨越两个以上层面（代码、配置、基建、外部依赖），并且无法预先排除到其中一个层面。
3. 已经补过一次上下文并重试，证据仍然互相矛盾。证据来自Worker回报或Lead自己的调查都算。
4. 预计跨越单次工作时段，中途会持续出现新信息，Agent需要自己修正计划。

只满足一条时不派Fable。一条命中通常说明任务描述不够，Lead先补上下文，用Opus 5重试。

判据是工作模式，不是风险大小。"改错会影响生产"属于风险维度，它不触发Fable——高风险的架构决策恰恰归Lead本人，决策权不外派。把风险判据混进这张清单，会让Fable和Lead的职责在同一个任务上打架。

### 闸门和"决策权不外派"重叠时怎么裁

一个任务可以同时过闸门、又落在上表"架构决策、跨仓库契约、权限模型、数据迁移设计"这一行里。例如"重构支付模块，说不清怎么改，要动数据库schema、配置和第三方网关适配，预计好几天"，闸门中三条，同时又是数据迁移设计。

不要在两条规则之间二选一，按工作性质拆成两段：

| 段 | 归谁 | 产出 |
|---|---|---|
| 调查段 | Fable `high` | 现状、约束、可选方案、每个方案的代价和风险 |
| 决策段 | Lead本人，Opus 5 `max` | 选定方案、定契约、写验收标准 |

Fable负责把不确定性收敛成有限的可选项，Lead负责在可选项里做决定。整体派出去会让架构决策落到Worker手上，Lead自己硬啃调查段又违背闸门的本意。拆段之后两条规则各自成立，不再冲突。

任务小到拆不出两段时，说明它没有真正的调查段，按决策处理，Lead本人做。

选择Fable不等于选择更高effort，默认保持`high`。任务已经足够明确、只剩大量编码工作时，把计划定好后按规格闭合度派实施。

### 适合Opus 5的情况

Opus 5承担团队绝大部分工作，判断型全部归它：架构评审、复杂PR审查、疑难Bug推理、数据模型设计、安全分析和关键方案比较。实施型里规格不闭合的也归它：按现有契约实现一个模块要自己定内部分层和错误语义、重构要自己决定怎么切，这类活的核心是设计判断，不因为产出是代码就交给执行型模型。Lead本人跑`max`，派出去的一律`high`。

调查过程中发现问题跨出原有边界时，Lead按上方两条入口重新判定Fable，不要让Opus在不断扩大的任务里勉强继续。

### 适合Codex和Opus 4.8 `xhigh`的情况

这两档接规格闭合的实施：需求、约束和完成条件已经写清楚，剩下的工作是转录。跨模块实现、大型重构、批量补测试、仓库级迁移、复杂代码审查后的集中修复都在这一类，前提是spec把决策做完了。两档轮着派，同一批Task不全押一条路。

仓库级批量改不轮，优先Codex Sol：`ultra`能把工作分成多个互不覆盖的模块自行委派和汇总，Terra和Luna的`max`不委派，派给它们的任务要自己切好，Opus 4.8同样要Lead切好。任务仍在争论做什么或为什么做时，先交给Opus 5或Fable，不要直接开执行档。

## 派发语义与升级

模型切换有四种动作，只有最后一种叫升级。原先把四种都叫升级并画成一根直线，是规则断裂的原因。

| 动作 | 方向 | 触发 | 验收权 |
|---|---|---|---|
| 向下派 | Lead到Codex Sol Ultra | 需求已明确，只剩代码执行 | Lead |
| 向上派 | Lead到Fable `high` | 任务被识别为长期自主调查 | 仍归Lead |
| 横向派 | Lead到Agent Teams队友 | 需要并行分析，不想烧Lead的context | Lead |
| 档内升级 | Worker内部 | 当前档反复失败 | 不变 |

### 档内升级的合法路径

| 当前档位 | 触发条件 | 去向 |
|---|---|---|
| Codex任一档 | 无法稳定解释根因、需要决定核心架构或自动委派后仍没有可靠结果 | 交回Lead，由Lead重新判断派谁。不升Opus 4.8，两者能力大致相当，换过去是平移 |
| Opus 4.8 `xhigh` Worker | 同上 | 交回Lead，同样不平移到Codex |
| Opus 5 `high` Worker | 补过一次上下文仍然卡住 | 交回Lead。不在Worker里原地升`max`，Lead本身就是`max`，交回去等价且保留验收权 |
| Agent Teams队友 | 问题跨出分配范围 | 交回Lead |
| Fable `high` | 满足下方xhigh硬门槛 | Fable `xhigh` |
| Fable `xhigh` | 满足下方max硬门槛 | Fable `max` |

任何路径的终点都是Lead。Lead不会把任务升级出去然后失去验收权。Lead重新判断后可以补上下文派一个新的`max` Worker，那是重派不是档内升级。

### Lead自己卡住时怎么办

终点是Lead，那Lead卡住就没有向上的路。按下面顺序处理，不要无限重试同一档：

1. **重新过一遍Fable闸门**。判据3不绑定Worker，Lead自己调查得到的矛盾证据同样算数，所以Lead卡住时它通常已经成立，再满足任意一条就向上派出Fable调查，Lead保留验收。这是首选出口。任务同时属于架构决策时按上面的拆段规则走，只把调查段交出去。
2. **拆小再派**。任务能切出边界清楚的子问题时，把子问题派给实施档验证具体假设——切出来的子问题天然闭合，走Codex或Opus 4.8 `xhigh`——用它的结果收敛Lead自己的判断。
3. **前两条都不适用时问用户**。闸门没过、也切不出可验证的子问题，说明缺的是信息或方向而不是算力。这时提问不算"把普通技术判断转给用户"，它落在`SKILL.md`用户确认边界里"技术死路"那一条。提问时带上已排除的假设、已有证据和你需要用户提供什么，不要只说卡住了。

出口3不以"Fable `max`也失败"为前提。闸门没过时Fable根本不会启动，把它写成前提会让出口链在最需要的场景下全部失效。

不允许降档重试，不允许在同一档反复跑第三次。第二次失败还没有新证据时，说明缺的是信息不是算力。

升级时新开Session，把目标、已有证据、已排除假设、当前改动和完成条件一起交过去。不要只复制上一模型的结论，结论可能正是失败原因。

### Fable xhigh硬门槛

三条全部满足才允许开：

1. `high`已完成至少一次完整调查循环：读过相关代码、提出过假设、做过验证、拿到了证据。
2. 证据互相冲突，或关键假设用现有手段无法验证。
3. Lead已排除"任务描述不清"这一项，补过一次上下文后仍然卡住。

### Fable max硬门槛

三条全部满足才允许开：

1. `xhigh`已经实际跑过并失败。
2. 失败原因不是上下文不足、不是任务描述问题、不是缺少工具权限。
3. Lead在Task记录里写明这个任务的价值或风险足以承担高额消耗。

任一条不满足就不开。判断不清时先补上下文重试当前档，不靠升档掩盖任务描述问题。`max`容易在长任务里快速消耗token，也可能在部分任务上出现收益递减和过度思考。

## Agent Teams队友的context

Claude Agent Teams的队友定义在`~/.claude/agents/`，frontmatter支持`model`、`effort`和`tools`。2026-07-31实测确认`model`字段吃`[1m]`后缀：写`claude-opus-5[1m]`拿到1000000，写`claude-opus-5`只有200000。所以队友定义的`model`必须带后缀，跟启动命令一致。

2026-08-03实测补充：background队友确实继承完整执行能力，`Bash`、`Read`、`Write`、`Edit`全部可用，无权限拒绝也无确认弹窗，环境变量`CLAUDE_CODE_CHILD_SESSION=1`可用于自证子会话身份。所以"队友只能读文件"是`tools`字段写出来的边界，不是机制限制。给不给Bash是我们自己的选择。

### 派队友时不要碰Agent工具的`model`参数

队友的档位只有两个合法来源：队友定义frontmatter里的`model`，或者不指定时对本会话的继承。Agent工具那个`model`参数不是第三个来源，它一个合法值都没有。

<!-- consistency:allow-start -->
2026-08-05在Claude Code 2.1.222上逐值实测，2026-08-11升到`2.1.226`之后这张表没有重测：
<!-- consistency:allow-end -->

| 传入值 | 结果 |
|---|---|
| `claude-opus-5[1m]` | `InputValidationError`，schema拒绝 |
| `claude-opus-5` | 同上 |
| `claude-opus-4-8` | 同上 |
| `sonnet`、`opus`、`haiku`、`fable` | schema放行 |
| 不传 | 继承本会话，实测拿到`claude-opus-5[1m]`，`modelUsage.contextWindow`是1000000 |

报错原文是`Invalid option: expected one of "sonnet"|"opus"|"haiku"|"fable"`。也就是说本文全部档位名在这个参数里都表达不出来，schema在hook之前就把它们挡掉了。剩下四个别名又都不带`[1m]`，一律违反"所有子session显式1M上下文"这条硬约束。

所以规则是不传，不是"传对的那个"。不传时继承链把`[1m]`一起带过去，这条路径不受别名漂移影响。需要非默认档位就换`subagent_type`，让队友定义里的`model`生效。

`assets/claude/bin/orca-model-guard.py`挂在`PreToolUse`的`Agent`matcher上执行这条规则，拦截时把整改路径回灌给调用方。2026-08-05实测：`bypassPermissions`不跳过这个hook，`run_in_background`的调用同样拦得到，被拦之后模型会自己改成不传`model`重发。安装由`scripts/install.sh --apply`完成，它会把hook写进`~/.claude/settings.json`和每个`~/.claude-profiles/*/settings.json`。

### 队友能做什么，不能做什么

三个预装队友的能力差别全部落在`tools`上：

| agent | `tools` |
|---|---|
| `orca-opus-reviewer` | `Read, Grep, Glob` |
| `orca-fable-architect` | `Read, Grep, Glob` |
| `orca-opus-investigator` | 多一个`Bash` |

2026-08-03实测：派出的investigator自报可用工具与定义文件逐字一致，`tools`确实生效。

> [!CAUTION]
> **队友的只读性由`tools`承担，`permissionMode`不承担。** 2026-08-03实测：造一个带`Bash`且带`permissionMode: plan`的临时队友派出去，四条命令全部直接执行，其中包括往`/tmp`写文件，没有出现任何权限提示，也没有任何plan模式拦截。该队友自报它可见的系统提示里完全没有plan模式、planning-only之类的措辞，说明这个字段没有进入运行时上下文。本Skill所有session都用bypass参数启动，父会话的权限模式在这个组合下胜出。
>
> 所以在当前派发路径上`permissionMode`不提供任何保证，`scripts/check-consistency.sh`断言的是`tools`白名单。两个只读队友定义里的`permissionMode: plan`保留不删：它现在没有效果，但改用非bypass方式启动时会重新生效，留着算纵深防御。**不要把任何安全保证建在它上面。**

派活时按下面这条边界分：

| 工作 | 派给 | 原因 |
|---|---|---|
| 要读的文件在3个以内，或Lead已经读过 | Lead自己做 | 开队友的成本高于收益 |
| 通读整个模块或跨仓库做判断、Review、契约核对 | `orca-opus-reviewer` | 队友能力范围内，且省Lead的context |
| 需要2个以上独立视角同时Review | 派多个只读队友 | 同上 |
| 跑测试、翻日志、执行构建、查运行时状态 | `orca-opus-investigator` | 它有`Bash`，跑完回报结论，日志留在它自己的session里。这类活派给只读队友只会白烧一次session |
| 要改文件 | **Terminal Worker** | 三个队友都没有`Write`和`Edit`。模型按规格闭合度选：闭合的在Codex和Opus 4.8 `xhigh`之间轮，不闭合的用Opus 5 `high` |
| 失败之后要一条可审计的返工血缘，或需要上下文交接 | **Terminal Worker** | Agent Teams拿不到Orca的orchestration，见[Orca操作手册](orca-operations.md)"Claude Worker的三种形态" |

队友和Terminal之间只有一条判据：失败之后需不需要**一条可审计的返工血缘**。需要就走Orca Terminal形态，能整体重派就走Agent Teams队友。

判据问的是账，不是能力。队友在harness层面同样接得上：它停在半路时用`SendMessage`发一条，它会从自己的transcript接着跑。2026-08-03实测，14个停在写文件之前的队友全部这样捞了回来。所以"能不能接着跑"区分不了两种形态，两边都能。Terminal独有的是Task状态流转、`--retry-of`的重试计数和官方熔断，也就是出事之后有人替你记账。手工发消息拉得动，但那是Lead自己在当调度器，`orca terminal list`里查不到，失败次数也没人数。

给`orca-opus-investigator`派活时仍然要写清楚只是调查。它的系统提示禁止借Bash绕道改仓库，但任务描述里如果明说"顺手修一下"，等于让它在停下来回报和执行指令之间二选一，不要制造这种冲突。

### 为什么要用队友省context

Lead的context要活到任务结束，用来持有业务目标、Task DAG、Worker回报和跨仓库契约。大范围读代码会永久占用这部分容量，交给队友可以让代码原文随队友session一起释放，Lead只保留结论。

队友的实际context上限以队友session里的`/context`为准，不要凭定义文件推断。

## Agent执行前的判断顺序

ClaudeTeamAgents在启动Session前按以下顺序判断：

1. 任务是否落进Fable的两条入口：深度只读调研的范围条件，或长期自主调查的闸门。符合时选择Fable 5的`high`档。
2. 任务是否以判断为主，需要架构决策、方案比较或深度Review。符合时用Opus 5：架构决策和最终裁定由Lead本人以`max`做；只读的Review、通读模块和契约核对派`orca-opus-reviewer`；需要跑测试、翻日志、执行构建或查运行时状态的调查派`orca-opus-investigator`，队友都跑`high`。
3. 任务是否以实施为主。先判规格闭合度：不闭合的派Opus 5 `high`的Terminal Worker；闭合的在Codex和Opus 4.8 `xhigh`之间轮着派，Codex先跑一次`orca-codex-usage.py`按水位定档，仓库级批量改优先Codex。
4. Opus 5不可用时用Opus 4.8 `max`灾备。

在Codex和Opus 5之间判断不清时选Opus 5，不要从低档试错再用失败结果证明任务复杂。Fable不适用"判断不清就往上选"这条，它只由两条入口决定。也不要直接把Fable推理强度开到`xhigh`或`max`。

## 如何确认Session开对了

### Claude Code

进入Session后运行：

```text
/status
```

确认模型名称、账户和Session状态。再运行：

```text
/context
```

确认上下文容量和当前占用。需要检查或切换推理强度时运行：

```text
/effort
```

| 命令 | `/status`应显示 | `/context`应显示 | `/effort`应显示 |
|---|---|---|---|
| Opus 5 Lead | Claude Opus 5 | 1M | max |
| Opus 5 Worker | Claude Opus 5 | 1M | high |
| Codex三档 | 见下节 | 见下节 | 见下节 |
| Fable | Fable 5 | 1M | high |
| Opus 4.8实施档 | Claude Opus 4.8 | 1M | xhigh |
| Opus 4.8灾备档 | Claude Opus 4.8 | 1M | max |

`/context`显示200k说明`[1m]`没写上或者被剥掉了，停下来查启动命令。Fable只有符合硬门槛时才应显示`xhigh`或`max`。Opus 5 Worker显示`max`说明抄错了Lead的命令，效率和过度思考两头都吃亏，停下来重起。

### Codex CLI

进入Session后运行：

```text
/status
```

确认模型和推理档位跟本次要用的那一档一致：Sol配`ultra`，Terra和Luna配`max`。上下文容量应该是258400，那是272000的95%，不是100万——5.6系列没有1M。

Fast三档都不开，状态栏出现`fast`字样说明起错了。可以把状态栏打开确认：

```text
/statusline
```

`/fast`是切换命令，不要运行它。已经是关闭状态时运行一次会把Fast打开。

`/fast`是切换命令。状态已经是Fast时不要再运行，否则会把Fast关闭。

## 参数说明

| 参数 | 用处 |
|---|---|
| `--model 'claude-opus-5[1m]'` | 锁定Opus 5，并显式使用100万上下文 |
| `--model 'claude-fable-5[1m]'` | 锁定Fable 5，并显式使用100万上下文。不加`[1m]`是200k |
| `--model 'claude-opus-4-8[1m]'` | 锁定Opus 4.8，并显式使用100万上下文 |
| `--effort max` | Claude最高推理档，只属于Lead本体和Opus 4.8灾备档 |
| `--effort high` | Claude Worker统一档：Opus 5判断型与不闭合实施型Worker，以及Fable默认档 |
| `--effort xhigh` | Opus 4.8实施分摊档；Fable满足xhigh硬门槛时使用 |
| `--model gpt-5.6-sol` | Codex主力档，水位低于60%时用 |
| `--model gpt-5.6-terra` | Codex日常档，水位60%到85%时用 |
| `--model gpt-5.6-luna` | Codex省额度档，水位高于85%时用 |
| `model_context_window=272000` | 5.6系列的实际上限，有效可用258400 |
| `model_reasoning_effort="ultra"` | 最大推理并允许自动任务委派，只有Sol用 |
| `model_reasoning_effort="max"` | 推理深度与`ultra`相同但不自动委派，Terra和Luna用 |
| `service_tier="fast"` | **不使用**。模型目录标为1.5倍速度并增加额度消耗，纯粹速度换额度 |
| Claude危险权限参数 | 跳过Claude Code权限确认 |
| Codex危险权限参数 | 跳过Codex审批并关闭沙箱 |

`model_context_window`只在所选模型真的支持那个容量时有效，超过上限会被静默截断。2026-08-05核对`codex debug models`与Session记录：5.6系列上限272000、有效258400，早期版本写的1000000从来没有生效过。任何时候都按`/status`的实际显示处理，不得仅凭启动命令宣称已经开启。

## 给ClaudeTeamAgents的固定指令

可以把下面这段交给负责启动Session的Agent：

> 团队用两张表描述模型。能力排序是Fable 5、Opus 5 `max`、Opus 5 `high`、然后Opus 4.8与Codex三档大致相当；使用顺位是Opus 5最高、Codex和Opus 4.8 `xhigh`并列次之、Fable少而准、Opus 4.8 `max`只做灾备。Lead固定跑Opus 5的`max`档加1M上下文，持有业务方向、Task DAG和最终验收权，可以派出能力更强的Fable Worker但不交出验收权；派出去的Claude Worker一律`high`，`max`只属于Lead本体。判断型工作用Opus 5。实施型工作先判规格闭合度：spec把输入输出、边界、错误语义、验收全写死的算闭合，在Codex和Opus 4.8 `xhigh`之间轮着派，不全押一条路，仓库级批量改优先Codex；实现中还要做设计决策的算不闭合，派Opus 5 `high`的Terminal Worker。能用Opus 5干的优先Opus 5。Codex派之前跑`orca-codex-usage.py`按周额度水位在Sol、Terra、Luna里选一档，降档换模型不换推理强度：Sol配`ultra`、Terra和Luna配`max`，三档都不开Fast。Fable有两条入口：深度只读Review与调研，入口是范围条件——要通读一个模块以上并自己决定往哪查深；长期自主调查，闸门是四条判据满足两条或以上——列不出执行步骤、根因跨两个以上层面、补过上下文重试后证据仍矛盾、预计跨越单次工作时段，只满足一条先补上下文用Opus 5重试。任务同时属于架构决策或数据迁移设计时拆段，调查段给Fable、决策段留Lead。Fable的`xhigh`和`max`需要满足硬门槛才能开，不进常规模板。所有Claude Session显式配置100万token上下文，Codex是272000上限不适用这条。启动后必须检查模型、推理档位和上下文容量，Fast应当是关闭的，实际状态与命令不一致时停止任务并报告。

## 资料来源

- [Anthropic Claude Code模型配置](https://code.claude.com/docs/en/model-config)
- [Anthropic Claude Code CLI参考](https://code.claude.com/docs/en/cli-reference)
- [Anthropic Claude Code Subagents](https://code.claude.com/docs/en/sub-agents)
- [OpenAI Codex模型选择](https://learn.chatgpt.com/docs/models)
- [OpenAI Codex配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)
- 本机`claude --help`、`codex --help`和`codex debug models`输出，核验日期为2026-07-31
- `claude -p --output-format json`的`modelUsage.contextWindow`探针：2026-07-31首次核验，2026-08-05重跑并覆盖`[1m]`后缀行为、别名解析去向、Agent工具`model`参数取值范围、队友模型继承，2026-08-11在Claude Code `2.1.226`上第三次重跑`[1m]`后缀行为
- `orca status --json`的`result.runtime.appVersion`：Orca版本的唯一来源，`orca --version`不带版本号
