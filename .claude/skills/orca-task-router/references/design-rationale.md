# 设计依据

## 用户目标

- 用户只描述最终目标，不逐个Task判断复杂度或选择模型。
- Lead跑Opus 5的`max`，理解业务方向、组织工作并负责最终验收；派出去的Claude Worker一律`high`。
- 判断型工作用Opus 5。实施型工作按规格闭合度派：闭合的在Codex和Opus 4.8 `xhigh`之间轮，不闭合的派Opus 5 `high`的Terminal Worker，能用Opus 5干的优先Opus 5。Fable走两条入口：深度只读调研的范围条件，或长期自主调查的闸门。
- Orca展示Claude Agent Teams原生分屏，同时允许主Agent管理Codex Terminal。
- 多仓库需求复用已有feature分支，不为了多Agent强制创建新分支。
- 普通派发不询问用户，危险操作和产品方向缺口才询问。

## 为什么不使用自动Memory保存策略

Claude自动Memory适合保存模型观察到的项目经验，内容可能由模型更新，并且按机器和项目存放。它不适合作为稳定的路由策略。

本方案把职责分开：

- `claude-cc-teams`的追加系统提示只保存Lead必须自动路由这一条强约束。
- `~/.claude/rules/orca-task-routing.md`提供所有Claude会话都能看到的角色边界。
- 本Skill保存完整、可版本控制的判断流程。
- `~/.claude/agents/`保存Claude角色和模型选择。
- 各项目自己的`CLAUDE.md`、`AGENTS.md`和`CONTEXT.md`保存业务上下文。

## 上下文守卫为什么放在项目级配置

Worker跑久了滑出Smart Zone，需要在越线时提醒它交接。Claude Code不给hook暴露token数，但transcript的JSONL里有`message.usage`，把最后一条的input侧三项相加就能重建`/context`看到的数字，所以守卫做成一个PostToolUse hook自己读transcript。

配置怎么进去，试了三条路才通。

**第一条：`claude --settings`给Worker单独加载。** 被`claude-cc`拒绝：那个wrapper要用`--settings`注入API Key Helper，而Claude Code对重复flag是后者覆盖前者，传两次会把认证配置冲掉。

**第二条：让wrapper合并认证配置和守卫配置。** 技术上做通了，安全审查也过了（键白名单只放行`hooks`，因为`env`是settings合法键、能把`ANTHROPIC_BASE_URL`指向攻击者，黑名单挡不住这类）。但这条路要在每次启动时动认证链路，而"任何操作不能触发登录"是用户的红线。为红线让路，整段撤回，`claude-cc`恢复原样。

**第三条：项目级`.claude/settings.json`。** 通了。本机每条启动路径最后都汇到`claude-cc`，`--settings`让`~/.claude/settings.json`的hooks不生效，但项目级配置在它之上被读取。2026-08-01用探针实测：全局hook不触发，项目级hook触发。<!-- consistency:allow-start -->**这半句在2026-08-05被推翻**：在当时的Claude Code 2.1.222上重测，<!-- consistency:allow-end -->`~/.claude-profiles/<profile>/settings.json`里声明的`PreToolUse` hook走完整`claude-cc`启动路径确实触发，`--settings`是合并不是遮蔽。context guard继续留在项目级是既有选择，不再是因为全局不生效。新加的hook不必跟着放项目级，model guard就装在user级。

代价是每个worktree都要铺一份，所以有了`scripts/install-project-guard.sh`。收益是完全不碰认证链路。

**阈值按百分比不按固定token数。** 最初写死300k，那是按1M窗口算的；200k窗口的会话永远到不了300k，守卫在它身上等于不存在。窗口大小hook拿不到——transcript里的模型名不带`[1m]`后缀，1M会话和200k会话在超过200k之前长得一模一样。statusline拿得到`context_window_size`，所以由它写出来给hook读。

**用户侧还有一层statusline提醒。** hook能不能生效取决于启动路径，而statusline在所有路径下都执行，越线时把上下文段显示成`⚠ 该交接`。hook让Claude自己停，statusline让用户看见，两层互不依赖。

守卫只在Worker主线程触发。subagent的PostToolUse带的是父会话的`transcript_path`和`session_id`，在那里动手会把提示注入到subagent、同时消耗掉父会话的提醒标记，导致主线程永久静默，所以守卫检测到`agent_id`就直接退出。

守卫只覆盖Claude Worker。Codex没有等价的hook机制，而Codex是实施主力，所以这个能力的实际覆盖面比看上去小，Codex那边仍然靠Lead观察回报节奏手动收回。

## 为什么允许重述，但用机器保证一致

规则分裂过一次：同一条"Lead是谁"在7个文件写了7遍，出现5个说法，而运行时正在跑一个Opus 5的session被告知自己是Fable。

直觉的解法是禁止重述，让每条规则只有一处定义。这条路走不通，有三类重述删不掉：

- `assets/claude/rules/orca-task-routing.md`是always-on规则，Skill没加载时要靠它兜底，必须自包含。
- `assets/claude/agents/*.md`的frontmatter必须写具体模型名，那是给Claude Code读的字段。
- `references/orca-operations.md`要给出可直接复制的完整启动命令，不能只写引用。

而且强行消除重述会逼Lead在判断时反复跳文件，每跳一次都烧context，Skill本身反而变难用。

所以改成允许重述、机器保证一致：[Session启动指南](session-launch-guide.md)顶部维护一个`canonical-facts`块和一份废弃说法黑名单，`scripts/check-consistency.sh`拿它核对全Skill。人改一处，脚本负责发现另外几处没跟上。

`scripts/verify-fixtures.sh`还会注入多种真实发生过的漂移，确认校验脚本自己没失效。一个永远报绿的检查比没有检查更危险，它会给出虚假的完成信号——这个Skill在grep全绿的时候，Fable Worker和两个只读队友正全部跑在200k。

## 为什么不随机选择Opus和Codex

随机选择不能利用任务语义，还会让同类任务出现不可解释的成本和结果差异。这条反对的是拿随机替代语义判断。v0.16.0的轮换不在此列：规格闭合度先把语义定死——不闭合的确定归Opus 5 `high`，轮换只发生在闭合道内两个大致等强的档位之间，同类任务拿到的质量档位仍然是确定的。

## 为什么实施型不再只归Codex，2026-08-06

原规则把实施型整类焊在Codex上。焊点不是一次论证，是三层标签叠出来的。

<!-- consistency:allow-start -->
| 层 | 出处 | 埋了什么 |
|---|---|---|
| 公理层 | 本文"用户目标"旧文写着"实施型工作用Codex Sol Ultra" | 写在前提里，从未论证 |
| 模板层 | 启动命令区唯一的Claude终端模板标着"判断型Worker，团队主流档" | 实施型没有Claude模板可抄 |
| 判据层 | 派活边界表写"要改文件→Codex Worker" | 离派发决策最近，所以它赢 |
<!-- consistency:allow-end -->

跨仓库所有权那棵树把两个实施位都画成Codex，把三层在视觉上焊死。结果是一个档位借一个模糊标签吃掉一整类工作，而2026-08-03的实测早已证明"队友只读"是`tools`字段写出来的选择，机制上Claude改文件毫无障碍，SKILL第6步也一直把Claude和Codex并列为实施型Worker——规则内部自己就在打架，只是判据层离决策最近，赢的总是它。

唯一的经济论证是一句"Token预算只作为偏向Codex的约束"。2026-08-06用户裁定：额度不再是本Skill的路由输入，两侧额度平均且充足。Codex水位三档保留，因为那侧有`orca-codex-usage.py`这个可执行的客观数字，而且它约束的是Codex内部选档，不决定活归不归Codex。

新刀口是**规格闭合度**：spec把输入输出、边界、错误语义、验收全写死的算闭合，实现中还要做设计决策的算不闭合。它比"判断型对实施型"准，因为大量非机械工作落在旧刀口的实施侧——按契约实现要自己定分层和错误语义，重构要自己决定怎么切。闭合度问的是Task写没写透，这件事Lead派发前就知道答案，因为spec是Lead自己写的。

四条防回退，每条都是这次差点再犯或已经犯过的：

- **Worker用`high`的理由是吞吐和过度思考，不是省额度。**effort影响的输出token只占总量的0.4%，所以拿这个数据来证明"high省不了钱、应该统一max"推翻不了规则——规则本来就不是按省钱立的。`max`更慢，且在长任务里有过度思考和收益递减，Fable `max`硬门槛那节早记过同一现象。
- **Fable深度只读道的入口必须是范围条件，不能是难度形容词。**"这个Review很有挑战性"式的入口会把Opus 5的判断道整个抽空，跟Codex借"实施型"标签吃掉一整类工作是同一个病，本节前三段就是病历。
- **Opus 4.8 `xhigh`分摊腿的理由是路径防腐坏，不是能力。**从不被行使的派发路径会静默腐坏，本Skill自己有三个案例：`--live`从不被自动化调用、model guard的matcher在工具改名时无声失配、statusline的`// 0`兜底能让守卫永久静默。轮着派让这条路径始终被行使着。
- **闭合实施轮着派，不写配额。**回看一周发现某一档占比压倒性时，查的是Task spec写没写透、判断有没有向单边漂，不是调配额。

Opus 4.8 `max`同时从"额度紧的备用"改成灾备档，只在Opus 5不可用时顶上：额度既然出局，原触发条件就空了，而能力上4.8严格弱于Opus 5，不缺额度时没有理由主动选它。

## 为什么Codex不是Claude原生队友

Claude Agent Teams的队友都是Claude Code会话，共享团队Task和消息。Codex是独立CLI，不能加入Claude原生Team。Orca提供统一Terminal和Orchestration层，所以Lead仍能从一个主视角管理两类Worker。

## 为什么Lead是Opus 5而不是能力第一的Fable

团队能力排序把Fable放第一，但Lead位子给了Opus 5。这不是笔误，理由有三条：

- Lead位子要的是稳定协调和验收，不是最强推理。Lead持有业务目标、Task DAG、跨仓库契约和最终验收权，这些是记账和判断，不是攻坚。
- Lead是用户实际敲字交互的那个session，需要随时应答。Fable的优势在无人值守的长跑，放在这个位子上是浪费。
- 最难的活可以派出去。Lead不需要自己是能力第一，它只需要认得出什么时候该请Fable。

<!-- consistency:allow-start -->
由此得到一条反直觉但正确的结论：Lead可以派比自己更强的Worker，派出去不转移验收权。原设计把这一条漏掉了，于是"Fable能力第一"被读成"Fable是Lead"，规则在7个文件里分裂成5个说法。
<!-- consistency:allow-end -->

## 为什么把排序拆成两张表

原设计只有一张"团队固定排序"表，同时承担"谁更强"和"该派谁"两个问题。这是分裂的根因。现在拆成表A能力排序和表B使用顺位，两张表顺序不同，各自标注用途。判断升级看表A，日常路由看表B。

Opus 5能力紧随Fable却使用第一，因为它便宜且能力强。Codex和Opus 4.8 `xhigh`能力垫底且大致相当，却并列使用次之，因为规格闭合的实施是量最大的工种之一。这类错位用一张表表达不了。表A的并列还带来一条升级修正：Codex失败升4.8是平移，正确去向是Opus 5。

<!-- consistency:allow-start -->
## 为什么删掉Codex XHigh

Codex只保留Ultra一档。XHigh和Ultra的能力差在实际使用中不明显，但保留两档会让Lead在派发时多一次无谓判断，而且"边界完全清楚"这个触发条件本身就不可靠，判断错了就要重跑。团队token充足，直接用Ultra省掉这次判断。
<!-- consistency:allow-end -->

## 为什么Codex重新分档，以及为什么分的不是effort

上面那条论证有四根支柱，2026-08-05塌了最后一根：Codex的周额度不够用了。前三根还立着，所以这次不是把它推翻，是在它没覆盖的维度上重建。

先量了消耗结构，没有凭直觉动手。统计本机08-05全部大Session：

| 项 | 占总token |
|---|---|
| input | 99.6%，其中97.6%命中缓存 |
| output | 0.4% |

effort档位调的是`reasoning_output_tokens`，它在那0.4%里面。**降effort省不到东西。**第一版方案画的是`ultra`降`max`降`high`的阶梯，砍的正好是不花钱的那部分，数据一出来就作废了。真实消耗来自每个turn把整段context重送一遍，单个Session跑294个turn、累计3800万token；一天之内周额度从19%走到34%，涨的10个百分点几乎全部来自同时开的几个并行Worker。

所以杠杆按大小是turn数、并行Worker数、模型单价，effort根本不在表上。分档就分模型：Sol、Terra、Luna，effort全部钉在各自最深一档不做牺牲。Terra和Luna用`max`而不是`ultra`，是因为`max`的推理深度跟`ultra`相同、只是不自动委派，而委派会派出更多子任务。

这条路径不经过`xhigh`，所以BANNED里那两条不用翻案，上面那节的结论在它自己的范围内仍然成立。

<!-- consistency:allow-start -->
触发条件用周额度水位不用任务难度，直接回应旧论证的第三根支柱："边界清不清楚"判错要重跑，那个顾虑今天依然成立。水位是客观数字、判错不会导致返工，而且当时SKILL里有同构先例——Opus 4.8旧备用档的触发条件带着"且Opus 5额度吃紧"（该档位在v0.16.0改成灾备、额度条件废除，先例已不在，但这不影响当时的论证）。水位低于60%时三档规则等价于旧规则，判断成本只在额度真紧时才付。
<!-- consistency:allow-end -->

`assets/claude/bin/orca-codex-usage.py`让这套判据可执行。Codex没有`usage`子命令，但TUI状态栏那个百分比每个turn都写进`~/.codex/sessions`的rollout日志，读文件就拿得到，不需要终端在跑。

## Codex从来没有1M上下文

同一次核对里发现的，比档位问题更早存在。`codex debug models`报5.6全系`max_context_window`是272000、`effective_context_window_percent`是95，实际可用258400，跟Session记录逐位对上。启动命令里的`-c model_context_window=1000000`从来没有生效过，被模型上限静默截断，而`canonical-facts`、硬约束"全部显式1M context"、参数说明表和`check_codex_command`的一条断言全都建立在这个错误上——校验脚本一直在忠实地强制执行一个无效参数。

讽刺的是指南里早写了免责声明"若`/status`显示的容量不是100万，按实际状态处理，不得仅凭启动命令宣称已经开启"。写了，没人去验。这是同一个坑的第五次：照着命令和help文本推断行为。整个5.6系列都没有1M，`gpt-5.4`才有。

## 为什么默认不创建新Worktree

用户现有的两个仓库已经在对应feature分支上。多Agent只要求执行上下文隔离，不必产生额外分支。不同仓库使用各自Terminal已经能隔离写入范围。只有同一仓库需要真正并行写入或实验性修改时，Worktree才有价值。

## 为什么加第三种队友形态

原先只有两种形态，把工作按"改不改文件"切了一刀：不改的派只读队友，改的开Orca Terminal。这一刀切错了位置，留下一类无家可归的工作——**跑测试、翻日志、执行构建、查运行时状态**。它们不改文件，却需要执行命令。旧规则只能把它们推给Codex Terminal Worker，为了跑一条命令付出建终端、等TUI就绪、绑Dispatch、事后手工回收的全套成本。

2026-08-03实测确认background队友继承完整执行能力，`Bash`、`Read`、`Write`、`Edit`全部可用，无权限拒绝也无确认弹窗。所以"队友只能读文件"从来是`tools`字段写出来的选择，不是机制限制。既然是选择，就该按工作性质重新划，于是有了`orca-opus-investigator`：给`Bash`，不给`Write`和`Edit`。

判据跟着换了三轮。

<!-- consistency:allow-start -->
第一版接替"改不改文件"的判据写成"有没有需要被记账和重试的中间状态"，问法不可判定：任何超过一条命令的任务都有中间状态，问出来永远是"有"，换个人问还能得到另一个答案。

第二版改成**失败之后需不需要从中断点继续**，理由是答案唯一：同一个任务对"能不能从头重跑一遍"只有一个回答，不随谁来判断而变。这一版被实测推翻。队友在harness层面同样接得上——2026-08-03那轮有14个队友停在写文件之前就结束了，用`SendMessage`逐个发一条，它们全部从自己的transcript接着跑完并补写了文件。判据字面在问一个两种形态都满足的条件，照字面判会把任何不想从头重跑的任务都送进Terminal。
<!-- consistency:allow-end -->

现在用的判据是**失败之后需不需要一条可审计的返工血缘**。需要就走Orca Terminal形态，能整体重派就走Agent Teams队友。

第三版修的是问错了对象，不是问错了答案。前一版想问的东西没错，措辞把"任务需不需要"写成了"形态能不能"。加上可审计三个字之后，判据对着的是Terminal独有的那本账：Task状态流转、`--retry-of`的重试计数、连续失败3次的官方熔断。手工发消息也能把队友拉起来，但那是Lead自己在当调度器，`orca terminal list`里查不到，失败次数也没人数。任务需要那本账时收益为正，不需要时收益是零，跟前一版一样答案唯一，同时不再跟形态能力混淆。

Agent Teams队友拿不到orchestration，这是选型时唯一要认真权衡的代价，对调查型工作我们判定可以接受。少了哪些具体能力、以及怎么按判据挑形态，见[Orca操作手册](orca-operations.md)"Claude Worker的三种形态"，这里不重复。取舍本身记在这：**不要因为任务重要就把它升级成Terminal形态**，重要程度不是这条判据。

### 队友的只读性由`tools`承担，不由`permissionMode`承担

三个队友定义里`orca-opus-reviewer`和`orca-fable-architect`带`permissionMode: plan`，`orca-opus-investigator`没带。这处差异一度被写成一条因果：`plan`会挡住命令执行，investigator要跑Bash所以不能加。这条因果没有实测依据，站不住。

三件事叠在一起让它站不住。本Skill所有session都用bypass参数`--dangerously-skip-permissions`启动；官方文档称bypass可用时plan模式不再阻断编辑和命令；subagent继承Lead的权限模式，Agent Teams又没有在spawn时按队友覆盖它的入口。照这三条推，`permissionMode`在本Skill的派发路径上很可能根本不被读取。

更麻烦的是照着那条因果写出来的断言。它校验了一个大概率不生效的字段，同时放过了真正承载只读保证的`tools`。实测把只读队友的`tools`加上`Write, Edit`，旧断言照样报OK、退出0，一条自称在守只读性的检查，对只读性被破坏毫无反应。

所以`scripts/check-consistency.sh`改成断言`tools`白名单：队友只允许持有`Read`、`Grep`、`Glob`、`Bash`，多出来的一律报错。匹配按整个token做而不是子串，因为`BashOutput`能满足`*Bash*`、`NotebookEdit`藏在`*Edit*`里面，子串写法会把这两个放进来。`scripts/verify-fixtures.sh`注入了写工具、`NotebookEdit`、`BashOutput`和整段`tools`缺失四种漂移，确认断言真的会拦。新工具想进白名单必须有人手动加，飘不进来。

2026-08-03补上了实测。造一个带`Bash`且带`permissionMode: plan`的临时队友派出去，四条命令全部直接执行，包括往`/tmp`写文件，没有权限提示也没有plan模式拦截；它自报可见的系统提示里完全没有plan模式或planning-only之类的措辞。**那个字段没有进入运行时上下文，父会话的bypass参数胜出。**推断方向是对的，但在拿到这条实测之前，它仍然只是推断。

两个只读队友定义里的`permissionMode: plan`保留不删。它现在没有效果，改用非bypass方式启动时会重新生效，留着算纵深防御，代价为零。要紧的是别再把只读保证记在它头上。

把只读保证从散文搬进机器断言这个动作是对的，上一版只是搬错了对象。教训另有一条：本文件"已知边界"一节自己就写着"两分钟实测就能推翻的结论不要靠推断"，而plan会挡Bash这条正是靠推断写下的，还据此立了一条守错东西的断言。同一个坑踩了第二次。

## 已知边界

- Orca 1.4.179没有按任务复杂度自动切模型的原生Router，判断由Lead和本Skill完成。官方的`worker-start`只接受预设agent id，不透传`--model`、`--effort`和Codex的`-c`参数，所以档位必须由本Skill在低层启动命令里指定。
- Claude角色定义只能控制Claude模型；Codex必须由Orca创建外部Terminal。
- 队友的Agent定义写显式模型名而不是`opus`别名。理由不是别名当前指向哪个模型，而是它会漂：两个相邻小版本之间，`opus`指向的模型已经换过一次，实测记录见[Session启动指南](session-launch-guide.md)。指向变了我们不会收到通知，显式模型名加`[1m]`才锁得住。同一条理由也适用于Agent工具的`model`参数，那个参数连显式模型名都不接受，只能不传。
- `[1m]`后缀在subagent frontmatter里确实生效，2026-07-31实测：`model: claude-opus-5[1m]`拿到1000000，`model: claude-opus-5`只有200000。所以队友定义必须带后缀。此前依据"官方文档没写"推断它可能无效，是错的，两分钟实测就能推翻的结论不要靠推断。
- 队友的实际context上限仍以队友session里的`/context`为准。`[1m]`是否生效依赖模型注册表条目带`supports_1m_suffix`，Opus 5不在Claude Code 2.1.226的内置模型表里，它的1M能力由网关下发的availableModels决定。
- 系统提示和规则能提高执行一致性，但不能替代Lead对Worker结果的实际验证。

## 已知缺陷，2026-08-03的Review挑出来但暂缓处理

四路独立审查（其中一路反编译了`Orca.app`对源码，一路用fake CLI复现）挑出四十余条。会造成破坏、照做失败、以及让测试报假绿的部分已经修完并逐条验证。以下是当时判定为"现在不咬人、以后会咬"而留下的，按咬人的可能性排：

- **安装器写死`$HOME/.claude`，不认`CLAUDE_CONFIG_DIR`。**本机实际用的是`~/.claude-profiles/20x`，现在没出事只因为profile里的`statusLine`写的是绝对路径，而`~/.claude`下的rules/agents/skills都是指回profile的symlink。换profile或换机器就会装到宿主看不见的地方，而`verify_all`校验的是同一条错路径，照样报成功。跟llm-wiki安装器那次"装到宿主看不见的地方还报ok"是同类。附带：`chmod 700`会跟随symlink改到profile目录本体。
- **`install_file`统一`chmod 600`，剥掉了statusline的可执行位。**仓库里是755，装完变600。当前配置用`sh <path>`调用所以无碍；哪天改成直接执行，statusline会静默失效，而它正是上下文守卫的窗口数据来源。`verify_file`只比内容看不到权限。
- **`install.sh`的statusline接线检查在`settings.json`不存在时整个跳过**，而"全新机器"恰好是它注释里点名要防的场景。
- **statusline用`// 0`兜底。**Claude Code若改掉`used_percentage`字段名，会写出一个看着正常的`0`，守卫读到`(window, 0.0)`直接返回，连固定token的兜底分支都到不了，从此一次都不触发，而所有grep依然通过。改成`// null`才能让缺失可辨。
- **smoke的三条SKIP路径用`exit 0`**，跟"全部通过"共用退出码，只看退出码的调用方分不清"全绿"和"一条都没跑"。
- **`/tmp/orca-smoke-ask.json`路径固定、令牌硬编码、断言前不清空。**上一轮被中断留下的文件会让这一轮在ask/reply完全没跑通的情况下报绿。
- **`expect_drift_caught`只判非零退出**，`check-consistency.sh`因事实块畸形而`exit 2`也会被算成"抓到了漂移"。当前21条注入逐条重放过、理由都对，属于结构性隐患而非已发生。
- **`check_versions`的stray版本正则写死`1.4.1xx`等三个版本族**，版本跨段之后检测静默失去覆盖。
- **`--live`从不被任何自动化入口调用。**官方命令存活探测是`check-consistency.sh`里最值钱的一段，却全靠人记得手动加参数。
- ~~**allowed-tools缺`Bash(orca terminal show:*)`和`install-project-guard.sh`的执行权限**~~ 0.12.0补上，并由`check_reclaim_grant`和`check_runtime_script_grant`各加一条断言守住。这类缺口不会报错，只会在派发中途弹权限等一个没在看屏幕的人，所以两条都配了漂移注入。
- **Task DAG讲了依赖但全文没有`--deps`。**带依赖的Task状态是`pending`，而非retry的`worker-start`要求`ready`，会被拒且文档里找不到解释。
- **覆盖缺口**：自定义argv经过`worker-start`接管后不丢这条，2026-08-06在Claude侧手工补验过一次（Opus 5 `high`探针，接管后argv原样、状态栏报`Opus 5 (1M context)·high`、`worker_done`结案），但smoke测试里验档位的终端和被接管的终端仍然不相交，Claude档位断言尚未进`smoke-orchestration.sh`；重开Task再派注入的仍是原spec；上下文守卫是否真在阈值触发；`terminal read`不带`--cursor`第二次返回空。

## 已知缺陷，model guard（0.14.0）留下的

- **guard只管住了事故的一半。**事故是`general-purpose`＋`model=sonnet`去改文件。guard堵的是`model`那一半，堵完之后同一个调用会以`claude-opus-5[1m]`继续用裸`general-purpose`改文件，没有Task状态也没有重试计数。另一半目前只靠`SKILL.md`的散文和eval 23管着，没有机器断言。要真堵住得判断prompt意图或者按`subagent_type`白名单拦，两条都比这次的规则贵得多，暂缓。
- **`TeamCreate`是否也吃`model`参数没有验证。**它在`SKILL.md`的`allowed-tools`里，如果它的入参也能钉模型，那就是绕过guard的第二条路。全库transcript里没有一次`TeamCreate`调用，所以现在不咬人。验证方法是查一次它的schema，有顶层`model`字段就把matcher扩成`Agent|TeamCreate`；如果模型钉在members数组里，guard的取值逻辑要跟着改，不能只扩matcher。
- **guard的matcher是它唯一的生命线，而工具改过一次名。**`Task`在2.1.63改成`Agent`。再改一次，matcher静默失配，guard一次都不触发，`check_model_guard`照样全绿——它grep的是我们自己写进安装器的字符串，属于循环论证。唯一能证伪的动作是手工派一个带`model`的调用看它拒不拒，`SKILL.md`已写明升版后必做，但没有自动化入口。
- **`~/.config/claude-cc/orca-model-guard.py`被删或`~/.config`被清时，hook命令的else分支静默吞掉stdin**，全部派发无守卫。这是照抄context guard的容错写法，好处是不会因为脚本丢失而焊死派发，代价是失效无声。只有`install.sh --verify`看得见，而它跟`--live`一样没有自动化入口。
- **description已用640字符，`skillListingMaxDescChars`是1024。**2026-08-05实测这个限制按字符算不按UTF-8字节算（当前描述是1610字节，listing里完整显示）。余量384字符。触发条件六排在尾部，再往description里加东西之前先确认它没被截掉，截掉了本次改动的主目的就落空。
