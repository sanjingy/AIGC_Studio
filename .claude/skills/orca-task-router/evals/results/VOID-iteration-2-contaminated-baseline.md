# iteration-2 作废,不要用它的数据

## 怎么发现的

eval-11 的 without_skill agent 在最终回复里自己报了出来:它写plan之前读了同目录的
`eval_metadata.json`,那里面直接写着 `expected_output` 和9条断言。它明说这一条基线
"不能当作干净基线用"。

## 范围

拿 `eval_metadata.json` 去 grep 41 个已记录 arm 的 transcript,24 个读过答案:

| | 两侧都干净 | 至少一侧看过答案 |
|---|---|---|
| 用例数 | 5 (eval-2/6/10/16/18) | 16 |

一对里只要有一侧看过答案,这一对的对照就没有意义。5/21 的可用率不值得抢救,
而且抢救出来的那几对跟重跑的那些环境不一致,拼在一起也不是一份能看的基准。

## 根因

是我铺 workspace 时的错,不是 agent 的错。我把 `eval_metadata.json` 和 `grade.md`
放进了 `eval-<n>-<name>/` 目录,而 runner 的 task.md 恰好指向同一个目录下的
`<config>/outputs/`。agent 为了确认输出目录存在会去 list 父目录,一 list 就看见答案。
skill-creator 的官方布局里 `eval_metadata.json` 确实在用例目录下,但官方的 runner 是
`run_eval.py` 起的独立进程,拿不到这个目录;我用 Agent 工具手起,agent 有完整文件系统
权限,官方布局的隐含前提在这里不成立。

## iteration-3 怎么改的

- 答案挪到 `_answers/<eval-name>.json`,评分任务挪到 `_grading/`,两者都在 `eval-*/` 外面
- runner 只看得到 `eval-<n>-<name>/<config>/run-1/`,里面只有 task.md 和空的 outputs/
- task.md 加了一段边界约束:不许往上翻父目录,撞见写着预期结果的文件立刻停下并报路径
- 官方 aggregator 要的 `eval-*/eval_metadata.json` 等全部跑完之后再从 `_answers/` 拷回去

留着这个目录是当反例存档。这类污染不会报错,只会让基准悄悄变好看。
