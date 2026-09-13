"""本机运行时（试点，ADR 待定，默认关闭）。

用户的 Codex / Claude CLI 登录留在他自己的机器上。桌面连接器
（`apps/local_runner`）主动**出站**连到本服务，拉一条请求，在本地跑官方
CLI，把结果回传。服务端不监听桌面、不开远程 shell、不复制任何登录态。

两种请求：

* **文本** —— 顶替一次 Agent 的模型调用（`agent/llm.py` 逐请求路由）。
* **图片** —— 走 Codex 原生 `image_gen`。产物是真实图片字节，
  由 Worker 交给 `asset` 模块落进项目资产库，与 API 出图同一条落地路径、
  同一张 `tasks` 行、同一套预扣结算。用户在网页上选来源，
  **选了本机就不会再调用付费 API**（失败也不回落）。

视频 / 语音**不支持**，也不打算在这里假装支持：本机三个 CLI 里没有一个
有订阅制的视频或语音生成入口（Gemini CLI 的 Imagen/Veo 是 MCP 外接的
付费 API，不是会员权益）。

不导出 router：包的 __init__ 一旦 import router 就会把 auth.deps 一并拉进
初始化链，撞循环导入（skill 模块踩过同一个坑）。
"""

__all__: list[str] = []
