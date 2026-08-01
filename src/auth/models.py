"""鉴权相关 Pydantic 模型（规范书 §4.4 / §6.2 / §7.4）。

``Principal`` 是请求级瞬时对象（由 JWT claims 构造），不持久化、不返回给客户端
的字段（raw_token）仅用于审计。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Principal(BaseModel):
    """当前请求主体（§6.2）。account_ids 为空列表表示可访问租户下全部账户（仅 admin）。"""

    user_id: str
    tenant_id: str
    roles: list[str] = Field(default_factory=list)
    account_ids: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)   # §4.4.3 scope claim（空格分隔字符串解析为列表）
    jti: str                                # 用于撤销/审计
    raw_token: str = ""                     # 审计日志用，不返回给客户端


class TokenPair(BaseModel):
    """登录/刷新成功响应（§4.6）。"""

    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"


class LoginIn(BaseModel):
    """登录请求。users 表按 (tenant_id, username) 唯一；租户内用户名不冲突时
    tenant_id 可省略，服务端全局定位后校验。"""

    username: str
    password: str
    tenant_id: str | None = None


class RefreshIn(BaseModel):
    """刷新请求：refresh_token 可经 body 传入，也可由 HttpOnly Cookie 携带（二选一）。"""

    refresh_token: str | None = None


class MeOut(BaseModel):
    """GET /api/auth/me 响应（Principal 去掉 raw_token）。"""

    user_id: str
    tenant_id: str
    roles: list[str]
    account_ids: list[str]
    jti: str


class TenantCreateIn(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)


class TenantOut(BaseModel):
    tenant_id: str
    name: str
    status: str
    created_at: str


class UserCreateIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str
    roles: list[str] = Field(default_factory=lambda: ["trader"])


class UserOut(BaseModel):
    user_id: str
    tenant_id: str
    username: str
    roles: list[str]
    status: str
    created_at: str


class GrantIn(BaseModel):
    """授权某用户访问租户内某账户。"""

    user_id: str
    account_id: str
