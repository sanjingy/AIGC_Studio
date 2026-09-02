# 模块路线与依赖计划

> 更新：2026-09-02。依据 [DECISIONS_2026-09-02.md](./DECISIONS_2026-09-02.md) 与 ADR-030 ~ ADR-033。

## 1. 优先级原则

P0 只服务于"每个镜头一条带配音的 MP4"；P1 提高可用性与商业化；P2 扩展专业生态。
任何不在黄金路径上的新页面、新能力不得挤占 P0。四把尺子里只把"产品闭环"变绿：
前端两套壳、工程门禁只修挡住闭环的部分；Skill 运行时、节点画布、`_NEXT` 硬编码、
角色实体化整体冻结。

## 2. 黄金路径

```text
Auth（+ 邮箱验证、找回密码）
  → Project
  → Content + Agent（+ 分集大纲、创意入口）
  → Gateway（+ video / tts 能力、三层模型默认、生成前选模型）
  → Consistency + Image（+ 候选版本）
  → TTS（每镜配音，得到真实时长）
  → Video（每镜 i2v，镜内切段）
  → Mux（ffmpeg：段拼镜 + 混音）
  → Download

Task + Realtime + Billing 横贯每个生成阶段
```

## 3. 模块依赖

| 模块 | 依赖 | 被依赖 |
|---|---|---|
| Auth | core/db | 所有用户侧模块 |
| Project | Auth | Content、Agent、Asset、Task、Billing |
| Content | Project、Agent | Web、Media |
| Agent | Project、Task、Gateway、Billing | Content、Consistency |
| Gateway | Billing Credentials、Provider Adapter | Agent、Image、Video、TTS |
| Consistency / Image | Project、Asset、Task、Gateway、Billing | Media |
| Asset | Auth、Project、Storage | Image、Media、Download |
| Task / Realtime | Project、Redis | 所有异步生产 |
| Billing | Auth、Task、Gateway | 所有付费动作 |
| Media（TTS / Video / Mux） | Content、Consistency、Asset、Task、Gateway、Billing | Download |

## 4. 迭代波次

### Wave 0：文档与收口（2026-09-02 起，一周内）

- 本轮文档：`project_docs/` 成为唯一入口，14 个模块文档按决策记录重写，ADR-030 ~ 033 落定。
- `content` 模块（ADR-029）跑迁移和测试，作为独立 commit 收进来。
- GitHub Actions 增加前端 `typecheck + build`（不等 ESLint）。
- 把现有未提交修改拆成可审查的提交单元。
- **`POST /credits/topup` 收口**：加管理员白名单判定，入账类型改 `ADMIN_GRANT`（W2 发现任何登录用户可自助加 Credits）。
- 镜像加 ffmpeg（api / worker 共用 `aigc-studio-backend`）。

### Wave 1：freeflow 独立闭环 + 删旧壳（ADR-030）

1. 审批门、`revise` 迁入 freeflow（不再跳回 `/projects/{id}`）。
2. 故事 / 角色 / 场景 / 分镜编辑接 ADR-029 的 Patch / 修订历史 / 撤销接口，不再只存组件本地。
3. 任务页改读 `tasks`，按需关联 `agent_runs`。
4. 素材页接正式上传链路。
5. 删 `(app)` 除 `/login` 外全部路由，`/dashboard` 重定向；删 `members` / `servers` / `templates` / `mock-data.ts` / 悬浮助手；Canvas 路由从导航隐藏。
6. 邮箱验证、找回密码。
7. 分集大纲、创意项目入口（`plot_index` 输入放宽）。
8. Playwright 黄金路径：登录 → 新建项目 → 剧本确认 → 分镜确认 → 出图。
9. **文本链路接计费**：`advance` 走 reserve/settle 直连并写 `agent_runs.cost`，加幂等键（W1 发现文本生成全程不计费、不幂等、不受熔断）。
10. **模型解析提前到建任务时**并写进 `input_json`（ADR-024 硬约束 1 当前被违反：预扣按默认模型估价）。
11. 限流（登录、找回密码）与邮箱验证同批。

### Wave 2：M2 逐镜 MP4（最高优先级，ADR-031 ~ 033）

1. `ModelSpec` 重构（第一件事），Gateway 增加 `video_generation` / `tts` 能力，adapter 声明"单段最大时长"；`pricing._shape` 补视频 / TTS 分支。
2. 组织级模型默认表；三层解析；"可选模型 + Credits 估算"接口；前端模型选择按钮。
3. 候选版本：`is_current`、下游记录消费版本、"过期 / 保留当前版"标记。
4. 分镜阶段消费视频模型单段上限，定每镜时长与段数。
5. TTS：每镜台词配音，真实时长回写镜时长。
6. i2v：每段一次调用，段拼成镜；段间首帧衔接方案由 Lead 在实施前定。
7. Mux：ffmpeg 结构化 Render Spec，段拼接 + 混音，产出每镜 MP4。
8. 下载：单镜、批量。
9. 第一条打通用 DashScope 万相 + CosyVoice；Seedance Key 到位后接第二家验证切段逻辑。

### Wave 3：M3 商业化

- 支付订单、回调、退款；用户账单页与运营对账。
- 任务成本、毛利、失败率看板。
- 合规审核、AIGC 标识、实名和风控。
- 整集时间线、字幕、BGM、转场、剪映草稿导出。

### Wave 4：专业化（冻结中）

- Skill 运行时接线与项目内选择；`_NEXT` 硬编码迁到 Skill 编译器。
- 节点画布持久化与执行。
- 角色 / 场景 / 道具实体化（ReelBench 式）。
- 图片编辑（图生图 + 多参考图），Provider 能力扩展。
- ComfyUI Runtime、Node Agent、角色 LoRA。

## 5. 完成定义

一个模块只有同时满足以下条件才标记"已实现"：

- 有明确用户入口和失败提示。
- 数据模型及迁移已落地。
- Service 与 API 契约稳定。
- 异步操作具备任务、取消、重试和幂等。
- 付费动作有估算、预扣、结算和异常释放。
- 关键权限和跨租户场景有测试。
- 文档与实际代码一致。
