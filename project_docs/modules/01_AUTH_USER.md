# 01 用户、登录与组织

> 状态：**部分实现**（注册 / 登录 / 会话闭环，邮箱验证与找回密码完全没有）
> 优先级：**P0**（外部创作者第一天就会撞上找回密码）
> 负责人：待定
> 最近核对：2026-09-02
> 权威顺序：[DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §6 / §1 > 当前代码 > ADR

---

## 0. 2026-09-05 发现的一个安全小洞（未修）

**登录表单在 hydrate 完成前按回车，会以原生 GET 提交，把密码写进 URL**
（`/login?email=...&password=...`）。密码因此进入浏览器历史与 Referer。
hydrate 之后行为正常，所以只有在页面出现后的几百毫秒内提交才会触发，真实用户很难撞上，
但它是一个真实的凭据泄漏路径。
**修法**：给表单加原生兜底（`method="post"` 指向自身），或提交按钮在 hydrate 完成前禁用。

---

## 1. 模块目标与边界

给所有用户侧资源提供身份、会话和租户上下文。产品是**托管的多租户 SaaS**，
不是自托管工具（决策记录 §1），所以"每个请求都能问出 `org_id` 是谁"是
这个模块唯一不可让步的职责。

**拥有**：`organizations`、`users`、`refresh_tokens`、`user_preferences`，
以及将来的邮箱验证令牌与密码重置令牌。

**不拥有**：Credits（模块 09）、项目与资产（模块 02 / 07）、
业务权限规则。其他模块只保存 `user_id` / `org_id`，
**不得复制用户状态或任何密码材料**。

---

## 2. 用户与使用场景

用户是外部创作者，自己在网上注册，没有客服帮他重置密码。四个场景：

1. 用邮箱 + 密码 + 昵称注册，立刻拿到体验 Credits，开始建项目。
2. 登录后在浏览器里保持会话，Access Token 过期无感刷新。
3. **忘了密码**——自助找回，这是外部用户与内部试用用户最大的差别。
4. 退出；或系统发现 Refresh Token 被重放，强制整族下线。

注册即建一个 personal organization，个人用户也不例外。
不做特例的理由：做团队版时如果个人用户没有 org，整套权限模型都要打补丁。

---

## 3. 当前真实能力

状态词按 [DECISIONS_2026-09-02.md](../DECISIONS_2026-09-02.md) §0。

| 能力 | 状态 | 代码 / 测试证据 |
|---|---|---|
| 注册：同一事务建 org + owner 用户 | 已实现 | `auth/service.py::register`；`tests/integration/test_auth_flow.py::test_register_creates_session_and_org` |
| 注册后发放体验 Credits（幂等、走 `ADMIN_GRANT` 且标 `is_bonus`） | 已实现 | `billing/service.py::grant_welcome_credits`；`test_billing_ledger.py::test_welcome_grant_is_not_repeated` |
| 邮箱规范化（去空白 + 转小写）、软删后可重新注册 | 已实现 | `auth/schemas.py::_normalize_email`；`auth/models.py` 的部分唯一索引 `uq_users_email_active` |
| 密码强度：≥8 位且不得纯数字 / 纯字母 | 已实现 | `auth/schemas.py::RegisterIn._strength`；`test_weak_passwords_rejected` |
| Argon2 哈希 | 已实现 | `auth/security.py` |
| 登录：未知邮箱也执行虚拟哈希，错误与密码错一致 | 已实现 | `test_unknown_email_gives_same_error_as_wrong_password` |
| Access / Refresh 走 httpOnly Cookie，令牌不进响应体 | 已实现 | `auth/router.py::_set_cookies`；`test_cookies_are_httponly`、`test_tokens_never_appear_in_response_body` |
| Refresh 只存哈希、每次轮换、令牌族 + 重放检测 | 已实现 | `auth/service.py::refresh`；`tests/integration/test_auth_token_reuse.py`（5 例） |
| 登出撤销当前族并清 Cookie | 已实现 | `test_logout_invalidates_refresh` |
| 每个请求注入 `CurrentUser`（含 `org_id`），支持 Cookie 与 Bearer | 已实现 | `auth/deps.py` |
| 跨租户资源一律 404 | 已实现 | `tests/integration/test_tenant_isolation.py` |
| 生产环境 Cookie `Secure`（`is_production` 时开） | 已实现 | `auth/router.py:22` |
| 前端登录 / 注册页 | 已实现 | `apps/web/app/(auth)/login/page.tsx`（ADR-030 之后 `(app)` 只留这一页） |
| `user_preferences` 表 | 预留 | `auth/models.py::UserPreference` 有表有列，**没有任何读写代码、没有接口** |
| `users.realname_status` / `realname_verified_at` | 预留 | 同上，只有列（合规预留，见模块 13） |
| `users.role`（`owner` / `member`） | 预留 | 有列、注册时写 owner，**没有任何地方按它判权限** |
| **邮箱验证** | 未实现 | 全仓无 `email_verified` 列、无发信代码、无 SMTP / 邮件服务配置 |
| **找回密码 / 修改密码** | 未实现 | 全仓无 `password_reset` 相关代码 |
| **登录限流 / 失败计数 / 异常登录风控** | 未实现 | 全仓无任何限流中间件 |
| 会话列表 / "退出其他设备" | 未实现 | `refresh_tokens` 有 `user_agent` 列，无查询接口 |
| 团队成员、邀请、RBAC | 未实现 | 决策记录 §6 明确**不做** |
| 手机号 | 未实现 | 决策记录 §6 明确**不做** |

---

## 4. 功能需求

### 4.1 P0

前七条已实现，后两条是本轮新增（决策记录 §6："补：邮箱验证、找回密码"）。

- **FR-AUTH-001**：邮箱注册，邮箱全局唯一且大小写不敏感。（已实现）
- **FR-AUTH-002**：邮箱密码登录，错误响应不泄露邮箱是否存在。（已实现）
- **FR-AUTH-003**：安全 Cookie 会话，Access 短时（1800 s）、Refresh 长时（14 天）。（已实现）
- **FR-AUTH-004**：Refresh 每次使用必须轮换；旧令牌再次出现即撤销整个 family。（已实现）
- **FR-AUTH-005**：登出撤销当前令牌族并清空 Cookie。（已实现）
- **FR-AUTH-006**：所有业务请求注入 `CurrentUser` 与 `org_id`；跨租户 404。（已实现）
- **FR-AUTH-007**：停用用户不得登录或刷新。（已实现）
- **FR-AUTH-008：邮箱验证。** 注册后发一封验证信；未验证的账号可以登录、
  可以浏览，但**不能发起任何花 Credits 的生成**。见 §5.1 的理由与状态机。
- **FR-AUTH-009：找回密码。** 邮件一次性 Token、短 TTL、用后立即作废，
  重置成功即撤销该用户全部令牌族。见 §5.2。
- **FR-AUTH-010：登录与发信限流。** 与 008 / 009 同批做，不能推后：
  一个能对任意邮箱无限发信的接口，是给别人当垃圾邮件枪用的。

### 4.2 P1

- **FR-AUTH-020**：修改密码（要求验证旧密码，成功后撤销其他会话）。
- **FR-AUTH-021**：用户资料与偏好（语言、主题、生产模式）接线，
  把 `user_preferences` 从"预留"变成"已实现"。
- **FR-AUTH-022**：会话列表与"退出其他设备"（`refresh_tokens.user_agent` 已有）。
- **FR-AUTH-023**：更换邮箱（要走一遍新邮箱的验证）。

### 4.3 P2

- **FR-AUTH-030**：OAuth / 企业 SSO / MFA。按商业化需求决定，现在没有需求。

### 4.4 不做（决策记录 §6）

| 项 | 为什么不做 |
|---|---|
| **团队成员、邀请、owner/admin/member 角色** | 首批用户是单人创作者。`users.role` 那一列留着，但**不能因为有列就说支持团队协作** |
| **手机号 / 短信验证码** | 与邮箱验证功能重复，且要走运营商资质 |
| 支付相关的账号状态（会员等级等） | 支付本身不做（模块 09） |

---

## 5. 核心流程与状态机

### 5.1 邮箱验证（FR-AUTH-008）

```text
Register
  → validate（邮箱规范化、密码强度）
  → create organization + owner user（同一事务）
  → issue cookie session
  → grant welcome credits（失败不回滚注册，可补偿）
  → 生成验证 Token（只存哈希）→ 发信            ← 新增

Verify
  → 用 raw token 算哈希查表
  → 检查未过期、未使用
  → users.email_verified_at = now()
  → 令牌标记已使用（一次性）
```

**门开在哪**：未验证可以登录、可以建项目、可以看界面；
**不能发起花 Credits 的生成**。

理由是这条门槛要挡的是"拿一次性邮箱刷体验额度"，
而体验额度的价值只有在发起生成时才被消耗（`welcome_grant = 500`，
够走完故事 → 角色 → 分镜 + 1 个镜头试片）。
把门开在登录处只会让真实用户第一步就被卡住，挡不住刷子多按一次收信。
门的判定点放在 `task.service.create_task`——那里已经有余额与熔断检查，
是所有花钱动作的唯一入口。

**重发**：同一邮箱 60 秒内只能发一封，24 小时上限 5 封（走 `pricing_rules`
之外的独立配置，因为它不是钱）。旧 Token 在发新的时作废。

### 5.2 找回密码（FR-AUTH-009）

```text
Forgot
  → 无论邮箱存不存在，都返回 204          ← 不能泄露账号是否存在
  → 存在时：生成一次性 Token（只存哈希，TTL 30 分钟）→ 发信

Reset
  → 用 raw token 算哈希查表
  → 检查未过期、未使用、用户未停用
  → 校验新密码强度（复用 RegisterIn._strength）
  → 更新 password_hash
  → 标记 Token 已使用
  → 撤销该用户的**全部**令牌族          ← 密码被改，旧会话必须全断
  → 不自动登录，跳回登录页
```

三条不能省：

- **`Forgot` 一律返回 204。** 返回"该邮箱未注册"等于送一个账号枚举接口，
  与 `login` 里那个虚拟哈希是同一个理由（`test_unknown_email_gives_same_error_as_wrong_password`）。
- **Token 只存哈希。** 与 `refresh_tokens.token_hash` 同一套做法。
  库被读走时，明文重置链接等于所有账号的密码。
- **重置成功撤销全部令牌族。** 密码泄露场景下，攻击者的旧会话必须同时失效，
  否则改密码毫无意义。

### 5.3 Refresh 轮换（已实现）

```text
Refresh
  → hash raw token
  → find token
  → detect revoked / reused / expired
      └─ reused → 撤销整个 family（重放检测）
  → issue replacement
  → revoke old token and link replacement（可追溯链）
```

---

## 6. 数据模型与所有权

### 6.1 已有

| 表 | 关键列 | 说明 |
|---|---|---|
| `organizations` | `name`、`kind`（personal/team）、`owner_user_id` | 与 `users.org_id` 互为外键，`owner_user_id` 可空：建 org → 建 user → 回填 |
| `users` | `email`、`password_hash`、`display_name`、`status`、`role`、`realname_status`、`last_login_at` | `uq_users_email_active` 是**部分唯一索引**（`deleted_at IS NULL`），普通 UNIQUE 会让注销过的邮箱永久不可用 |
| `refresh_tokens` | `token_hash`、`family_id`、`expires_at`、`revoked_at`、`replaced_by`、`user_agent` | 只存哈希；替换链可追溯 |
| `user_preferences` | `language`、`theme`、`production_policy` | **预留**，无读写代码 |

### 6.2 需要新增

```sql
email_verification_tokens
  id, user_id, token_hash, expires_at, used_at, created_at
  UNIQUE (token_hash)

password_reset_tokens
  id, user_id, token_hash, expires_at, used_at, created_at, requested_ip
  UNIQUE (token_hash)
```

外加 `users.email_verified_at TIMESTAMPTZ NULL`。

**两张表而不是一张带 `purpose` 列的表**：TTL 不同（验证 24 小时、
重置 30 分钟）、作废规则不同（重置成功要撤销全部会话，验证不用）、
风控口径不同。合成一张表的代价是每处都要 `if purpose ==`，
而这两个流程唯一的共同点只是"一次性令牌"这个形状。

**已存在的用户怎么办**：迁移把存量用户的 `email_verified_at` 直接填成
`created_at`。首批用户是负责人手动拉进来的，让他们回去点一封发不出去的
验证信没有意义。

---

## 7. API、事件与前端入口

### 7.1 已有

```text
POST /api/v1/auth/register    201  建号 + 建 org + 发体验额度 + 下发 Cookie
POST /api/v1/auth/login       200
POST /api/v1/auth/refresh     200  轮换
POST /api/v1/auth/logout      204
GET  /api/v1/auth/me          200
```

### 7.2 需要新增（P0）

```text
POST /api/v1/auth/verify-email/request     204  重发验证信（限流）
POST /api/v1/auth/verify-email/confirm     204  body: {token}
POST /api/v1/auth/password/forgot          204  body: {email}；一律 204
POST /api/v1/auth/password/reset           204  body: {token, new_password}
```

P1：`PATCH /auth/password`（改密）、`GET|PATCH /auth/preferences`、
`GET /auth/sessions` + `DELETE /auth/sessions/{id}`。

`GET /auth/me` 的响应要加 `email_verified: bool`，前端据此显示未验证横幅。

### 7.3 前端入口

- `/login`：登录与注册共用页（`app/(auth)/login/page.tsx`）。
  ADR-030 之后旧壳只剩这一页。
- 需要新增：`/verify-email`（收信点进来的落地页）、`/reset-password`（同上）、
  以及登录页上的"忘记密码"入口。三处都在 `(auth)` 段下，不进 `/freeflow`
  ——未登录的人不该加载工作台的壳。
- 未验证横幅出现在 `/freeflow` 全局顶栏，点击触发重发。

### 7.4 发信怎么发

**本轮不自建邮件服务，也不接第三方事务型服务**：首批用 **Gmail 与 QQ 邮箱的 SMTP** 发
（决策记录 §12 第 2 条），大陆用户默认走 QQ，其余走 Gmail；两组账号进配置不进代码。
已知代价是个人邮箱的日发信上限与退信率，开放注册前换阿里云邮件推送 / Resend，
`mailer` 接口不变。在 `apps/api/core/` 下留一个 `mailer` 抽象，
`ENV=test` 时强制走 Mock（与 Provider 的规矩一致，不能让测试真发信）。
发信失败**不能让注册失败**——与体验额度同一类可补偿错误，
注册照常返回，用户可以在界面上点重发。

---

## 8. 技术选择和工程设计

- Argon2 哈希，参数升级后在登录时 rehash。
- JWT 只承载短期 Access；长期 Refresh 必须可撤销并落库。
- 一次性 Token 用 `secrets.token_urlsafe(32)` 生成，**只存 SHA-256 哈希**，
  与 `refresh_tokens` 同一套。
- 注册的 org 与 user 必须同一数据库事务。
- 体验额度失败是可补偿错误，不造成账号创建失败（`billing.safe_topup` 的用意）。
- Cookie 在生产开 `Secure`、`HttpOnly`、`SameSite=lax`。
- **`rollback()` 之后不要再读 ORM 属性**：会触发同步 refresh，在 async 上下文里
  抛 `MissingGreenlet`，把真正的业务错误掩盖成 500。需要的值在 try 之前取出来。

---

## 9. 安全、性能和可靠性

- **限流是 P0 不是 P1**（FR-AUTH-010）。三处：登录失败按 IP + 账号计数、
  `password/forgot` 按邮箱 + IP、`verify-email/request` 按用户。
  现在**一处都没有**，而 `password/forgot` 一旦上线就是一个可以对任意
  邮箱地址发信的公开接口。
- 账号枚举：`login`、`forgot` 两处的响应必须与"存在但密码错"不可区分。
- 一次性 Token 全部只存哈希、短 TTL、用后作废、不可重复使用。
- 重置密码成功 → 撤销全部令牌族。
- 用户侧查询一律带 `org_id`；跨租户返回 404 而不是 403
  （403 会确认资源存在，可枚举）。
- 日志不输出密码、JWT、Refresh Token、Cookie、重置链接
  （`core/logging.py` 的脱敏，`tests/unit/test_logging_redaction.py`）。
- CSRF：Cookie 是 `SameSite=lax`，写操作目前靠它。上线前补一层显式检查
  （自定义头或双提交 Token），P1。

---

## 10. 模块依赖

- **依赖**：core（db / config / errors / logging）、模块 09（注册时发体验额度）、
  将来的 mailer。
- **被依赖**：所有用户侧模块。`CurrentUser` 是它们拿 `org_id` 的唯一来源。

---

## 11. 当前缺口与风险

1. **没有找回密码。** 外部用户一旦忘记密码就永久失去账号，
   我们又没有客服。这是 P0 里最真实的那一条。
2. **没有邮箱验证。** 一次性邮箱可以无限刷体验额度（每次 500 Credits），
   免费额度设计（`19_UnitEconomics.md` §5）失效。
3. **没有任何限流。** 登录可被暴力尝试；上线找回密码后还会多一个发信枪。
4. **`user_preferences` / `role` / `realname_status` 三项都是"预留"。**
   有列不等于有功能，文档里不得写成"支持团队协作"或"支持实名"。
5. **CSRF 只靠 `SameSite=lax`**，没有显式防护。
6. 停用用户的 `status` 只在登录与刷新处检查，长期有效的 Access Token
   在 30 分钟内仍然可用。可接受（TTL 短），但停用后要主动撤销令牌族。

---

## 12. 迭代计划

1. **Wave 1**：邮箱验证 + 找回密码 + 三处限流（FR-AUTH-008/009/010），
   连同 `mailer` 抽象与两张令牌表一起做。
   路线图里这一条排在 freeflow 闭环同一波（`04_MODULE_ROADMAP.md` §4 Wave 1-6）。
2. **Wave 1 之后**：`GET /auth/me` 补 `email_verified`，前端未验证横幅。
3. **P1**：改密、偏好接线、会话列表、更换邮箱。
4. **P1**：显式 CSRF 防护。
5. **不排期**：团队成员、RBAC、手机号、SSO / MFA。

---

## 13. 验收标准和测试

**已可验收（现在就该绿）**

- 注册、登录、刷新、登出、Token 重放、停用用户均有集成测试。
  （`tests/integration/test_auth_flow.py`、`test_auth_token_reuse.py`）
- 响应体里没有任何令牌；Cookie 是 httpOnly。
- 未知邮箱与密码错误的响应完全一致。
- 并发注册同一邮箱只产生一个账号（部分唯一索引兜底）。
- 任意跨租户资源请求返回 404，不能推断资源是否存在。
  （`tests/integration/test_tenant_isolation.py`）

**新增（P0 做完才算）**

- 验证 Token 用过一次后再用返回失败；过期同理。
- `password/forgot` 对已注册与未注册邮箱的响应**逐字节一致**（含耗时量级）。
- 重置密码成功后，重置前签发的所有 Refresh Token 全部失效。
- 未验证邮箱的账号可以登录、可以建项目，**发起出图任务被拒**，
  且错误信息说明原因和怎么解决。
- 登录连续失败 N 次后被限流；`forgot` 对同一邮箱的发信频率被限制。
- 日志与响应中找不到重置链接原文。
- 存量用户迁移后 `email_verified_at` 非空，不会被新门挡住。
