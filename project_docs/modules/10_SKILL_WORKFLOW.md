# 10 Skill 与工作流

> 状态：**部分实现**（声明格式、白名单校验、上传与隔离已通；**运行时未接线**）
> 优先级：**P2 / 冻结**（决策记录 §9："Skill 运行时 / Canvas / `_NEXT` 硬编码整体冻结"）
> 负责人：待定
> 最近核对：2026-09-02
> 权威顺序：[DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §9 / §2 > ADR-020 / 026 / 030 > 当前代码

---

## 1. 模块目标与边界

用一份**声明式、可版本化、不可执行**的 YAML 描述一条生产流程，
让项目可以选择不同的生产路线（ADR-020）。

**Skill 是生产配方，不是服务器插件。** 第三方只能上传数据声明，
不能上传 Python / JS。这条是安全边界，不是风格偏好——
允许上传代码等于把服务器交出去。

**Canvas（节点画布）是 Skill / 执行计划的可视化编辑器**，
不是执行状态的真相源。按 ADR-030 第 5 条**搁置**：路由从导航隐藏、
代码保留、不接后端。

**本模块整体冻结。** 下面的"当前真实能力"仍然准确、仍然要维护，
但§4 里不存在 P0 与 P1 的 Skill 运行时需求，§9 的迭代计划是
"冻结，M2 之后再评估"。

---

## 2. 用户与使用场景

今天真实成立的只有两条：

1. 我传一份自己的 Skill YAML 上去，**立刻知道它合不合法、哪一行错了**。
2. 我在技能库里看到我传过什么，并且**看到一句实话**：运行时还没接。

不成立的（因此界面上必须说清楚）：选了它就按它跑。
`projects.selected_skill_id` 是真列，但编排器不读它。

---

## 3. 当前真实能力

状态词按 [DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §0。

| 能力 | 状态 | 代码 / 测试证据 |
|---|---|---|
| 内置 Skill `skill.novel_to_anime.v1`（26 阶段 + 5 道门） | 已实现 | `skills/builtin/novel_to_anime.yaml` |
| Registry：builtin + custom 双目录、热加载、按路线取默认 | 已实现 | `skills/registry.py`（`custom` 优先于 `builtin`） |
| YAML → 严格 Pydantic IR 的 schema 校验 | 已实现 | `skills/spec.py`；`tests/unit/test_skill_spec.py` |
| 处理器白名单（14 个 `KNOWN_HANDLERS`） | 已实现 | `skills/spec.py::KNOWN_HANDLERS`；未注册的处理器直接判非法 |
| 导出路径按段白名单、门的全集收敛、重试上限约束 | 已实现 | `skills/spec.py` |
| 上传 Skill 存 `org_skills`，按 `org_id` 隔离 | 已实现 | `apps/api/modules/skill/`；`tests/integration/test_skill_upload.py` |
| **校验不过也 201 入库**（`status=invalid` + 错误原文） | 已实现 | `skill/service.py`——丢掉它用户只会得到"传了没反应"的黑洞；真失败只有"根本不是 YAML"（400） |
| 上传**不落** `skills/custom/` | 已实现 | 那个目录是进程级注册表，写进去等于对所有租户生效 |
| 四条接口：列表 / 上传 / 取原文 / 软删 | 已实现 | `skill/router.py` |
| `runtime_wired` 由后端给，前端不写死文案 | 已实现 | `skill/schemas.py:44`（恒为 `False`）；`asset-library-grid.tsx:270`、`skill-upload.tsx:171`、`shell/sidebar.tsx:229` |
| `model_policy.user_selectable` 驱动"哪些能力用户可改" | 已实现 | 被 `gateway/router.py` 与 `billing/credentials.py::configurable_capabilities` 消费 |
| **Skill 运行时** | 未实现 | 阶段图硬编码在 `agent/orchestrator.py::_NEXT` / `_SPEC_OF` / `_GATE_OF`，与 Skill 声明的顺序一致但没有任何代码从 Skill 读它 |
| 项目内选择 Skill 并生效 | 部分实现 | `projects.selected_skill_id` 有列、可写；**编排器不读**（ADR-026 明写不做） |
| Skill 编译成不可变 plan 快照 | 未实现 | 无编译器、无快照表 |
| Skill 版本与发布状态 | 未实现 | `org_skills` 有 `version` 字段但没有版本语义（同名同版本再传是新行） |
| Canvas 节点画布 | 部分实现 | `components/freeflow/canvas/`（12 个文件，`@xyflow/react`）；图存 `localStorage`、节点来自 `lib/freeflow/mock-data.ts`、**不接任何后端** |
| Canvas 图持久化 / 编译 / 执行 | 未实现 | — |
| ComfyUI Runtime / Node Agent / AutoDL | 仅设计 | ADR-003 / 004 / 007，`06_ComfyUIAndNode.md`、`07_AutoDL.md` |

---

## 4. 功能需求

### 4.1 P0

**没有。** 逐镜 MP4 这条链路一步都不经过 Skill 运行时——
生产流程走的是 `orchestrator._NEXT` 那张硬编码的图，它已经能跑通。

### 4.2 P1

只有一条，而且它**不是 Skill 运行时**，是删旧壳时别把已有能力弄丢：

- **FR-SKILL-020：Skill 上传入口要在 freeflow 里有落点。**
  当前唯一的上传入口是 `useSkillUpload()`，被
  `components/project-composer.tsx`（旧壳对话框的「+」附件菜单）和
  `components/shell/sidebar.tsx` 引用，**这两个文件都在 ADR-030 的删除清单里**
  （见 [11_WEB_WORKBENCH.md](./11_WEB_WORKBENCH.md) §4）。
  删完之后：
  - 用户再也没有办法上传 Skill；
  - `asset-library-grid.tsx:98` 的空状态文案还在说
    "在对话框的「+」附件菜单里可以传一份 YAML"——指向一个不存在的入口。

  两条出路（**待 Lead 定**，见报告）：把 `skill-upload.tsx` 迁到
  `/freeflow/assets` 的技能库 chip 上；或者接受"上传功能随冻结一起下线"，
  同时改掉那句空状态文案。**不能什么都不做**——留着一句指向不存在入口的话
  是最坏的选项。

### 4.3 P2 / 冻结（决策记录 §9）

以下全部**冻结，M2 之后再评估**，不排期、不进 Wave：

| 项 | 说明 |
|---|---|
| Skill 编译器与 IR | 把 YAML 编译成不可变 DAG plan |
| 项目选中 Skill 后按它执行 | 需要先有编译器 |
| `orchestrator._NEXT` 迁到 Skill 编译器 | 决策记录 §9 点名冻结 |
| plan 快照（运行中的项目不受 Skill 后续编辑影响） | 依赖编译器 |
| Skill 版本与发布状态 | 依赖运行时才有意义 |
| **Canvas 图持久化 / 编译 / 执行** | ADR-030 第 5 条搁置 |
| Canvas 与 YAML 共用同一份 IR | 同上 |
| Skill 市场、权限、依赖解析 | 更远 |
| ComfyUI Runtime / Node Agent / AutoDL | ADR-003 / 004 / 007，M4 之后 |

**冻结的含义是"不写新代码"，不是"文档可以撒谎"。**
`runtime_wired` 的诚实标注（ADR-026 的验收标准）在冻结期间**继续有效**，
且必须由后端给、不能是前端写死的文案。

---

## 5. 生命周期（目标形态，冻结中）

```text
upload YAML
  → parse（不是 YAML 才 400）
  → schema + 白名单校验
  → invalid（入库，带错误原文）/ valid
  → 项目选择某个版本
  → 编译成不可变 plan          ← 冻结线在这里
  → Director 按 plan 推进，经 Task / Gateway 执行
```

今天走到"入库 + 可选"为止。冻结线以下一行代码都没有。

---

## 6. 数据模型

### 6.1 已有

`org_skills`：`name`、`version`、`spec_yaml`、`status`（valid / invalid）、
`validation_errors`、`uploaded_by`，按 `org_id` 隔离，软删除。

`projects.selected_skill_id`：指向 `org_skills.id`，**不加外键**——
跨模块加外键会把两个模块的迁移绑死（ADR-009 的模块边界同样适用于数据库约束）。

进程级注册表：`skills/builtin/` + `skills/custom/`，热加载，
`custom` 同路线时优先。**用户上传的东西不进这两个目录。**

### 6.2 冻结中不建

不可变版本表、编译产物快照表、Canvas graph schema、能力依赖解析结果。

---

## 7. API 与前端入口

```text
GET    /api/v1/skills            本 org 传过的（含 runtime_wired）
POST   /api/v1/skills            传一份 YAML（校验不过也 201）
GET    /api/v1/skills/{id}/spec  取回原文
DELETE /api/v1/skills/{id}       软删
```

**前端入口的真实位置与 ADR-030 的描述不一致，需要注意**：

- ADR-030 第 4 条写"`skills` 页保留，它诚实标注了运行时未接线"。
  但 `app/freeflow/(global)/skills/page.tsx` 实际是一个
  `GlobalPlaceholder`（"此功能尚未接入"），**不是技能库**。
- 真正的技能库列表在 `/freeflow/assets` 的「技能」chip 里
  （`asset-library-grid.tsx`，读 `orgSkills.list()`），
  "运行时尚未接线"那句话也在那里。
- 所以"保留 skills 页"这条如果按字面执行，保留下来的是一个占位页。
  **建议**：要么把技能库真的搬到 `/freeflow/skills`，
  要么把这个占位路由与 `members` / `servers` / `templates` 一起删掉，
  技能库就留在资产库的 chip 里。**待 Lead 定。**

---

## 8. 技术与工程设计（现有约束，冻结期间不变）

- **YAML 只是编辑格式**，进程内一律先解析成严格 Pydantic IR 再用。
- **`handler` 只能取白名单里的**（14 个）。它是"任意能力"的入口，
  放开等于让声明文件调用平台内部函数。
- **`export` 路径按段白名单校验**，禁止路径穿越——
  它会往用户磁盘写文件，不校验就是任意位置写入。
- **阈值、废片率不写进会被分发的 YAML，只写键名。**
  数字冻进发布物，改一个数就要发一次版。
- **校验直接复用 `skills/spec.py`，不为上传新增豁免。**
  那套白名单本来就是为不可信输入写的。
- **上传的 Skill 只进 `org_skills`，按 `org_id` 隔离。**

---

## 9. 迭代计划

**冻结，M2 之后再评估。**

冻结期间只做两件维护性的事：

1. 保持 `runtime_wired` 的诚实标注有效（ADR-026 验收标准）。
2. 删旧壳时处理好上传入口与那句空状态文案（FR-SKILL-020，P1）。

解冻的判据不是"有空了"，而是**逐镜 MP4 已经跑通、且 `_NEXT` 这张图
开始因为多路线而频繁改动**。在那之前固化阶段图只会白改两遍——
这正是 ADR-026 当初不做运行时的理由，它没有变。

---

## 10. 模块依赖

- **依赖**：01（`org_id`）、02（`selected_skill_id`）。
- **被依赖**：05 Gateway（读 `model_policy.user_selectable` 决定哪些能力
  用户可改 / 可配 Key）、09 Billing（同一份声明决定 BYOK 能配哪些能力）、
  11 Web（技能库与上传入口）。

**注意这条依赖是真的**：`gateway/router.py::_declared_capabilities()` 与
`billing/credentials.py::configurable_capabilities()` 都在读 Skill 的
`model_policy`。所以 Skill 模块虽然运行时冻结，
**它的声明仍然在影响线上行为**，改 `novel_to_anime.yaml` 的
`user_selectable` 会直接改变设置页能配哪些 Key。

---

## 11. 当前缺口与风险

1. **用户能传但不能跑**，最容易造成误解。诚实标注是唯一的防线，
   三处标注（技能库、上传回执、旧壳侧栏）删旧壳时会掉一处，
   要确认剩下的还在。
2. **上传入口即将随旧壳消失**（FR-SKILL-020）。
3. **`/freeflow/skills` 是占位页**，与 ADR-030 的表述不符（§7）。
4. **Canvas 是 `localStorage` + mock 数据**。ADR-030 决定隐藏路由、
   保留代码——但 `mock-data.ts` 同时被 `workflow-canvas.tsx` 和
   `home-template-grid.tsx` 引用，删 mock 会连带影响 Canvas 能不能打开
   （见 [11_WEB_WORKBENCH.md](./11_WEB_WORKBENCH.md) §4.3）。
5. **硬编码阶段图与 Skill 声明并存**，两边顺序现在一致，但没有任何机制
   保证它们继续一致。改 `_NEXT` 时没人会想起去改 YAML。
   冻结期间可接受，解冻时第一件事就是消掉这份重复。

---

## 12. 验收标准和测试

**已可验收（现在就该绿）**

- 任意上传的声明**不能执行任意代码**，也不能往白名单之外的路径写文件。
  （`tests/unit/test_skill_spec.py`、`tests/unit/test_agent_spec_sandbox.py`）
- 未注册的 `handler`、越界的 `export` 路径、超上限的重试配置一律判非法。
- 校验失败的 YAML 仍然入库为 `invalid` 并带回错误原文；
  只有"根本不是 YAML"返回 400。（`tests/integration/test_skill_upload.py`）
- 上传的 Skill 只对本 org 可见，不写进 `skills/custom/`。
- `runtime_wired` 恒为 `False` 且由后端返回，前端据它渲染提示。

**冻结期间新增的唯一一条**

- 删旧壳之后，技能库页面上关于"怎么上传"的文案指向的入口**真实存在**；
  如果决定下线上传，则页面上不再出现任何上传指引。

**解冻后才需要验的**（现在不做）

- 项目运行可追溯到具体 Skill 版本与编译后的 plan。
- Skill 更新不改变已运行项目的计划。
- Canvas 与 YAML 对同一流程编译出一致的 IR。
