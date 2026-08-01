"""FastAPI 鉴权依赖（规范书 §六）。

- ``get_current_principal``  解析 JWT → Principal（401/403/503，见 §6.4 错误规范）
- ``require_roles(*roles)``  角色白名单（403）
- ``require_tenant_admin``   快捷要求 admin 角色（403）
- ``verify_account_access``  账户归属 + 授权校验（403 TENANT_MISMATCH，集中实现，
                             各路由禁止自行实现——§5.4.3）
- ``require_tenant_id``      租户隔离强制规则的开发期兜底（§2.2 原则 3：
                             缺 tenant_id 的隔离表查询直接抛错）

审计：越权/角色不足等 403 事件写 logs/auth.jsonl（§10.5），日志不落密钥与 token 本体。
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

import jwt
from fastapi import Depends, Header, HTTPException, Request, status

from src.auth import config, jwt_handler, store
from src.auth.models import Principal
from src.paper_trading import store as paper_store

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_AUDIT_DIR = _ROOT / "logs"
# 默认 None → 按日轮转（logs/auth-YYYYMMDD.jsonl，§10.5）。测试可覆写为固定文件路径。
_AUDIT_LOG: Path | None = None


class TenantIsolationError(RuntimeError):
    """缺少 tenant_id 的租户隔离查询（§2.2 原则 3：隔离失败即拒绝，开发期抛错）。"""


def require_tenant_id(tenant_id: str | None) -> str:
    """租户隔离强制规则兜底：tenant_id 缺失/为空直接抛错，拒绝执行。"""
    if not tenant_id or not tenant_id.strip():
        raise TenantIsolationError("租户隔离查询缺少 tenant_id，拒绝执行")
    return tenant_id


def _log_path() -> Path:
    """审计日志路径：默认按日轮转（logs/auth-YYYYMMDD.jsonl，§10.5）；测试可覆写固定路径。"""
    if _AUDIT_LOG is not None:
        return _AUDIT_LOG
    return _AUDIT_DIR / f"auth-{datetime.now(timezone.utc):%Y%m%d}.jsonl"


def audit_log(event: str, *, tenant_id: str = "", user_id: str = "",
              jti: str = "", ip: str = "", detail: str = "") -> None:
    """写一条结构化审计日志（§10.5，按日轮转）。日志失败不阻塞请求。"""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "jti": jti,
        "ip": ip,
        "detail": detail,
    }
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("审计日志写入失败 (%s): %s", exc, event)


def auth_error(status_code: int, code: str, message: str) -> HTTPException:
    """统一错误结构（§6.4）：{"error": {"code", "message"}}。供路由层复用。"""
    return HTTPException(status_code=status_code,
                         detail={"error": {"code": code, "message": message}})


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return auth_error(status_code, code, message)


def get_optional_principal(
    request: Request,
    authorization: str = Header(default=""),
) -> Principal | None:
    """模拟盘读操作鉴权（§7.5 hybrid 降级 + §2.3 legacy 回滚）。

    - 携带有效 JWT → Principal（含租户停用/黑名单等完整校验，见 get_current_principal）；
    - 无 token 且 ``AUTH_MODE ∈ {hybrid, legacy}`` 且 ``LEGACY_ACCOUNT_ID_AUTH`` 允许
      → 返回 None（由路由按 account_id 凭证降级，仅读 + DEPRECATED 日志）；
    - 其余情况（jwt 模式无 token / 携带无效 token）→ 401/403/503，不降级
      （无效 token 不降级，防止伪造凭证冒充旧客户端，§10.3）。

    写操作不使用本依赖（见 src/api/routes/paper.py 的 _authorize_write）。
    """
    if not authorization.strip():
        if config.AUTH_MODE in ("hybrid", "legacy") and config.LEGACY_ACCOUNT_ID_AUTH:
            return None
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "缺少 Authorization 头")
    return get_current_principal(request, authorization)


def get_current_principal(
    request: Request,
    authorization: str = Header(default=""),
) -> Principal:
    """解析 Bearer JWT → Principal（§4.6 校验流程）。

    失败响应：缺头/无效 401，过期 401（TOKEN_EXPIRED），refresh 误用 403
    （TOKEN_TYPE_MISMATCH），租户停用 403（TENANT_SUSPENDED），JWT_SECRET
    未配置 503（§2.2 原则 4：安全默认关闭，不降级为无鉴权）。
    """
    if not config.jwt_ready():
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE,
                     "AUTH_NOT_CONFIGURED", "服务端未配置 JWT_SECRET，受保护端点不可用")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "缺少 Authorization 头")
    try:
        claims = jwt_handler.decode_access(token)
    except jwt_handler.JwtNotConfiguredError:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE,
                     "AUTH_NOT_CONFIGURED", "服务端未配置 JWT_SECRET，受保护端点不可用")
    except jwt.ExpiredSignatureError:
        raise _error(status.HTTP_401_UNAUTHORIZED, "TOKEN_EXPIRED", "token 已过期")
    except jwt.InvalidTokenError as e:
        if "token_type" in str(e):
            raise _error(status.HTTP_403_FORBIDDEN,
                         "TOKEN_TYPE_MISMATCH", "业务端点需 access token")
        raise _error(status.HTTP_401_UNAUTHORIZED, "TOKEN_INVALID", "token 无效")
    except jwt.PyJWTError:
        raise _error(status.HTTP_401_UNAUTHORIZED, "TOKEN_INVALID", "token 无效")

    if jwt_handler.is_revoked(claims["jti"]):
        raise _error(status.HTTP_401_UNAUTHORIZED, "TOKEN_REVOKED", "token 已被撤销")

    principal = Principal(
        user_id=claims["sub"],
        tenant_id=claims["tenant_id"],
        roles=claims.get("roles", []),
        account_ids=claims.get("account_ids", []),
        scope=claims.get("scope", "").split(),
        jti=claims["jti"],
        raw_token=token,
    )
    _check_tenant_active(principal, request)
    return principal


def _check_tenant_active(principal: Principal, request: Request) -> None:
    """租户状态校验：停用/删除/不存在的租户一律 403（§6.4 TENANT_SUSPENDED）。"""
    tenant = store.get_tenant(principal.tenant_id)
    if tenant is None or tenant["status"] != "active":
        ip = request.client.host if request.client else ""
        audit_log("tenant_suspended", tenant_id=principal.tenant_id,
                  user_id=principal.user_id, jti=principal.jti, ip=ip,
                  detail=f"status={tenant['status'] if tenant else 'missing'}")
        raise _error(status.HTTP_403_FORBIDDEN, "TENANT_SUSPENDED", "租户已停用或不存在")


def require_roles(*roles: str):
    """角色白名单依赖工厂：principal 角色与白名单无交集 → 403（§6.4 FORBIDDEN）。"""
    def _dep(request: Request, principal: Principal = Depends(get_current_principal)) -> Principal:
        if not set(principal.roles) & set(roles):
            ip = request.client.host if request.client else ""
            audit_log("forbidden", tenant_id=principal.tenant_id, user_id=principal.user_id,
                      jti=principal.jti, ip=ip, detail=f"需要角色 {roles}，实际 {principal.roles}")
            raise _error(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "角色权限不足")
        return principal
    return _dep


def require_tenant_admin(request: Request,
                         principal: Principal = Depends(get_current_principal)) -> Principal:
    """快捷依赖：要求 admin 角色。"""
    if "admin" not in principal.roles:
        ip = request.client.host if request.client else ""
        audit_log("forbidden", tenant_id=principal.tenant_id, user_id=principal.user_id,
                  jti=principal.jti, ip=ip, detail="需要 admin 角色")
        raise _error(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "需要租户管理员角色")
    return principal


def verify_account_access(account_id: str, principal: Principal,
                          request: Request | None = None) -> None:
    """账户归属 + 授权校验（§5.4 / §6.1）。

    1. 账户不存在 → 404（不泄露归属信息，与不存在同等对待）。
    2. ``accounts.tenant_id`` 与 ``principal.tenant_id`` 常数时间比较（§10.3.6），
       不符 → 403 TENANT_MISMATCH + 审计。
    3. 非 admin 且 account_ids 白名单非空时，账户须在白名单内 → 否则 403。
    """
    require_tenant_id(principal.tenant_id)
    with paper_store.connect() as conn:
        row = conn.execute("SELECT tenant_id FROM accounts WHERE account_id = ?",
                           [account_id]).fetchone()
    if row is None:
        raise _error(status.HTTP_404_NOT_FOUND, "ACCOUNT_NOT_FOUND", "账户不存在")

    account_tenant = row[0] or ""
    if not secrets.compare_digest(account_tenant, principal.tenant_id):
        ip = request.client.host if request and request.client else ""
        audit_log("tenant_mismatch", tenant_id=principal.tenant_id, user_id=principal.user_id,
                  jti=principal.jti, ip=ip, detail=f"account_id={account_id}")
        raise _error(status.HTTP_403_FORBIDDEN, "TENANT_MISMATCH", "账户不属于当前租户")

    if ("admin" not in principal.roles
            and principal.account_ids
            and account_id not in principal.account_ids):
        ip = request.client.host if request and request.client else ""
        audit_log("forbidden", tenant_id=principal.tenant_id, user_id=principal.user_id,
                  jti=principal.jti, ip=ip, detail=f"未授权账户 {account_id}")
        raise _error(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "未被授权访问该账户")
