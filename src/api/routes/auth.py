"""鉴权路由（规范书 §7.4）：/api/auth/login /refresh /logout /me。

- 登录签发 Access + Refresh 双 Token；Refresh 同时经 HttpOnly Cookie 下发
  （§7.11：HttpOnly + SameSite=Strict + Path=/api/auth，生产置 Secure——
  由 JWT_COOKIE_SECURE 控制，本地 http 开发默认关）。
- Refresh 轮转（§4.5）：刷新即签发新双 Token，旧 refresh jti 撤销（一次性使用）。
- 限流与防爆破（§10.4）：同 IP 10 次/分、同 username 5 次/分，连续失败 5 次锁定
  15 分钟；失败响应不区分「用户不存在」与「密码错误」（INVALID_CREDENTIALS）。
  Redis 计数优先；Redis 不可用时降级为进程内内存计数（单进程部署语义等价，
  多进程部署需 Redis 才严格——已在部署文档注明）。
- 审计（§10.5）：登录成功/失败、刷新、登出均写 logs/auth.jsonl。
"""
from __future__ import annotations

import logging
import time

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from src import cache
from src.auth import config, jwt_handler, password, store
from src.auth.dependencies import audit_log, get_current_principal
from src.auth.models import LoginIn, MeOut, Principal, RefreshIn, TokenPair

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# ---- 限流参数（§10.4）----
_RATE_WINDOW = 60          # 计数窗口（秒）
_RATE_IP_MAX = 10          # 同 IP 每分钟上限
_RATE_USER_MAX = 5         # 同 username 每分钟上限
_LOCK_FAILS = 5            # 连续失败锁定阈值
_LOCK_SECONDS = 15 * 60    # 锁定时长

# 进程内降级计数（Redis 不可用时）：key -> 事件时间戳列表 / 锁定截止
_mem_hits: dict[str, list[float]] = {}
_mem_locks: dict[str, float] = {}

# 用户不存在时用于拉平校验耗时的假哈希（防用户枚举的时序侧信道，§10.4）
_dummy_hash: str | None = None


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _hit(key: str, limit: int) -> bool:
    """滑动窗口计数 +1，返回是否已超限。Redis 优先，失败降级进程内内存。"""
    now = time.time()
    r = cache._get_redis()
    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, _RATE_WINDOW)
            count, _ = pipe.execute()
            return int(count) > limit
        except Exception as exc:
            logger.warning("限流计数失败 (%s)，降级进程内计数", exc)
    hits = [t for t in _mem_hits.get(key, []) if now - t < _RATE_WINDOW]
    hits.append(now)
    _mem_hits[key] = hits
    return len(hits) > limit


def _locked(username: str) -> bool:
    key = f"rl:login:lock:{username}"
    r = cache._get_redis()
    if r is not None:
        try:
            return bool(r.exists(key))
        except Exception:
            pass
    return _mem_locks.get(key, 0) > time.time()


def _record_login_failure(username: str) -> None:
    """连续失败计数，达到阈值锁定 username 15 分钟。"""
    now = time.time()
    fail_key = f"rl:login:fail:{username}"
    lock_key = f"rl:login:lock:{username}"
    r = cache._get_redis()
    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.incr(fail_key)
            pipe.expire(fail_key, _LOCK_SECONDS)
            count, _ = pipe.execute()
            if int(count) >= _LOCK_FAILS:
                r.set(lock_key, b"1", ex=_LOCK_SECONDS)
            return
        except Exception:
            pass
    fails = [t for t in _mem_hits.get(fail_key, []) if now - t < _LOCK_SECONDS]
    fails.append(now)
    _mem_hits[fail_key] = fails
    if len(fails) >= _LOCK_FAILS:
        _mem_locks[lock_key] = now + _LOCK_SECONDS


def _clear_login_failures(username: str) -> None:
    fail_key = f"rl:login:fail:{username}"
    r = cache._get_redis()
    if r is not None:
        try:
            r.delete(fail_key)
        except Exception:
            pass
    _mem_hits.pop(fail_key, None)


def _check_rate(ip: str, username: str) -> None:
    if _locked(username):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="失败次数过多，账号已临时锁定，请 15 分钟后再试")
    if _hit(f"rl:login:ip:{ip}", _RATE_IP_MAX) or _hit(f"rl:login:user:{username}", _RATE_USER_MAX):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="请求过于频繁，请稍后再试")


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """下发 Refresh Cookie（§7.11）：HttpOnly + SameSite=Strict + Path=/api/auth。"""
    response.set_cookie(
        config.JWT_COOKIE_NAME,
        refresh_token,
        max_age=config.JWT_REFRESH_TTL,
        path="/api/auth",
        httponly=True,
        secure=config.JWT_COOKIE_SECURE,   # 生产 HTTPS 必须 true（§10.2）
        samesite="strict",
    )


def _invalid_credentials() -> HTTPException:
    """统一失败响应：不区分用户不存在 / 密码错误 / 账号停用（§10.4 防枚举）。"""
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail="INVALID_CREDENTIALS")


def _verify_or_dummy(raw_password: str, password_hash: str | None) -> bool:
    """用户不存在也用假哈希跑一次 bcrypt，拉平响应耗时。"""
    global _dummy_hash
    if password_hash is None:
        if _dummy_hash is None:
            _dummy_hash = password.hash_password("dummy-password-for-timing")
        password.verify_password(raw_password, _dummy_hash)
        return False
    return password.verify_password(raw_password, password_hash)


@router.post("/auth/login", response_model=TokenPair)
def login(body: LoginIn, request: Request, response: Response):
    """用户名密码登录（§4.6 签发流程）。"""
    if not config.jwt_ready():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="服务端未配置 JWT_SECRET，登录不可用")
    ip = _client_ip(request)
    _check_rate(ip, body.username)

    user = store.get_user_by_username(body.username, body.tenant_id)
    # get_user_by_username 在未指定 tenant_id 且用户名跨租户重复时返回 None（歧义），
    # 与「不存在」同等处理——多租户同用户名场景请显式传 tenant_id。
    ok = _verify_or_dummy(body.password, user["password_hash"] if user else None)
    if not ok or user["status"] != "active":
        _record_login_failure(body.username)
        audit_log("login_failed", tenant_id=body.tenant_id or (user["tenant_id"] if user else ""),
                  user_id=user["user_id"] if user else "", ip=ip, detail=body.username)
        raise _invalid_credentials()
    tenant = store.get_tenant(user["tenant_id"])
    if tenant is None or tenant["status"] != "active":
        audit_log("login_failed", tenant_id=user["tenant_id"], user_id=user["user_id"],
                  ip=ip, detail="租户停用")
        raise _invalid_credentials()

    _clear_login_failures(body.username)
    account_ids = store.list_grants(user["user_id"])
    pair = jwt_handler.issue_token_pair(
        user_id=user["user_id"], tenant_id=user["tenant_id"],
        roles=user["roles"], account_ids=account_ids)
    _set_refresh_cookie(response, pair["refresh_token"])
    audit_log("login_success", tenant_id=user["tenant_id"], user_id=user["user_id"], ip=ip)
    return pair


def _extract_refresh_token(request: Request, body: RefreshIn | None) -> str:
    """refresh token 从 HttpOnly Cookie 或 body 二选一获取（§7.4/§7.11）。"""
    token = (body.refresh_token if body else None) or request.cookies.get(config.JWT_COOKIE_NAME, "")
    return token.strip()


@router.post("/auth/refresh", response_model=TokenPair)
def refresh(request: Request, response: Response, body: RefreshIn | None = None):
    """Refresh Token 换新双 Token（轮转：旧 refresh 一次性作废，§4.5）。"""
    token = _extract_refresh_token(request, body)
    ip = _client_ip(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="缺少 refresh token")
    try:
        claims = jwt_handler.decode_refresh(token)
    except jwt_handler.JwtNotConfiguredError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="服务端未配置 JWT_SECRET")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token 已过期，请重新登录")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token 无效")

    if jwt_handler.is_revoked(claims["jti"]):
        audit_log("refresh_reuse", tenant_id=claims["tenant_id"], user_id=claims["sub"],
                  jti=claims["jti"], ip=ip, detail="已撤销的 refresh token 被重用")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token 已被撤销")
    tenant = store.get_tenant(claims["tenant_id"])
    if tenant is None or tenant["status"] != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="租户已停用")

    if config.REFRESH_ROTATE:
        jwt_handler.revoke_jti(claims["jti"], jwt_handler.remaining_ttl(claims))
    pair = jwt_handler.issue_token_pair(
        user_id=claims["sub"], tenant_id=claims["tenant_id"],
        roles=claims.get("roles", []), account_ids=claims.get("account_ids", []),
        scope=claims.get("scope", ""))
    _set_refresh_cookie(response, pair["refresh_token"])
    audit_log("token_refresh", tenant_id=claims["tenant_id"], user_id=claims["sub"],
              jti=claims["jti"], ip=ip)
    return pair


@router.post("/auth/logout")
def logout(request: Request, response: Response,
           principal: Principal = Depends(get_current_principal)):
    """登出：撤销当前 access jti 与（若携带的）refresh jti（§4.8 主动登出）。"""
    claims = jwt_handler.decode_access(principal.raw_token)  # 已校验过，取 exp 算剩余 TTL
    jwt_handler.revoke_jti(principal.jti, jwt_handler.remaining_ttl(claims))
    refresh_token = request.cookies.get(config.JWT_COOKIE_NAME, "")
    if refresh_token:
        try:
            rclaims = jwt_handler.decode_refresh(refresh_token)
            jwt_handler.revoke_jti(rclaims["jti"], jwt_handler.remaining_ttl(rclaims))
        except jwt.PyJWTError:
            pass  # refresh 已失效无需撤销
    response.delete_cookie(config.JWT_COOKIE_NAME, path="/api/auth")
    audit_log("logout", tenant_id=principal.tenant_id, user_id=principal.user_id,
              jti=principal.jti, ip=_client_ip(request))
    return {"status": "ok"}


@router.get("/auth/me", response_model=MeOut)
def me(principal: Principal = Depends(get_current_principal)):
    """返回当前 Principal（不含 raw_token，§7.4）。"""
    return MeOut(user_id=principal.user_id, tenant_id=principal.tenant_id,
                 roles=principal.roles, account_ids=principal.account_ids, jti=principal.jti)
