# Agent 内容质量用例（schemaless 回退位）

这个目录是 `tests/unit/test_agent_eval_required.py` 的**回退判据**所在。

## 什么时候需要往这里放东西

绝大多数 Agent 都声明了 `output_schema`。对这类 Agent，"有没有 eval 覆盖"
的硬门槛是：它的 `output_schema` 必须在 `tests/eval/eval_suite.py` 的
`SCHEMA_SAMPLES` 里有一份能通过 Pydantic 校验的最小样例（结构化输出回归）。
**这类 Agent 不需要往本目录放文件。**

只有当一个 Agent **不声明 `output_schema`**（例如未来某个产出二进制资产、
而非 JSON 的 media/TTS Agent）时，schema 样例这条路走不通，它必须在本目录
放一份 `*.eval.yaml` 内容质量用例文件，否则 `test_agent_eval_required.py`
会判它"缺 eval 覆盖"而变红。

## 文件长什么样

放在本目录（`tests/eval/agent_evals/`，随 `tests/` 一起被 compose 挂载，
容器内可见），文件名任意但必须以 `.eval.yaml` 结尾，顶层是一个映射：

```yaml
agent: media.tts.v1        # 必填：这份用例覆盖哪个 Agent id
cases:                      # 必填：至少一条用例
  - name: 基本朗读
    input: "你好，世界。"
    expect_nonempty: true
```

`test_agent_eval_required.py` 只校验**这份文件存在、能解析、`agent` 对得上、
`cases` 非空**——它证明"这个 Agent 的提示词被纳入了回归用例集"，
不在单元测试里跑 LLM-as-judge 打分（那属于 `22_AgentEval.md` §2.3，需要真实
Provider、有成本、非确定性，不进 CI 单测范围）。
