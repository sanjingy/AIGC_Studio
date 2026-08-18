# 复杂度与模型路由

> [!IMPORTANT]
> 本文是复杂度分级的语义层，回答"什么样的任务算高中低复杂度"。模型档位、启动命令、派发语义、Fable闸门和升级门槛的定义在[Session启动指南](session-launch-guide.md)，两边冲突时按指南执行。
>
> 本文会重述指南里的关键结论，方便Lead判断时不用跳文件。重述的一致性由`scripts/check-consistency.sh`机器保证，不靠人记得同步改。改档位、模型或阈值时改指南里的`canonical-facts`块，然后跑一次校验脚本。

本规则用于Lead内部判断。用户只给最终目标，不参与逐Task选模型。

## 团队固定排序

以[Session启动指南](session-launch-guide.md)的表A和表B为准。能力排序是Fable 5、Opus 5 `max`、Opus 5 `high`、然后Opus 4.8与Codex三档大致相当；使用顺位是Opus 5最高、Codex和Opus 4.8 `xhigh`并列次之、Fable少而准、Opus 4.8 `max`只做灾备。Lead跑Opus 5的`max`档，派出去的Claude Worker一律`high`。

实施型任务的派发刀口是**规格闭合度**：spec把输入输出、边界、错误语义、验收全写死的算闭合，在Codex和Opus 4.8 `xhigh`之间轮着派；实现中还要做设计决策的算不闭合，派Opus 5 `high`的Terminal Worker。能用Opus 5干的优先Opus 5。

**Codex的档位不在本文决定。**本文只判复杂度，Codex用Sol、Terra还是Luna由周额度水位决定，见指南"Codex按额度水位选档"。复杂度只在水位紧、需要判断某个任务值不值得越级用更强档时才参与。两个维度分开，避免同一个任务被两套规则指向不同档位。

## 高复杂度

出现任一情况即按高复杂度处理：

- 跨仓库数据契约、权限模型、身份认证、安全或审计。
- 核心架构、数据库迁移、公共协议或不可逆设计决策。
- 业务规则存在关键歧义，错误判断会改变产品行为。
- 多个模块需要统一接口，局部实现可能破坏全局一致性。
- 故障原因不清楚，已有两次以上实施失败。

路由：Lead本人以Opus 5 `max`拥有判断和验收权。任务判定为长期自主调查时向上派出Fable `high`，验收权仍留在Lead；判定按[Session启动指南](session-launch-guide.md)"Fable闸门"的四条判据，满足两条或以上才派；深度只读Review与调研走指南"Fable的两条入口"的范围条件，不用过闸门；同时落在架构决策或数据迁移设计上时按指南的拆段规则处理。需要独立只读意见时使用`orca-opus-reviewer`，需要跑命令验证的调查派`orca-opus-investigator`，过了两条入口之一才用`orca-fable-architect`。明确的编码子任务按规格闭合度派：高复杂度拆出来的实施子任务通常正是不闭合的，默认Opus 5 `high`的Terminal Worker；真正闭合的进Codex和Opus 4.8 `xhigh`的轮换。

## 中等复杂度

常见特征：

- 需求目标明确，但涉及一个完整模块或多个文件。
- 接口已经存在，需要设计局部实现或兼容方案。
- 需要独立Review、测试策略或风险检查。
- 可以限定修改范围和验收条件，但仍需要推理。

路由：

|任务语义|执行者|
|---|---|
|需求澄清、方案比较、风险判断、关键Review、疑难Bug根因|Opus 5，Lead本人`max`做或派`high`的Agent Teams队友|
|规格不闭合的实现：按契约实现但要自己定分层和错误语义、重构要自己决定怎么切|Opus 5 `high`，Terminal Worker|
|规格闭合的实现、补测试、直接修复、批量修改|Codex按水位，或Opus 4.8 `xhigh`，轮着派|
|涉及全局边界的新发现|交回Lead重新判断|

实施行的刀口是规格闭合度，不是"是不是编码"：同一件活spec写透没写透，会给出不同的执行者，而这件事Lead在派发前就知道答案。Lead决定自己读代码、派只读队友还是派能跑命令的调查型队友，判据见[Session启动指南](session-launch-guide.md)的"Agent Teams队友的context"一节。

## 低复杂度

常见特征：

- 单一目标、影响范围小、验收明确。
- 机械修改、测试补齐、文档同步、格式调整或直接Bug修复。
- 不涉及新的业务决策、公共契约或安全边界。

路由：Codex直接完成，档位按水位。水位紧时这一档任务最先降到Luna，它们对推理深度最不敏感。任务很小时由Lead自己完成，避免创建Worker的成本高于工作本身。

## 派发语义与升级

以[Session启动指南](session-launch-guide.md)的"派发语义与升级"一节为准。要点：向下派实施（Codex、Opus 4.8 `xhigh`或Opus 5 `high`）、向上派给Fable、横向派给Agent Teams队友都不转移验收权，只有Worker内部的档位切换才叫升级，任何路径的终点都是Lead。Codex失败不升Opus 4.8，那是平移，交回Lead。横向派里只读Review用`orca-opus-reviewer`，需要跑命令验证的调查用`orca-opus-investigator`。Fable的`xhigh`和`max`各有三条硬门槛，全部满足才能开。

## Worker数量

|独立工作面|Worker数量|
|---|---:|
|没有可并行部分|0到1|
|两个仓库且契约稳定|2|
|实现、测试、独立Review可以分开|2到3|
|四个完全独立模块|最多4|

文件重叠不算独立工作面。只要共享同一接口定义、迁移文件或权限声明，就设置依赖并串行。

## Task契约

每个Task至少包含：

```text
目标：完成后产生什么可观察结果
仓库：唯一主仓库和必要的只读参考仓库
范围：允许修改的目录或文件
依赖：前置Task及使用的接口版本
输入：业务规则、接口、样例和约束
验收：命令、测试、构建或人工检查标准
禁止：不得修改的内容，不得commit或push
回报：修改文件、测试结果、风险和未决问题
```

Task边界不清楚时，Lead先补齐再派发，不把模糊目标直接扔给Worker。
