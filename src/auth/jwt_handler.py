"""JWT 签发 / 校验 / 撤销（规范书 §4.2-§4.8）。

安全要点（§10.3 强制）：
  - 校验显式 ``algorithms=[JWT_ALGORITHM]`` 白名单，绝不接受 ``alg=none`` 或 token 自报算法；
  - 必传 ``issuer`` / ``audience``，``options.require`` 强制标准 claims 齐全；
  - 业务端点只接受 ``token_type == "access"``，refresh token 仅可用于 /api/auth/refresh；
  - 密钥轮换：校验先当前密钥、失败回退 ``JWT_SECRET_PREVIOUS``；签发一律用当前密钥。

撤销黑名单（§4.8）：Redis ``jwt:revoked:{jti}``，TTL = token 剩余有效期。
Redis 不可用时按规范降级为「不信任撤销、token 自然过期」：黑名单查询静默跳过
（记告警、不阻塞请求，Access TTL 仅 15 分钟可控）。Redis 访问复用 src.cache 的
延迟连接/静默降级范式，不改动 cache.py。
"""
from __future__ import annotations

import logging
import time
import uuid

import jwt

from src import cache
from src.auth import config

logger = logging.getLogger(__name__)

_REQUIRED_CLAIMS = ["exp", "nbf", "iat", "iss", "aud", "sub", "jti", "token_type", "tenant_id"]


class JwtNotConfiguredError(RuntimeError):
    """JWT_SECRET 未配置/过短——受保护端点应返回 503（§2.2 原则 4，安全默认关闭）。"""


def _require_secret() -> str:
    if not config.jwt_ready():
        raise JwtNotConfiguredError("JWT_SECRET 未配置或长度不足（≥32 字节）")
    return config.JWT_SECRET


def _issue(claims: dict, ttl: int) -> tuple[str, str]:
    """按当前密钥签发，返回 (token, jti)。"""
    now = int(time.time())
    jti = str(uuid.uuid4())
    payload = {
        "iss": config.JWT_ISSUER,
        "aud": config.JWT_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": jti,
        **claims,
    }
    token = jwt.encode(payload, _require_secret(), algorithm=config.JWT_ALGORITHM)
    return token, jti


def issue_token_pair(
    user_id: str,
    tenant_id: str,
    roles: list[str],
    account_ids: list[str] | None = None,
    scope: str = "",
) -> dict:
    """签发 Access + Refresh 双 Token（§4.5/§4.6）。claims 结构相同，TTL/用途不同。"""
    common = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": list(roles),
        "account_ids": list(account_ids or []),
    }
    if scope:
        common["scope"] = scope
    access, _ = _issue({**common, "token_type": "access"}, config.JWT_ACCESS_TTL)
    refresh, _ = _issue({**common, "token_type": "refresh"}, config.JWT_REFRESH_TTL)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": config.JWT_ACCESS_TTL,
        "token_type": "Bearer",
    }


def _decode(token: str) -> dict:
    """验签 + 标准 claims 校验；当前密钥失败时回退 JWT_SECRET_PREVIOUS（§4.2 轮换）。"""
    keys = [_require_secret()]
    if config.JWT_SECRET_PREVIOUS:
        keys.append(config.JWT_SECRET_PREVIOUS)
    last_err: jwt.PyJWTError | None = None
    for key in keys:
        try:
            return jwt.decode(
                token,
                key,
                algorithms=[config.JWT_ALGORITHM],  # 白名单，拒绝 alg=none / 算法降级
                issuer=config.JWT_ISSUER,
                audience=config.JWT_AUDIENCE,
                leeway=config.JWT_LEEWAY,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.InvalidSignatureError as e:
            last_err = e  # 签名不符才尝试轮换密钥；过期/受众错等立即抛出
    raise last_err  # type: ignore[misc]


def decode_access(token: str) -> dict:
    """校验业务用 Access Token，返回 claims。refresh token 一律拒绝（§4.5）。"""
    claims = _decode(token)
    if claims.get("token_type") != "access":
        raise jwt.InvalidTokenError("token_type 非 access")
    return claims


def decode_refresh(token: str) -> dict:
    """校验 Refresh Token（仅 /api/auth/refresh 使用）。"""
    claims = _decode(token)
    if claims.get("token_type") != "refresh":
        raise jwt.InvalidTokenError("token_type 非 refresh")
    return claims


# ========== jti 撤销黑名单（Redis，缺席时按 §4.8 降级）==========

def _revoked_key(jti: str) -> str:
    return f"jwt:revoked:{jti}"


def revoke_jti(jti: str, ttl_seconds: int) -> bool:
    """把 jti 写入黑名单，TTL = token 剩余有效期。Redis 不可用返回 False（降级生效）。"""
    if ttl_seconds <= 0:
        return True  # 已自然过期，无需写入
    r = cache._get_redis()
    if r is None:
        logger.warning("Redis 不可用，jti 撤销降级失效（token 将自然过期）: %s", jti)
        return False
    try:
        r.set(_revoked_key(jti), b"1", ex=ttl_seconds)
        return True
    except Exception as exc:  # 黑名单写入失败不阻塞请求（§4.8 可用性优先）
        logger.warning("jti 撤销写入失败 (%s): %s", exc, jti)
        return False


def is_revoked(jti: str) -> bool:
    """查询 jti 是否被撤销。Redis 不可用/查询失败静默返回 False（§4.8 降级）。"""
    r = cache._get_redis()
    if r is None:
        return False
    try:
        return bool(r.exists(_revoked_key(jti)))
    except Exception as exc:
        logger.warning("黑名单查询失败 (%s)，按未撤销放行: %s", exc, jti)
        return False


def remaining_ttl(claims: dict) -> int:
    """token 剩余有效期（秒），用于撤销黑名单 TTL。"""
    return max(0, int(claims.get("exp", 0)) - int(time.time()))
