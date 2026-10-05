# Reelbench 参考工作台实施计划

2026-09-25 · Lead 负责计划、契约与验收；Opus 串行实施。

## 目标与依据

用户要求按 https://reelbench.79px.com 的页面和模块控制规划、逐步复刻。以公开首页及官方 dashboard/storyboard 截图为视觉依据；本次未进入登录后真实工作台，不能把宣传图上的操作当作已验证能力。截图存于 `orca/tasks/reelbench-{dashboard,storyboard}.png`。历史研究 `_research/reelbench_ux_review.md` 仅作辅助，时间差异以当前代码和本次截图为准。

目标是模块导航清楚、对象定位明确、编辑与生成在同一工作区、操作范围可预期。保留 AIGC Studio 品牌、现有 SaaS 权限/计费/生产审核规则。替代暂停的 `UI_REFINEMENT_0925.md` 宽泛美化方案。基线提交 `69f96e1`。

## 设计约定

- 工作台：紧凑项目顶栏 + 72–88px 模块栏 + 230–280px 对象目录 + 弹性主工作区；编辑器可侧开，窄屏改抽屉。
- 参考暖黑底与克制金色强调：背景 #151514、面板 #1D1D1A、悬浮 #262621、边线 #36362D、正文 #EEECE4、强调 #C9AD70。错误/成功保留语义色，核验对比度。
- 中文正文用系统无衬线；标题可用系统宋体作有限对照；正文 14–16px，表格最小 13px。编号只用于真实对象，取消装饰性胶片齿孔和占空间的大标题。
- 空间服务内容：图片可预览，文本可编辑；对象列表紧凑，状态与动作贴近对象。减少无内容的大卡片和重复元信息。
- 导航表示当前查看位置，生产状态单独显示。已知阶段都可查看；推进、审批、付费生成仍走后端限制。

## 分阶段交付

| 阶段 | 可验收结果 | 依赖与边界 | 状态 |
|---|---|---|---|
| P1 分镜工作台样板 | 模块栏、真实节点/镜头目录、分镜总览、筛选、编辑预览、当前范围补图、历史与审核 | 复用现有 nodes/index/node_index；不伪造集归属、时长、视频 | 已完成，f512af0 已部署，用户确认效果 |
| P2 剧本/角色/场景 | 同一壳内按实际集/场、角色、地点选择；主区编辑+图；上传/选资产/生成闭环 | P1 验收；复用内容补丁、提示词、基准图接口 | 已验收（Mock；真实后端闭环留 P4） |
| P3 首页/资产/任务/设置 | 参考创意输入首页、近期项目；资产检索与使用；任务范围/失败恢复；清楚的上游模型控制 | P1 壳稳定；只提供可持久化且实际消费的选项 | 实施中：P3A 首页/资产 |
| P4 整体验收与交付 | 桌面/窄屏、真实 API 闭环、回归、文档、提交推送与服务器更新 | P1–P3 Lead 验收；部署前验证差异与健康检查 | 待实施 |

依赖顺序 P1 → P2 → P3 → P4；每次仅一个实施 Worker，失败返工通过后才推进。每个阶段另建有明确允许文件范围与测试要求的任务书。

## 数据边界与后续能力

当前 StoryboardNode 只有 index/scene_ref/summary，Shot 通过 node_index 关联，没有 episode_id，也没有 duration。P1 按“节点 → 镜头”真实展示，并提供未归属分组；不把场景或节点序号猜成集号。剧本本身已有 episodes，P2 可按真实剧本集导航。

参考站的按集补分镜、节奏时长、视频生成需要独立契约工作。列为后续能力批次，先定义稳定集关联与旧数据兼容，再做作用域生成/合并/版本冲突控制。时长遵从现行 ADR 的真实音频语义；没有真实音频不展示虚构秒数。视频、TTS、ComfyUI 不是此次 UI 交付的暗含完成项，缺服务不加伪入口。若实施要改变生产规则或 schema，先交 Lead 写 ADR 并另派后端任务。

## 每轮验收

1. 设计草案与参考截图对照：布局、密度、动作位置、可读性；不以“更有电影感”代替标准。
2. 浏览器 1440×900、1280×800、390×844 截图；键盘焦点、目录切换、长文本、空状态、错误、加载均可用。
3. 关键行为：非连续镜号编辑原始数组位置正确；筛选不影响对象身份；未保存切换保护；补图准确范围且已有图/运行中不重复提交；失败可恢复。
4. 数据刷新后仍正确；平台/本机选择、模型配置、提示词预览、内容历史、上传原有能力无回退。
5. 前端 typecheck/lint/build 与必要行为测试。若后端修改，跑 ruff/format/mypy/pytest 与迁移验证。浏览器 Mock 验证必须标明，不冒充真实 Provider 测试，不为验收擅自付费生成。
6. Worker 不 commit/push/deploy、不改密钥或用户 `.claude/settings.local.json`。Lead 审核后统一交付。

## 执行记录

- 新建 Orca Run：`run_c9bf8031e1cf`。
- P1 Task：`task_4894245495d2`；Dispatch：`ctx_c5443c3397f9`；Worker：`term_167b8c19-5a46-48ba-b6d2-e66c93c6d2b8`，终端已显示 Opus 5 / 1M / high，不能据此宣称为 5.5。
- 旧二次美化任务继续保持停止，新参考任务从基线开始。
- 2026-09-26 恢复：原 Worker 终端丢失，旧 Dispatch 已失败；任务被恢复为 ready。接回原 Run，启动新 Dispatch `ctx_bbf87bd0f6ca` / Worker `term_b77844de-7563-4a80-89dc-8c9f281e6aa6`。启动时任务输入未实际到达，经补送任务说明后，Worker 已核实身份并开始读取资料；以更新后的 ack、设计稿、代码与验收报告判断实际进度。
- 2026-09-27 接续 P2A：task_e299df6b71d2 / ctx_76833509f154 / term_778c9f49-090e-426b-ac26-5580094c158a。上轮菜单字符 2 被误读为新需求而停顿；已澄清并恢复原任务。P2B 与 P3 范围草案已准备，等待前置验收。
- 2026-09-27 用户指定详细实现交 Opus 5.5：在现有 P2A 会话内切换为 claude-opus-5-5[1m]，CLI 确认 Opus 5.5（1M context）。保留代码与上下文，继续修复审阅 A–E 并完成测试；Lead 负责范围与验收。
- 2026-09-28 接续浏览器验收：旧会话上下文触顶，ctx_76833509f154 已撤销执行权并保留交接；新 Task task_abbfd6b35180 / Dispatch ctx_b987f1558305 / Terminal term_30cd684a-5db4-4dd0-abbf-40c7fa3c63dd，启动参数确认 claude-opus-5-5[1m] high。仅接续 P2A 剩余验证与修复，尚未验收交付。
- 启动器首次未送达任务（把启动参数当输入），已撤销 ctx_b987f1558305 并复用就绪会话重新派发，当前有效 Dispatch 为 ctx_a8dcf408b027。

- 2026-10-03 Lead 交接：原 Codex Lead（term_92a57f07）不再运行，Run 改由 Claude Lead `term_e9912ee2-c109-4152-8859-64b4613fddb5` 绑定（`run-use`）。P2B 首个 Task `task_2778f2ae7332` 因上下文触顶交接为 failed：代码、逻辑测试、typecheck 已完成，浏览器从未运行。续做 Task `task_45ab2bea6be8` / Dispatch `ctx_85db5feeb3e6` / Terminal `term_b997f2dc-8c46-4fdd-a501-21342121788d`，启动参数 `claude-opus-5-5[1m] high`，任务书 `orca/tasks/REELBENCH_P2B_FINISH.md`。
- 2026-10-03 P2B 验收通过（Lead 复核）：Mock 浏览器验收 129/129，P2A 回归 96/96 + 导航 19/19，P1 回归 50/50，逻辑测试 37/37、typecheck、独立 build 通过；Lead 重跑逻辑测试/typecheck/`git diff --check` 并抽看截图。证据 `orca/tasks/REELBENCH_P2B.report.md`。仅 Mock，未做真实后端/Provider 闭环，出图完成路径未覆盖。
- 同日返工 P2A：Worker 发现撤销后表单仍保留旧草稿（再保存会把撤销的改动写回），P2B 已修，P2A 的 `fields-editor` / `scene-editor` 同样写法，Lead 读码确认。Task `task_c7357e072ea9` / Dispatch `ctx_bb5269cfaee2`，派回同一终端。修完后进入 P3。
- 2026-10-03 P2A 撤销刷新返工验收通过：Worker 先在浏览器复现（剧本字段与场编辑器各一次），修法是把 `rebaseDraft` 抽到 `lib/freeflow/draft-rebase.ts` 共用；逻辑测试 38/38、P2A accept 98/98（新增 2 条表单断言）、导航 19/19、P2B 129/129。Lead 重跑逻辑测试、typecheck、独立 build（exit 0）与 `git diff --check`。Worker 终端 `term_b997f2dc` 已回收。P2 阶段完成，下一步 P3。
- 2026-10-03 P3A 首页与资产派发：Task `task_640de70d611c` / Dispatch `ctx_31d285fb7b7d` / Terminal `term_28df7646-71f5-4ebb-ac99-c81b4522533c`，`claude-opus-5-5[1m] high`，任务书 `orca/tasks/REELBENCH_P3A.md`。不改后端；需要后端的点只记录。P3B 任务与设置排在 P3A 验收之后。
- 2026-10-03 负责人授权提交推送部署：P2 提交 `d4c6a05` 并推送；香港测试机 `/root/aigc_deploy.sh` EXIT=0（08:18 服务器时间），HEAD=d4c6a05，healthz/readyz/login 200，`/freeflow` 与真实项目的 story/characters/scenes/storyboard 均 200，api/worker 重建后 healthy。后端无改动，未在服务器跑 pytest。部署前已观察到同机饥荒游戏进程不在运行（面板与中继端口在），非本次所致，未触碰。
- 2026-10-03 P3A 首个会话上下文触顶交接（`ctx_31d285fb7b7d` 已 fence，终端已回收）：首页两动作、资产浏览器完成，逻辑测试 45/45、typecheck、Mock 浏览器 104/104；Lead 复核 45/45 与 typecheck。续做 Task `task_f8cfa2dfc486` / Dispatch `ctx_ffdb51bf1b5e` / Terminal `term_88dd95d3-dec0-49aa-a27e-7acb61b92886`：独立 build、P1/P2 回归、MASTER、报告。
- 2026-10-05 负责人提出模型设置改为多厂商（类似 CC Switch）。调研见 `orca/tasks/MULTI_PROVIDER.research.md`：无可整块引入的开源模块，决定按协议扩展自有适配器 + 自维护预设，记为 **ADR-039**（取代 ADR-031 第 5 条"一个文本自定义端点"）。负责人确认：文本+出图、用户自带 Key、常用预设+自定义。P3B 范围收窄为任务页（模型设置移出，等 A2）。
- 同日派 A1 后端：Task `task_cad664e7994e` / Dispatch `ctx_a749b6d3f0b6` / Terminal `term_2a67030c-18c0-4d6f-9ccc-83b87662f9f8`。Claude Code 升级到 v2.1.289 后启动会先弹 bypass 确认页，`terminal create` 因此超时但终端实际已起；`worker-start --agent claude` 会把参数当聊天消息（`ctx_588b9cf00bbe` 作废）。
- 2026-10-05 P3B 任务页验收通过（Lead 复核）：逻辑测试 56/56、typecheck、独立 build；Mock 浏览器 P3B 75/75，回归 P3A 104/104、P2B 129/129、P2A 98/98、nav 19/19、P1 50/50（均为当晚收尾后重跑）。证据 `orca/tasks/REELBENCH_P3B.report.md`。模型设置按 ADR-039 移出，由 A2 做。P4 打磨项：失败行同时显示了内部错误码（如 `provider.transient.timeout`），应只给用户可读文案。需要后端：TaskOut.retryable、组织级 SSE、Last-Event-ID 补发。
- 2026-10-05 ADR-039 A1 后端验收通过（Lead 复核）：ruff/format/mypy 通过，全量 pytest 1308 passed / 2 skipped（基线 1209/2），迁移 `b9e4c1a7d203` 往返通过。证据 `orca/tasks/MULTI_PROVIDER_A1.report.md`。A2 前端设置页与 A3 后端补三项（出图钉住上游、推理模型标记、连接引用查询）并行中。
- 2026-10-05 ADR-039 A3 后端补三项验收通过（Lead 复核 pytest 1322 passed / 2 skipped）：出图任务按 `input_json.upstream` 钉住连接执行、连接模型 `reasoning` 标记与 no_reasoning 角色拒绝、`GET /model-config/connections/{id}/references`。
- 2026-10-06 ADR-039 A2 前端模型设置页验收通过（Lead 复核）：逻辑测试 66/66、typecheck、独立 build；Mock 浏览器 A2 69/69，真实本地后端接口冒烟 13/13；回归 P3B 75/75、P3A 104/104、P2B 129/129、P2A 98/98、nav 19/19、P1 50/50；响应体与 DOM 无明文 Key。证据 `orca/tasks/MULTI_PROVIDER_A2.report.md`。**A 批全部完成。** 未提交：P3A、P3B、A1/A3 后端、A2 前端。B 批（其余出图协议 + 一致性实测）与 P4 待派。
