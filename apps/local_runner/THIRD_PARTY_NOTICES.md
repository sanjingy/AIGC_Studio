# 第三方来源说明（apps/local_runner）

本目录下 `appserver.py`、`imagegen.py`、`process.py`、`adapters.py` 的
**若干设计**参考了 Orca 的实现。不是复制文件——Orca 那边是 TypeScript /
Electron 主进程，这里是 Python，复用的是**算法与边界条件**（分帧、握手形状、
请求关联、逐请求超时、子进程死亡时的在途请求处置、参数白名单、进程树拆除、
子进程环境构造）。

四个文件的对应关系是明确的，早先那句"`appserver.py` 与 `imagegen.py`"
把范围写窄了——下面列的参考文件里，进程树拆除对应的是 `process.py::_kill_tree`
（Windows 走 `taskkill /T /F`、POSIX 走 `killpg` + `start_new_session`），
子环境构造对应的是 `adapters.py::child_env`，这两处都不在那句话覆盖的范围里。

## 来源

| 项 | 值 |
|---|---|
| 项目 | Orca —— https://github.com/stablyai/orca |
| 许可 | MIT License |
| 版权 | Copyright (c) 2026 Lovecast Inc. |
| 固定来源 commit | `d53cbed43f48179313d40811aa9b3330a44f0a46` |
| 参考日期 | 2026-09-07 |

### 参考的具体文件

```
src/main/codex/codex-app-server-jsonl.ts              JSONL 解析：失败返回 null 而不是抛
src/main/codex/codex-app-server-record-reader.ts      增量分帧 + 背压
src/main/codex/codex-app-server-connection.ts         spawn / 握手 / 请求关联 / 逐请求超时 /
                                                       子进程死亡时失败全部在途请求
src/main/codex/codex-app-server-handshake.ts          initialize 的参数形状与 15s 超时
src/main/codex/codex-app-server-session.ts            超时 / 不支持 两类错误的边界
src/main/codex/codex-app-server-process-teardown.ts   进程树拆除
src/main/codex/codex-app-server-posix-supervisor.ts   POSIX 进程组监督与"证明组已空"
src/main/codex/codex-structured-app-server-args.ts    CLI 参数白名单 + 可见报错
src/main/codex/codex-structured-child-environment.ts  子环境构造（CODEX_HOME 注入）
src/main/codex/codex-structured-turn-start.ts         turn/start 输入形状与 turn id 竞态
src/main/codex/codex-app-server-capability-signal.ts  "RPC 面不存在"的判据
```

### 参考点落到本仓库的哪一处

| Orca 文件 | 本仓库对应物 |
|---|---|
| `codex-app-server-jsonl.ts` / `-record-reader.ts` / `-connection.ts` / `-handshake.ts` / `-session.ts` / `-capability-signal.ts` | `appserver.py` |
| `codex-structured-turn-start.ts` | `imagegen.py`（`start_turn` 的 turn id 竞态） |
| `codex-app-server-process-teardown.ts` / `-posix-supervisor.ts` | `process.py::_kill_tree` |
| `codex-structured-child-environment.ts` | `adapters.py::child_env` |
| `codex-structured-app-server-args.ts` | `appserver.py::build_app_server_argv` |

## 本地改动（与 Orca 不同的地方，逐条列出）

1. **语言与并发模型**：Python + `asyncio`，不是 Node 的 EventEmitter。
   分帧改成 `asyncio.StreamReader` 逐行读取。
2. **不做**：多账号池、托管 CODEX_HOME 生命周期、自动信任 hooks、遥测、
   工作区/IDE 概念。这些是 Orca 的产品功能，与本试点无关。
3. **握手 clientInfo** 改成本项目自己的名字，不冒充 Orca。
4. **新增 Orca 没有的东西**：`imagegen.py` 整个模块。
   Orca 只把 `imageGeneration` 映射成一句界面提示语
   （`src/main/native-chat/agent-session-wire/provider-frame-activity.ts:71`），
   **不消费 `savedPath`、不落资产**。图片落地这一段没有可抄的实现。
5. **`result` 的解码**依据的是 OpenAI 官方 Codex 源码而非 Orca：
   `codex-rs/core/src/tools/handlers/extension_tools.rs` 的
   `image_generation_publication_preserves_extension_saved_path` 用例里
   `result: "cG5n"`（`base64("png")`），据此确定 `result` 是**图片字节的 base64**。
6. **`executor.py` 是我们自己的**：Orca 没有对应物。它把"一台本机 CLI 能干什么、
   怎么干"收成一个接口（Codex 走 app-server，Claude 走 headless `-p`），
   并且**能力是探出来的**（`codex features list` 里 `image_generation` 为 true
   才算数），不是声明的。
7. **超时预算是整条请求共享的**：握手 / `thread/start` / `turn/start` / 等产物
   从同一个 deadline 里扣。Orca 那边是每步各有上限（它没有"服务端下发剩余时间"
   这个约束，我们有）。
8. **打断带两个 id**：`turn/interrupt` 按官方 schema 的 `required` 传
   `{threadId, turnId}`。第一版只传了 threadId，打断从未真正生效。

## 协议本身的来源

Codex app-server 协议 schema 由**官方命令**生成，不是逆向：

```
codex app-server generate-json-schema --out <dir>
codex app-server generate-ts          --out <dir>
```

本机核对版本：`codex-cli 0.153.4`。

## MIT License 全文

重写而非复制的情况下，MIT 并不强制附上全文；这里仍然附上，因为成本是零
而争议是零。上面那个 commit 的 `LICENSE` 原文：

```
MIT License

Copyright (c) 2026 Lovecast Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
