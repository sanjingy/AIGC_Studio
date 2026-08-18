# Orca自动任务路由

- 顶层开发目标涉及多仓库、多个独立工作面、复杂业务判断或用户要求统筹时，自动加载`orca-task-router`，由当前Lead判断复杂度、依赖、模型和Worker数量。
- 不要求用户逐个Task选择Opus、Fable或Codex，也不为普通派发、重试和失败升级要求确认。
- 由`claude-cc-teams`启动且没有上级Task边界的会话是Lead。收到Lead分配的明确Task时是Worker，只完成分配范围，不再次组织团队。
- Lead固定跑Opus 5的`max`档，持有业务方向、跨仓库契约和最终验收权；`max`只属于Lead本体，派出去的Claude Worker一律`high`。判断型工作用Opus 5。实施型工作按规格闭合度派：spec把输入输出、边界、错误语义、验收全写死的算闭合，在Codex和Opus 4.8 `xhigh`之间轮着派，不全押一条路，仓库级批量改优先Codex；实现中还要做设计决策的算不闭合，派Opus 5 `high`的Terminal Worker。能用Opus 5干的优先Opus 5。Fable有两条入口：深度只读Review与调研，入口是范围条件——要通读一个模块以上并自己决定往哪查深；长期自主调查，即skill内"Fable闸门"四条判据满足两条或以上，只满足一条先补上下文用Opus 5重试。同时属于架构决策或数据迁移设计的，调查段给Fable、决策段留Lead。Opus 4.8不接决策权，`max`档只做灾备，在Opus 5不可用时顶上。
- Lead可以派出能力比自己强的Worker，派出去不转移验收权。任何升级路径的终点都是Lead。
- 形态判据是失败之后需不需要一条可审计的返工血缘。能整体重派的走Agent Teams队友，不占Terminal：只读Review派`orca-opus-reviewer`，要跑测试翻日志查运行时状态派`orca-opus-investigator`。需要那本账的，比如改文件、要失败重试计数和熔断，才开Terminal Worker，因为只有Terminal形态拿得到Orca的orchestration；Terminal里的模型按规格闭合度选，Claude和Codex都可以。两种形态都接得上中断，队友用`SendMessage`就能从它自己的transcript接着跑，判据问的不是谁能接着跑。
- Worker子session的启动命令以skill内`references/session-launch-guide.md`为准：Claude全部显式1M context，Fable的`xhigh`和`max`需要满足指南里的硬门槛才能开。
- Codex分三档按周额度水位选，派之前跑`python3 ~/.config/claude-cc/orca-codex-usage.py`：低于60%用Sol配`ultra`，60%到85%用Terra配`max`，高于85%用Luna配`max`。降档换模型不换推理强度，因为effort影响的output只占总token的0.4%，省不到东西。三档都不开Fast，它是1.5倍速度换额度。Codex上下文上限是272000，没有1M，配更大的值会被静默截断。
- Claude Code对模型表里没有的模型按200k上限处理。Opus 5必须写成`claude-opus-5[1m]`才拿得到100万上下文，只写`claude-opus-5`会被压到200k。
- 用Agent工具派队友时不传`model`参数，一个合法值都没有：完整模型名会被schema拒掉，剩下的`sonnet`、`opus`、`haiku`、`fable`四个别名都带不上`[1m]`，还会跨版本改指向。不传时子Agent继承本会话的`claude-opus-5[1m]`，含1M上下文。要换档位就换`subagent_type`，让队友定义里的模型生效。`orca-model-guard.py`会拦下带`model`的调用，被拦了按理由改不要绕。
- 这条判的是本会话自己的动作，不是用户的措辞：Lead在长任务中途自己决定并行、切分或派发时，同样按上面的档位和形态规则走，不因为用户不在场或没提编排就退回通用直觉。
- 默认复用已有feature分支和当前Worktree。没有隔离需求时不要创建新分支。Worker的Terminal默认起在Lead所在的worktree，`terminal create --worktree`和`worker-start --worktree`填同一个绝对路径，漏了`worker-start --worktree`会报`terminal_worktree_mismatch`。
- Worker验收完成、交接完成或被放弃后，Lead先`orca terminal read`收完证据，再`orca terminal close`回收它的Terminal。跑完的Worker不留着占分屏。交付前用`orca terminal list`确认没有遗留。
- 只有产品方向缺口、不可逆操作、外部权限、生产发布、commit、push或对外发送需要用户确认。
