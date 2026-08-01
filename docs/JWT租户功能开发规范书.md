# JWT 租户功能开发规范书

> **文档版本**：v1.0
> **编制日期**：2026-08-01
> **适用项目**：WechatNum — A 股股价看板
> **文档状态**：待评审 / 开发依据
> **文档性质**：开发规范（Specification），不含落地代码改动，仅作为后续编码的统一标准

---

## 〇、文档目的

本规范书基于对现有代码库的完整审查，定义「在现有 FastAPI + DuckDB + Redis 架构上引入 **JWT 鉴权** 与 **多租户（Multi-Tenant）隔离**」的统一开发标准。所有后续编码工作（新建文件、修改既有逻辑、数据迁移、测试）必须遵循本规范。

**本规范书本身不修改任何项目代码，仅产出本文档。**

---

## 一、现状分析

### 1.1 系统架构概览

```
tushare ──每日入库──▶ DuckDB(market.duckdb, 公共市场数据, 只读)
                          │ 只读连接 + SQL 聚合
                          ▼
React SPA  ◀── JSON ── FastAPI(/api/*) ◀── Redis L2 缓存(重算结果)
                         │
                         ├── market.duckdb  : 公共行情库（只读，全租户共享）
                         └── paper.duckdb    : 模拟盘业务库（读写，需租户隔离）
```

- **后端**：Python ≥3.11 + FastAPI + Pydantic v2 + DuckDB + Redis
- **前端**：React + Vite + TS + Tailwind + ECharts（构建成静态 `dist/` 由 FastAPI 同进程托管）
- **配置**：仓库根 `.env` → `src/config.py` 的 `os.environ.get` 加载

### 1.2 现有鉴权机制（审查结论）

| 端点前缀 | 鉴权方式 | 说明 |
|---|---|---|
| `/api/sql` | 静态 Bearer Token | `SQL_API_TOKEN`（`.env` 配置）+ `secrets.compare_digest` 常数时间比较；未配置时端点 404 关闭 |
| `/api/market/*`、`/api/stocks/*`、`/api/rankings`、`/api/screening/*`、`/api/analytics/*`、`/api/earnings-calendar/*`、`/api/export/*` | **无鉴权** | 公开市场数据，全开放 |
| `/api/paper/*`（模拟盘） | **account_id 凭证** | `account_id`（UUID 不可猜测）放在 URL path，无登录体系；谁拿到 `account_id` 谁即可操作该账户 |

**核心结论**：
1. 项目当前**完全没有 JWT、没有用户登录体系、没有租户概念**。
2. 模拟盘的 `account_id` 是「隐式租户」——通过 UUID 不可猜测性做能力凭证，属于**安全反模式**（URL 泄漏/日志留存/浏览器历史/Referer 泄漏均可导致越权）。
3. `pyproject.toml` 依赖中**没有** `PyJWT` / `python-jose` / `passlib` 等鉴权库。

### 1.3 现有模拟盘数据模型（租户隔离改造对象）

`paper.duckdb` 全部表以 `account_id` 作为隔离维度：

| 表 | 主键 | 隔离列 | 说明 |
|---|---|---|---|
| `accounts` | `account_id` | — | 账户主表（name/init_cash/cash/frozen/status） |
| `orders` | `order_id` | `account_id` | 委托，`UNIQUE(account_id, request_id)` 幂等 |
| `fills` | `fill_id` | `account_id` | 成交 |
| `positions` | `(account_id, code)` | `account_id` | 持仓 |
| `cash_flows` | `flow_id` | `account_id` | 资金流水 |
| `equity_snapshots` | `(account_id, trade_date)` | `account_id` | 每日净值快照 |
| `account_resets` | `reset_id` | `account_id` | 重置前快照 |

> **改造要点**：上述所有表均缺 `tenant_id` 列，所有查询均缺 `WHERE tenant_id = ?` 过滤。这是越权风险根因。

### 1.4 现有缓存层

- `src/cache.py` 的 `_make_key(func_name, params)` 生成全局哈希 key：`{CACHE_VERSION}:{func_name}:{digest}`
- **问题**：当前缓存内容均为公共市场数据，全局共享无问题；但模拟盘查询一旦走缓存（如 overview/equity_curve），**必须把 tenant_id 纳入 key**，否则跨租户串数据。
- 撮合后 `cache.invalidate_all()` 全量清缓存，租户化后应收敛为按租户失效。

### 1.5 改造诉求

引入 JWT 鉴权 + 多租户，达成：
1. 模拟盘从「account_id 凭证」升级为「JWT 携带身份 + 服务端强校验归属」；
2. 支持多租户（多组织/多用户）数据隔离；
3. 为后续 SaaS 化、配额管理、审计打基础；
4. 不破坏现有公开行情数据的无鉴权访问体验。

---

## 二、设计目标与原则

### 2.1 目标

| 编号 | 目标 | 验收标准 |
|---|---|---|
| G1 | 引入标准 JWT（Access + Refresh 双 Token） | 符合 RFC 7519，HS256 签名，可被任意标准库校验 |
| G2 | 引入租户（Tenant）实体与隔离 | 所有模拟盘数据读写强制带 `tenant_id`，跨租户不可见 |
| G3 | 模拟盘账户归属租户 | `account` 必属于某 `tenant`，操作须校验归属 |
| G4 | 向后兼容过渡 | 现有 `account_id` 凭证模式可在过渡期内并存（可配置开关） |
| G5 | 公开行情数据保持无鉴权 | `/api/market/*` 等公开端点不强制 JWT（可选只读租户上下文） |
| G6 | 缓存租户隔离 | 模拟盘相关缓存 key 含 `tenant_id` |
| G7 | 安全合规 | 密钥不落代码、常数时间校验、防重放、防越权 |

### 2.2 设计原则

1. **最小侵入**：新增 `src/auth/` 模块承载鉴权逻辑，既有路由通过 `Depends` 注入，不重写业务层。
2. **显式优于隐式**：租户上下文必须由服务端从 JWT 解析，**绝不信任客户端传入的 tenant_id**。
3. **隔离失败即拒绝**：任何缺少 `tenant_id` 的租户隔离表查询，一律拒绝执行（开发期抛错，生产期 500 + 告警）。
4. **安全默认关闭**：未配置 `JWT_SECRET` 时，受保护端点返回 503（不可用），而非降级为无鉴权。
5. **配置驱动**：所有阈值、TTL、开关均走环境变量，禁止硬编码。
6. **可观测**：鉴权失败、越权尝试必须记录审计日志。

### 2.3 兼容性策略

采用**双模式过渡**：

- `AUTH_MODE=legacy`：保持现状（account_id 凭证 + 静态 SQL token），用于回滚兜底。
- `AUTH_MODE=jwt`：启用 JWT + 租户（目标态）。
- `AUTH_MODE=hybrid`（过渡期默认）：JWT 优先，未携带 JWT 时回退校验 `account_id` 凭证，并打废弃日志。

过渡期满后强制 `jwt` 模式，移除 legacy 分支。

---

## 三、总体架构

### 3.1 鉴权与租户上下文流转

```
客户端
  │  Authorization: Bearer <JWT>
  ▼
FastAPI 中间件 / Depends(get_current_principal)
  │  ① 解析 JWT  ② 验签  ③ 校验 exp/iss/aud  ④ 提取 claims
  ▼
Principal(tenant_id, user_id, account_id?, roles, scopes)
  │  注入到请求处理函数
  ▼
路由层：Depends(require_tenant) / require_role("trader")
  │
  ▼
Service 层：所有 paper 库查询追加 WHERE tenant_id = principal.tenant_id
  │
  ▼
DuckDB(paper.duckdb)  +  Redis(key 前缀含 tenant_id)
```

### 3.2 租户隔离模型

```
Tenant（租户/组织）
  ├── 1:N ── User（用户，归属某租户）
  │            └── 持有 JWT，claims 含 tenant_id + user_id
  ├── 1:N ── Account（模拟盘账户，归属租户）
  │            └── User 可被授权访问 0..N 个 Account
  └── 配额 / 限额（预留）
```

- **租户**是隔离的**最高边界**：跨租户数据绝对不可见。
- **账户**是租户内的业务实体；一个租户可有多个账户。
- **用户**通过 JWT 标识；用户对账户的访问需校验账户归属租户 = 用户租户。

### 3.3 组件分层

```
src/
├── auth/                          【新增】鉴权模块
│   ├── __init__.py
│   ├── config.py                  # JWT 配置（密钥/算法/TTL/模式）
│   ├── jwt_handler.py             # 签发 / 校验 / 解析
│   ├── models.py                  # Principal / Tenant / User Pydantic 模型
│   ├── dependencies.py            # FastAPI Depends：get_current_principal / require_role
│   ├── password.py                # 密码哈希（passlib，仅登录用）
│   └── store.py                   # tenant/user 持久化（可落 paper.duckdb 或独立 auth.duckdb）
├── api/
│   ├── routes/
│   │   ├── auth.py                【新增】/api/auth/login /refresh /logout /me
│   │   ├── tenants.py             【新增】/api/tenants（租户管理，超管用）
│   │   ├── paper.py               【改造】所有路由加 Depends + 归属校验
│   │   ├── sql.py                 【改造】JWT 或 legacy token 二选一
│   │   └── ...（公开端点不改）
│   └── main.py                    【改造】注册 auth router + 中间件
└── ...
```

---

## 四、JWT 规范

### 4.1 依赖选型

| 依赖 | 版本约束 | 用途 |
|---|---|---|
| `PyJWT` | `>=2.8,<3` | JWT 签发与校验（标准库生态最广，纯 Python，无 C 扩展） |
| `passlib[bcrypt]` | `>=1.7,<2` | 用户密码哈希（仅登录签发 token 时用） |

> 不采用 `python-jose`：维护活跃度低于 PyJWT，且无额外优势。
> 不引入 OAuth2 全家桶：当前规模不需要授权服务器，自签 JWT 足够。

加入 `pyproject.toml` 的 `[project] dependencies`，并在 `[project.optional-dependencies] dev` 中加 `freezegun`（测试时间相关用例）。

### 4.2 密钥管理

| 配置项 | 说明 | 约束 |
|---|---|---|
| `JWT_SECRET` | HS256 对称密钥 | **必须**通过环境变量注入；禁止落代码、禁止落仓库 `.env`（`.env.example` 仅占位）；长度 ≥ 32 字节；生产应从密钥管理服务（如 Vault/KMS）获取 |
| `JWT_ISSUER` | 签发方 `iss` | 如 `wechatnum-api` |
| `JWT_AUDIENCE` | 受众 `aud` | 如 `wechatnum-clients` |

**密钥轮换**：支持 `JWT_SECRET_PREVIOUS`（上一个密钥，仅用于校验过渡），校验时先用当前密钥，失败再回退 `PREVIOUS`，签发一律用当前密钥。轮换窗口建议 ≤ 7 天。

### 4.3 算法

- **签名算法**：`HS256`（HMAC-SHA256，对称密钥）。
  - 选择对称而非 RS256：单服务部署、无需第三方验签，对称密钥运维更简单。
  - 若未来拆分微服务且需公钥验签，再迁移至 RS256/EdDSA。
- **禁止 `none` 算法**：校验时显式 `algorithms=["HS256"]`，**绝不接受 `alg=none`**（防经典绕过）。
- **禁止算法降级**：校验时白名单指定算法，不接受 token 自报算法。

### 4.4 Token 结构

#### 4.4.1 Header（固定）

```json
{ "alg": "HS256", "typ": "JWT" }
```

#### 4.4.2 Payload — 标准 Claims

| Claim | 类型 | 必填 | 说明 |
|---|---|---|---|
| `iss` | string | ✅ | 签发方 = `JWT_ISSUER` |
| `sub` | string | ✅ | 用户唯一标识 `user_id` |
| `aud` | string | ✅ | 受众 = `JWT_AUDIENCE` |
| `iat` | int | ✅ | 签发时间（Unix 秒） |
| `nbf` | int | ✅ | 生效时间（通常 = iat） |
| `exp` | int | ✅ | 过期时间（= iat + TTL） |
| `jti` | string | ✅ | Token 唯一 ID（UUID），用于撤销/审计 |

#### 4.4.3 Payload — 业务 Claims

| Claim | 类型 | 必填 | 说明 |
|---|---|---|---|
| `tenant_id` | string | ✅ | 租户唯一标识 |
| `roles` | string[] | ✅ | 角色列表，如 `["trader"]`；`["admin"]` 为租户管理员 |
| `account_ids` | string[] | ❌ | 该用户被授权访问的账户 ID 列表（空数组表示可访问租户下全部账户，由 `roles` 决定） |
| `scope` | string | ❌ | 空格分隔的能力串（预留 OAuth2 风格，如 `paper:write sql:read`） |
| `token_type` | string | ✅ | `access` 或 `refresh`，二者 claims 结构相同但 TTL/用途不同 |

#### 4.4.4 Access Token 示例 Payload

```json
{
  "iss": "wechatnum-api",
  "sub": "u_8f3a...",
  "aud": "wechatnum-clients",
  "iat": 1722470400,
  "nbf": 1722470400,
  "exp": 1722474000,
  "jti": "9b1d...-...-...",
  "tenant_id": "t_acme",
  "roles": ["trader"],
  "account_ids": ["acc_001", "acc_002"],
  "token_type": "access"
}
```

### 4.5 双 Token 机制

| Token | TTL（默认，可配） | 用途 | 存储 |
|---|---|---|---|
| Access Token | `JWT_ACCESS_TTL` = **15 分钟** | 携带访问 API | 客户端内存（不落 localStorage，防 XSS） |
| Refresh Token | `JWT_REFRESH_TTL` = **7 天** | 换取新 Access | 客户端 HttpOnly + Secure + SameSite=Strict cookie（防 XSS 读取） |

**刷新规则**：
- Refresh Token 仅能调用 `/api/auth/refresh`，不能访问业务 API（校验时检查 `token_type == "access"`）。
- 刷新时签发**新的** Access + Refresh，旧 Refresh 通过 `jti` 加入撤销名单（**Refresh Token 轮转**，一次性使用）。
- Refresh Token 过期或被撤销 → 客户端必须重新登录。

### 4.6 签发与校验流程

#### 签发（`/api/auth/login` 成功后）

1. 校验 `username` + `password`（`passlib` 验证哈希）。
2. 加载用户 → 取 `tenant_id` / `roles` / 授权 `account_ids`。
3. 生成 `jti`（`secrets.token_urlsafe(16)` 或 `uuid4`）。
4. 签发 Access + Refresh，`token_type` 区分。
5. 返回 JSON：`{access_token, refresh_token, expires_in, token_type:"Bearer"}`。

#### 校验（每个受保护端点 `Depends`）

1. 从 `Authorization: Bearer <token>` 提取 token（缺失 → 401）。
2. `jwt.decode(token, key, algorithms=["HS256"], issuer=..., audience=...)`，验签 + 校验 `exp/nbf/iss/aud`。
3. 校验 `token_type == "access"`（业务端点不接受 refresh token）。
4. 查 `jti` 是否在撤销黑名单（Redis SET `jwt:revoked:{jti}`，TTL = token 剩余 exp）。
5. 构造 `Principal` 注入请求。
6. **常数时间比较**所有敏感判断（如归属校验），防时序攻击。

### 4.7 时钟漂移与有效期

- `leeway` 参数 = **30 秒**（允许 `exp`/`nbf` 容差，防多机时钟不同步）。
- Access TTL 严禁 > 1 小时；Refresh TTL 严禁 > 30 天（配置项校验，超限启动报错）。

### 4.8 撤销与黑名单

- **主动登出**：`/api/auth/logout` 将当前 Access + Refresh 的 `jti` 写入 Redis 黑名单，TTL = 各自剩余有效期。
- **被动撤销**（改密 / 踢人 / 租户停用）：批量撤销该用户所有有效 `jti`（需在签发时维护 `jwt:user:{user_id}:active` Set，撤销时遍历清空）。
- **Redis 不可用时**：撤销降级为「失效」——即不信任撤销，token 自然过期。此情况记告警，不阻塞请求（可用性优先，因 Access TTL 仅 15 分钟）。

---

## 五、租户模型

### 5.1 概念定义

| 概念 | 定义 | 标识 |
|---|---|---|
| Tenant（租户） | 数据隔离的最高边界，对应一个组织/账户主体 | `tenant_id`（如 `t_acme`） |
| User（用户） | 登录主体，归属唯一租户 | `user_id`（如 `u_8f3a`） |
| Account（模拟盘账户） | 业务实体，归属唯一租户 | `account_id`（沿用既有 UUID） |
| Role（角色） | RBAC 角色串，租户内有效 | `admin` / `trader` / `viewer` |

### 5.2 数据库表设计（新增）

新增表存放于 **`paper.duckdb`**（与既有业务库同库，避免跨库 JOIN；后续若拆分可迁独立 `auth.duckdb`）。

```sql
-- 租户表
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   VARCHAR PRIMARY KEY,
    name        VARCHAR NOT NULL,
    status      VARCHAR NOT NULL DEFAULT 'active',  -- active / suspended / deleted
    created_at  TIMESTAMP NOT NULL DEFAULT current_timestamp,
    updated_at  TIMESTAMP NOT NULL DEFAULT current_timestamp
);

-- 用户表（登录主体）
CREATE TABLE IF NOT EXISTS users (
    user_id       VARCHAR PRIMARY KEY,
    tenant_id     VARCHAR NOT NULL,
    username      VARCHAR NOT NULL,          -- 租户内唯一
    password_hash VARCHAR NOT NULL,          -- passlib bcrypt
    roles         VARCHAR NOT NULL DEFAULT 'trader',  -- 逗号分隔：admin,trader
    status        VARCHAR NOT NULL DEFAULT 'active',  -- active / disabled
    created_at    TIMESTAMP NOT NULL DEFAULT current_timestamp,
    updated_at    TIMESTAMP NOT NULL DEFAULT current_timestamp,
    UNIQUE (tenant_id, username)
);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);

-- 用户 ↔ 账户授权（一个用户可访问租户内若干账户；空表表示按角色默认）
CREATE TABLE IF NOT EXISTS user_account_grants (
    user_id    VARCHAR NOT NULL,
    account_id VARCHAR NOT NULL,
    granted_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (user_id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_grants_user ON user_account_grants(user_id);
CREATE INDEX IF NOT EXISTS idx_grants_account ON user_account_grants(account_id);
```

### 5.3 既有表改造（追加列）

```sql
-- accounts 表追加 tenant_id（必填，迁移时回填默认租户）
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS tenant_id VARCHAR;
CREATE INDEX IF NOT EXISTS idx_accounts_tenant ON accounts(tenant_id);
```

> 其余业务表（orders/fills/positions/cash_flows/equity_snapshots/account_resets）**不需要**加 `tenant_id`——它们通过 `account_id → accounts.tenant_id` 关联即可定位租户。这样避免冗余列维护成本，且隔离校验统一收敛在 `account` 归属校验上。
>
> **权衡说明**：若对查询性能有极致要求（避免每次 JOIN accounts 取 tenant_id），可考虑在这些表冗余 `tenant_id`。当前数据量级（模拟盘）无需，JOIN 成本可忽略。此决策可后续评审调整。

### 5.4 租户隔离强制规则

1. **任何 paper 库写操作**必须先校验 `account.tenant_id == principal.tenant_id`，否则 403。
2. **任何 paper 库读操作**的 SQL 必须包含 `account_id IN (该 principal 授权的 account_ids)` 或显式 JOIN accounts 限定 tenant。
3. **隔离校验函数**集中实现于 `src/auth/dependencies.py` 的 `verify_account_access(account_id, principal)`，禁止各路由自行实现。
4. **Service 层签名**应显式接收 `tenant_id`/`principal` 参数，不依赖全局变量或请求上下文隐式获取（便于单测）。

---

## 六、鉴权依赖设计

### 6.1 依赖函数清单（`src/auth/dependencies.py`）

| 依赖 | 作用 | 失败响应 |
|---|---|---|
| `get_current_principal()` | 解析 JWT → `Principal`，注入请求 | 401 |
| `require_roles(*roles)` | 校验角色属于白名单 | 403 |
| `require_tenant_admin()` | 快捷：要求 `admin` 角色 | 403 |
| `verify_account_access(account_id, principal)` | 校验账户归属当前租户且用户被授权 | 403 |
| `get_tenant_scoped_conn()` | 返回已带租户上下文的 paper 库连接（概念，DuckDB 无会话级隔离，实际靠 SQL 过滤） | — |

### 6.2 Principal 模型（`src/auth/models.py`）

```python
class Principal(BaseModel):
    user_id: str
    tenant_id: str
    roles: list[str]
    account_ids: list[str]          # 授权账户，空表示全部（仅 admin）
    jti: str                         # 用于撤销/审计
    raw_token: str                   # 审计日志用，不返回给客户端
```

### 6.3 端点保护级别划分

| 级别 | 端点 | 鉴权要求 |
|---|---|---|
| **公开** | `/api/health`、`/api/market/*`、`/api/stocks/*`、`/api/rankings`、`/api/screening/*`、`/api/analytics/*`、`/api/earnings-calendar/*`、`/api/export/*` | 无（保持现状） |
| **租户级** | `/api/paper/accounts`（创建账户）、`/api/paper/accounts/{id}/overview` 等读 | `Depends(get_current_principal)` + 账户归属校验 |
| **写操作** | `/api/paper/accounts/{id}/orders`、`/reset`、`PATCH positions` | 上述 + 角色 `trader` 或 `admin` |
| **管理** | `/api/tenants`（增删租户）、用户管理 | `admin` 角色 |
| **SQL 网关** | `/api/sql` | JWT（`scope` 含 `sql:read`）**或** 既有静态 `SQL_API_TOKEN`（`AUTH_MODE` 决定） |

### 6.4 错误响应规范

统一错误响应结构（与既有 `HTTPException(detail=...)` 兼容，建议扩展为结构化）：

```json
{
  "error": {
    "code": "UNAUTHORIZED | FORBIDDEN | TOKEN_EXPIRED | TOKEN_INVALID | TENANT_MISMATCH",
    "message": "人类可读说明",
    "request_id": "可选追踪 ID"
  }
}
```

| 场景 | HTTP | code |
|---|---|---|
| 缺少 Authorization 头 | 401 | `UNAUTHORIZED` |
| Token 验签失败 / 格式错 | 401 | `TOKEN_INVALID` |
| Token 过期 | 401 | `TOKEN_EXPIRED` |
| 用 refresh token 访问业务 | 403 | `TOKEN_TYPE_MISMATCH` |
| 角色不足 | 403 | `FORBIDDEN` |
| 账户不属于当前租户 | 403 | `TENANT_MISMATCH` |
| 租户被停用 | 403 | `TENANT_SUSPENDED` |

---

## 七、改造点清单（按模块）

> 以下为编码阶段的任务清单与规范要求，**本规范书不执行这些改动**，仅作为开发依据。

### 7.1 配置层（`src/config.py` + `.env`）

- [ ] 新增 JWT 相关环境变量（见 §八）。
- [ ] `.env.example` 补充占位项（真实密钥不落仓库）。
- [ ] `src/config.py` 加载 `AUTH_MODE` / `JWT_SECRET` / `JWT_ACCESS_TTL` 等，启动期校验必填项。

### 7.2 数据库层（`src/paper_trading/store.py`）

- [ ] `PAPER_SCHEMA_SQL` 追加 `tenants` / `users` / `user_account_grants` 表 DDL。
- [ ] `accounts` 表追加 `tenant_id` 列与索引（`init_schema` 幂等执行）。
- [ ] 编写迁移脚本 `scripts/migrate_add_tenant.py`：为既有 `accounts` 回填 `tenant_id`（默认租户 `t_default`），并创建默认管理员账号。

### 7.3 鉴权模块（`src/auth/`，新增）

- [ ] `config.py`：加载并校验配置。
- [ ] `jwt_handler.py`：`issue_token_pair(user)` / `decode_access(token)` / `decode_refresh(token)`。
- [ ] `models.py`：`Principal` / `TokenPair` / `LoginIn` 等 Pydantic 模型。
- [ ] `password.py`：`hash_password` / `verify_password`（passlib bcrypt）。
- [ ] `dependencies.py`：FastAPI Depends 实现（见 §六）。
- [ ] `store.py`：`get_user_by_username` / `create_tenant` / `create_user` / `grant_account` 等。

### 7.4 鉴权路由（`src/api/routes/auth.py`，新增）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 用户名密码 → Access+Refresh |
| POST | `/api/auth/refresh` | Refresh → 新 Access+Refresh（轮转） |
| POST | `/api/auth/logout` | 撤销当前 token |
| GET | `/api/auth/me` | 返回当前 Principal（不含 raw_token） |

### 7.5 模拟盘路由层（`src/api/routes/paper.py`，改造）

- [ ] 所有 `account_id` path 参数路由加 `Depends(get_current_principal)`。
- [ ] 调用 service 前先 `verify_account_access(account_id, principal)`。
- [ ] `create_account` 改为从 `principal.tenant_id` 取归属（忽略客户端传入 tenant_id）。
- [ ] 写操作（下单/撤单/重置/改成本）加 `require_roles("trader", "admin")`。
- [ ] 过渡期 `AUTH_MODE=hybrid`：未带 JWT 时回退校验 `account_id` 凭证（仅读），并打 `DEPRECATED` 日志。

### 7.6 模拟盘服务层（`src/paper_trading/service.py`，改造）

- [ ] 所有函数签名追加 `tenant_id: str` 或 `principal: Principal` 参数。
- [ ] `_account_row` 等内部查询追加 `AND tenant_id = ?` 条件。
- [ ] `create_account` 写入 `tenant_id`。
- [ ] `reset_account` 等高危操作追加二次归属确认。

### 7.7 撮合引擎（`src/paper_trading/matcher.py`，改造）

- [ ] 撮合是系统级定时任务，不携带用户 Principal；但其查询天然按 account 维度，**只要 accounts.tenant_id 已回填，撮合逻辑无需大改**。
- [ ] 审计：撮合结果应记录 `account_id`（含隐式 tenant 可追溯）。

### 7.8 SQL 网关（`src/api/routes/sql.py`，改造）

- [ ] `AUTH_MODE=jwt`：校验 JWT 且 `scope` 含 `sql:read`。
- [ ] `AUTH_MODE=legacy`：保留既有 `SQL_API_TOKEN` 静态校验。
- [ ] `AUTH_MODE=hybrid`：优先 JWT，失败回退静态 token。

### 7.9 缓存层（`src/cache.py`，改造）

- [ ] `_make_key` 增加 `tenant_id` 入参：公共数据缓存 `tenant_id="__public__"`；模拟盘缓存用真实 `tenant_id`。
- [ ] `try_load` / `save` 的 `relevant_params` 自动注入 `tenant_id`。
- [ ] `invalidate_all` 保留（撮合后全清）；新增 `invalidate_tenant(tenant_id)` 按租户失效（pattern `{CACHE_VERSION}:*:{tenant_id}:*`，需调整 key 结构）。

### 7.10 公共只读端点（不强制改造）

- `/api/market/*` 等保持无鉴权。
- **可选增强**：若希望统计租户访问行为，可在不带 JWT 时注入 `Principal(tenant_id="__anonymous__")`，仅用于埋点，不影响数据可见性。

### 7.11 前端（`frontend/`，改造）

- [ ] 新增登录页 → 调 `/api/auth/login`。
- [ ] Access Token 存内存（JS 变量），刷新时由 Refresh Cookie 自动续期。
- [ ] Refresh Token 由后端 `Set-Cookie: HttpOnly; Secure; SameSite=Strict; Path=/api/auth` 下发。
- [ ] 请求拦截器：自动附加 `Authorization: Bearer <access>`；401 时尝试 `/api/auth/refresh` 后重试一次，再失败跳登录。
- [ ] 模拟盘页：不再在 URL 暴露 `account_id` 凭证语义，改为登录态自动拉取已授权账户列表。

### 7.12 应用入口（`src/api/main.py`，改造）

- [ ] 注册 `auth.router` 与 `tenants.router`。
- [ ] 加 CORS 时序：`CORS` 中间件必须在鉴权 Depends 之前（既有顺序已满足）。
- [ ] 启动期校验：`AUTH_MODE=jwt` 时 `JWT_SECRET` 必填，缺失则拒绝启动。

---

## 八、配置项清单

### 8.1 新增环境变量

| 变量 | 默认值 | 说明 | 约束 |
|---|---|---|---|
| `AUTH_MODE` | `hybrid` | `legacy` / `hybrid` / `jwt` | 过渡期用 hybrid |
| `JWT_SECRET` | （无） | HS256 密钥 | `AUTH_MODE != legacy` 时必填；≥32 字节 |
| `JWT_SECRET_PREVIOUS` | （无） | 旧密钥（轮换用） | 可选 |
| `JWT_ISSUER` | `wechatnum-api` | iss claim | — |
| `JWT_AUDIENCE` | `wechatnum-clients` | aud claim | — |
| `JWT_ALGORITHM` | `HS256` | 签名算法 | 仅允许 HS256 |
| `JWT_ACCESS_TTL` | `900` | Access TTL（秒） | ≤3600 |
| `JWT_REFRESH_TTL` | `604800` | Refresh TTL（秒） | ≤2592000（30天） |
| `JWT_LEEWAY` | `30` | 时钟容差（秒） | 0-300 |
| `JWT_COOKIE_NAME` | `wn_refresh` | Refresh Cookie 名 | — |
| `REFRESH_ROTATE` | `true` | Refresh 轮转开关 | 建议 true |
| `PASSWORD_MIN_LEN` | `8` | 密码最小长度 | ≥8 |
| `BCRYPT_ROUNDS` | `12` | bcrypt 计算轮数 | 10-14 |
| `DEFAULT_TENANT_ID` | `t_default` | 迁移用默认租户 | 仅迁移期 |
| `LEGACY_ACCOUNT_ID_AUTH` | `true`(hybrid) | 过渡期是否允许 account_id 凭证 | jwt 模式强制 false |

### 8.2 `.env.example` 追加（占位，不含真实值）

```env
# ===== Auth / JWT =====
AUTH_MODE=hybrid
JWT_SECRET=REPLACE_WITH_RANDOM_32B_MIN
# JWT_SECRET_PREVIOUS=
JWT_ISSUER=wechatnum-api
JWT_AUDIENCE=wechatnum-clients
JWT_ALGORITHM=HS256
JWT_ACCESS_TTL=900
JWT_REFRESH_TTL=604800
JWT_LEEWAY=30
JWT_COOKIE_NAME=wn_refresh
REFRESH_ROTATE=true
PASSWORD_MIN_LEN=8
BCRYPT_ROUNDS=12
```

---

## 九、迁移与兼容

### 9.1 数据迁移脚本（`scripts/migrate_add_tenant.py`）

1. 连接 `paper.duckdb`（读写）。
2. 执行新表 DDL（`tenants` / `users` / `user_account_grants`）。
3. `accounts` 表加 `tenant_id` 列（若不存在）。
4. 创建默认租户 `t_default`（若不存在）。
5. `UPDATE accounts SET tenant_id = 't_default' WHERE tenant_id IS NULL`。
6. 创建默认管理员 `admin`（密码由环境变量 `MIGRATE_ADMIN_PASSWORD` 注入，**禁止硬编码**），赋 `admin` 角色。
7. 打印迁移摘要，建议备份 `paper.duckdb` 后执行。

**幂等**：脚本可重复执行，已存在的对象跳过。

### 9.2 灰度 / 过渡期策略

| 阶段 | `AUTH_MODE` | 行为 | 验收 |
|---|---|---|---|
| 阶段 0 | `legacy` | 现状不变 | 回滚基线 |
| 阶段 1 | `hybrid` | JWT 可用，account_id 凭证仍可读（写需 JWT） | 新登录流程通过 |
| 阶段 2 | `hybrid` | 全量用户迁移完成，停用 account_id 凭证写 | 旧客户端下线 |
| 阶段 3 | `jwt` | 强制 JWT，移除 legacy 分支代码 | 安全审计通过 |

### 9.3 回滚方案

- 代码回滚到 `AUTH_MODE=legacy` 分支。
- `paper.duckdb` 新增列/表不影响旧代码读取（DuckDB 宽松列容忍）。
- 迁移前必须 `cp paper.duckdb paper.duckdb.bak.YYYYMMDD`。

---

## 十、安全规范

### 10.1 密钥安全

- `JWT_SECRET` 永不落仓库、永不打日志、永不出现在错误响应。
- 生产从 KMS/Vault 注入；本地开发用 `.env`（已 `.gitignore`）。
- 定期轮换（建议 90 天），轮换走 `JWT_SECRET` + `JWT_SECRET_PREVIOUS` 双密钥窗口。

### 10.2 传输安全

- 生产必须 HTTPS（反代层 TLS，FastAPI 8501 可保持明文，由前置 Nginx/Caddy 终止 TLS）。
- Refresh Token Cookie 必须 `Secure`（生产）、`HttpOnly`、`SameSite=Strict`。
- 禁止 Access Token 落 `localStorage`（XSS 可读）。

### 10.3 防 JWT 陷阱（强制）

1. **不接受 `alg=none`**：`decode` 时显式 `algorithms=[config.JWT_ALGORITHM]`。
2. **不信任 token 自报算法**：白名单指定。
3. **校验 `iss` / `aud`**：`decode` 时必须传 `issuer` / `audience`，缺失则拒绝。
4. **校验 `exp` / `nbf`**：库默认校验，但 `leeway` 不超过 30s。
5. **校验 `token_type`**：业务端点拒绝 refresh token。
6. **归属校验用常数时间**：`secrets.compare_digest(a, b)`，防时序侧信道。
7. **禁止把敏感数据放 payload**：JWT payload 仅 Base64，非加密。

### 10.4 限流与防爆破

- `/api/auth/login` 限流：同 IP 10 次/分钟，同 username 5 次/分钟（Redis 计数）。
- 连续失败 5 次锁定该 username 15 分钟。
- 登录失败不区分「用户不存在」与「密码错误」（统一返回 `INVALID_CREDENTIALS`，防用户枚举）。

### 10.5 审计日志

以下事件必须记录（结构化 JSON，含 `timestamp` / `tenant_id` / `user_id` / `jti` / `ip` / `event`）：

- 登录成功 / 失败
- Token 刷新
- 登出 / 撤销
- 越权尝试（`TENANT_MISMATCH` / `FORBIDDEN`）
- 租户创建 / 停用
- 高危操作（账户重置）

日志写入文件 `logs/auth.jsonl`（按日轮转），不写入 DuckDB（避免拖累业务库）。

### 10.6 密码存储

- passlib bcrypt，`BCRYPT_ROUNDS=12`。
- 禁止明文 / MD5 / SHA1。
- 改密时生成新 hash，旧 hash 即时失效。

---

## 十一、测试规范

### 11.1 单元测试（`tests/auth/`）

| 测试 | 覆盖点 |
|---|---|
| `test_jwt_issue_decode` | 签发后能正确解码，claims 齐全 |
| `test_jwt_expired` | exp 过期 → 抛 `ExpiredSignatureError` |
| `test_jwt_wrong_secret` | 错误密钥验签失败 |
| `test_jwt_alg_none_rejected` | `alg=none` 被拒绝 |
| `test_jwt_wrong_aud_iss` | aud/iss 不匹配被拒绝 |
| `test_refresh_rotation` | 旧 refresh 刷新后失效 |
| `test_password_hash_verify` | 哈希与验证 |
| `test_principal_construction` | Principal 字段正确 |
| `test_account_access_check` | 跨租户账户访问被拒 |
| `test_tenant_isolation_sql` | 查询不含 tenant_id 时抛错（开发期） |

### 11.2 集成测试（`tests/api/test_auth_flow.py`）

- 完整流程：注册租户 → 建用户 → 登录 → 创账户 → 下单 → 查询 → 跨租户访问被拒 → 刷新 → 登出。
- 用 `TestClient` + 临时 `paper.duckdb`（`tmp_path` 夹具）。
- `freezegun` 冻结时间测 token 过期。

### 11.3 安全测试

- 越权：A 租户用户用 B 租户 `account_id` 访问 → 403。
- 重放：撤销后旧 token → 401。
- 篡改 payload → 验签失败。
- Refresh 轮转：旧 refresh 二次使用 → 401。

---

## 十二、实施路线图

| 里程碑 | 内容 | 产出 |
|---|---|---|
| M1 基础设施 | 依赖、配置、`src/auth/` 骨架、JWT 签发校验 | 可签发/校验 token |
| M2 数据模型 | 迁移脚本、tenants/users 表、accounts.tenant_id | paper.duckdb 完成迁移 |
| M3 鉴权路由 | `/api/auth/*`、Principal 依赖 | 可登录获取 token |
| M4 模拟盘改造 | paper.py / service.py 加 Depends + 归属校验 | 模拟盘受 JWT 保护 |
| M5 缓存租户化 | cache.py key 含 tenant_id | 无跨租户串数据 |
| M6 SQL 网关 | sql.py 双模式 | — |
| M7 前端 | 登录页、请求拦截、刷新 | 端到端可用 |
| M8 过渡与灰度 | hybrid 模式上线，观测 → 切 jwt | 生产全量 JWT |

---

## 十三、附录

### A. 完整 Access Token Payload 示例

```json
{
  "iss": "wechatnum-api",
  "sub": "u_8f3a1c2b",
  "aud": "wechatnum-clients",
  "iat": 1722470400,
  "nbf": 1722470400,
  "exp": 1722471300,
  "jti": "5f8e3a9c-1b2d-4e5f-8a7b-9c0d1e2f3a4b",
  "tenant_id": "t_acme",
  "roles": ["trader"],
  "account_ids": ["acc_001", "acc_002"],
  "scope": "paper:write sql:read",
  "token_type": "access"
}
```

### B. 关键依赖骨架（参考，非落地代码）

```python
# src/auth/jwt_handler.py（骨架示意）
import jwt, time, uuid, secrets
from src.auth import config

def issue_token_pair(user) -> dict:
    now = int(time.time())
    common = {"iss": config.JWT_ISSUER, "aud": config.JWT_AUDIENCE,
              "sub": user.user_id, "tenant_id": user.tenant_id,
              "roles": user.roles, "account_ids": user.account_ids, "iat": now, "nbf": now}
    access_jti = str(uuid.uuid4())
    access = jwt.encode({**common, "exp": now + config.JWT_ACCESS_TTL,
                         "jti": access_jti, "token_type": "access"}, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)
    refresh_jti = str(uuid.uuid4())
    refresh = jwt.encode({**common, "exp": now + config.JWT_REFRESH_TTL,
                          "jti": refresh_jti, "token_type": "refresh"}, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)
    return {"access_token": access, "refresh_token": refresh,
            "expires_in": config.JWT_ACCESS_TTL, "token_type": "Bearer"}

def decode_access(token: str) -> dict:
    return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM],
                      issuer=config.JWT_ISSUER, audience=config.JWT_AUDIENCE,
                      leeway=config.JWT_LEEWAY, options={"require": ["exp", "iss", "aud", "sub", "jti", "token_type"]})
```

```python
# src/auth/dependencies.py（骨架示意）
from fastapi import Depends, HTTPException, Header, status
from src.auth.jwt_handler import decode_access
from src.auth.models import Principal

def get_current_principal(authorization: str = Header(default="")) -> Principal:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少 Authorization 头")
    try:
        claims = decode_access(token)
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 无效")
    if claims.get("token_type") != "access":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需 access token")
    # 此处应查 Redis 黑名单校验 jti（略）
    return Principal(user_id=claims["sub"], tenant_id=claims["tenant_id"],
                     roles=claims.get("roles", []), account_ids=claims.get("account_ids", []),
                     jti=claims["jti"], raw_token=token)

def verify_account_access(account_id: str, principal: Principal):
    # 必须先确认 account 属于 principal.tenant_id，再确认 principal 被授权该 account
    ...  # 查 paper.duckdb: SELECT tenant_id FROM accounts WHERE account_id=?
    # if tenant != principal.tenant_id -> 403 TENANT_MISMATCH
    # if account_id not in principal.account_ids and 'admin' not in roles -> 403
```

### C. 与现有约定的对齐说明

- 本规范沿用 `src/config.py` 的 `_load_dotenv()` 加载机制，不引入新的配置框架。
- `Principal` 不写入 `paper.duckdb`，是请求级瞬时对象（仅存在于单次请求生命周期）。
- 缓存 key 结构调整后，建议 `CACHE_VERSION` 递增至 `v2`，强制全量失效一次，避免新旧 key 共存。
- 撮合引擎 `run_daily_match` 沿用全量清缓存；建议新增 `invalidate_tenant` 供按租户失效（可选优化）。

---

## 十四、评审检查清单

开发完成前请逐项确认：

- [ ] `JWT_SECRET` 未硬编码、未入仓库
- [ ] `decode` 显式指定 `algorithms`，拒绝 `none`
- [ ] `iss` / `aud` / `exp` / `nbf` / `token_type` 全部校验
- [ ] 所有模拟盘写操作经过 `verify_account_access`
- [ ] 缓存 key 含 `tenant_id`
- [ ] 跨租户访问测试用例通过
- [ ] `AUTH_MODE=legacy` 可正常回滚
- [ ] 审计日志覆盖关键事件
- [ ] 密码 bcrypt 哈希，无明文
- [ ] Refresh Token 轮转 + Cookie 安全属性齐全

---

**—— 文档结束 ——**

> 本规范书为开发依据，编码实现时如发现规范与实际冲突，请先更新本规范再编码（规范先行）。
