"""JWT 配置加载与启动期校验（规范书 §四 / §八 / §7.12）。

取值统一定义在 ``src/config.py``（沿用 ``_load_dotenv()`` 机制，不引入新配置框架），
本模块 re-export 并补充合法性校验：

- ``AUTH_MODE`` 仅允许 legacy / hybrid / jwt；
- ``JWT_ALGORITHM`` 仅允许 HS256（§4.3，防算法降级）；
- TTL 上限：Access ≤ 1h，Refresh ≤ 30 天（§4.7）；
- ``JWT_LEEWAY`` 0-300s、``PASSWORD_MIN_LEN`` ≥ 8、``BCRYPT_ROUNDS`` 10-14；
- ``AUTH_MODE=jwt`` 时 ``JWT_SECRET`` 必填且 ≥32 字节，缺失拒绝启动（§2.2 原则 4）；
  hybrid 模式下缺失仅告警——受保护端点运行时返回 503。
"""
from __future__ import annotations

import logging

from src import config as _base  # noqa: F401  # 触发仓库根 .env 加载

logger = logging.getLogger(__name__)

# re-export（调用方一律经本模块属性读取，便于测试 monkeypatch）
AUTH_MODE = _base.AUTH_MODE
JWT_SECRET = _base.JWT_SECRET
JWT_SECRET_PREVIOUS = _base.JWT_SECRET_PREVIOUS
JWT_ISSUER = _base.JWT_ISSUER
JWT_AUDIENCE = _base.JWT_AUDIENCE
JWT_ALGORITHM = _base.JWT_ALGORITHM
JWT_ACCESS_TTL = _base.JWT_ACCESS_TTL
JWT_REFRESH_TTL = _base.JWT_REFRESH_TTL
JWT_LEEWAY = _base.JWT_LEEWAY
JWT_COOKIE_NAME = _base.JWT_COOKIE_NAME
JWT_COOKIE_SECURE = _base.JWT_COOKIE_SECURE
REFRESH_ROTATE = _base.REFRESH_ROTATE
PASSWORD_MIN_LEN = _base.PASSWORD_MIN_LEN
BCRYPT_ROUNDS = _base.BCRYPT_ROUNDS
DEFAULT_TENANT_ID = _base.DEFAULT_TENANT_ID
LEGACY_ACCOUNT_ID_AUTH = _base.LEGACY_ACCOUNT_ID_AUTH

_VALID_MODES = ("legacy", "hybrid", "jwt")
_ACCESS_TTL_MAX = 3600            # §4.7：Access 严禁 > 1 小时
_REFRESH_TTL_MAX = 30 * 86400     # §4.7：Refresh 严禁 > 30 天
_SECRET_MIN_BYTES = 32            # §4.2


class AuthConfigError(RuntimeError):
    """鉴权配置非法（启动期校验失败，拒绝启动）。"""


def jwt_ready() -> bool:
    """当前密钥是否足以签发/校验 JWT（运行时受保护端点据此返回 503）。"""
    return bool(JWT_SECRET) and len(JWT_SECRET.encode("utf-8")) >= _SECRET_MIN_BYTES


def validate_startup() -> None:
    """启动期配置校验（由 src/api/main.py 的 lifespan 调用）。

    非法取值一律抛 AuthConfigError 拒绝启动；仅「hybrid 模式缺 JWT_SECRET」
    降级为告警（§2.2 原则 4：受保护端点返回 503 而非无鉴权放行）。
    """
    if AUTH_MODE not in _VALID_MODES:
        raise AuthConfigError(f"AUTH_MODE 非法: {AUTH_MODE!r}（仅允许 {_VALID_MODES}）")
    if JWT_ALGORITHM != "HS256":
        raise AuthConfigError(f"JWT_ALGORITHM 非法: {JWT_ALGORITHM!r}（仅允许 HS256）")
    if not (0 < JWT_ACCESS_TTL <= _ACCESS_TTL_MAX):
        raise AuthConfigError(f"JWT_ACCESS_TTL 越界: {JWT_ACCESS_TTL}（须 1-{_ACCESS_TTL_MAX} 秒）")
    if not (0 < JWT_REFRESH_TTL <= _REFRESH_TTL_MAX):
        raise AuthConfigError(f"JWT_REFRESH_TTL 越界: {JWT_REFRESH_TTL}（须 1-{_REFRESH_TTL_MAX} 秒）")
    if not (0 <= JWT_LEEWAY <= 300):
        raise AuthConfigError(f"JWT_LEEWAY 越界: {JWT_LEEWAY}（须 0-300 秒）")
    if PASSWORD_MIN_LEN < 8:
        raise AuthConfigError(f"PASSWORD_MIN_LEN 越界: {PASSWORD_MIN_LEN}（须 ≥8）")
    if not (10 <= BCRYPT_ROUNDS <= 14):
        raise AuthConfigError(f"BCRYPT_ROUNDS 越界: {BCRYPT_ROUNDS}（须 10-14）")

    if AUTH_MODE == "jwt" and not jwt_ready():
        raise AuthConfigError("AUTH_MODE=jwt 时 JWT_SECRET 必填且 ≥32 字节，拒绝启动")
    if AUTH_MODE == "hybrid" and not jwt_ready():
        logger.warning("AUTH_MODE=hybrid 但 JWT_SECRET 未配置/过短：受保护端点将返回 503")
    if JWT_SECRET_PREVIOUS and len(JWT_SECRET_PREVIOUS.encode("utf-8")) < _SECRET_MIN_BYTES:
        logger.warning("JWT_SECRET_PREVIOUS 长度 <32 字节，轮换校验可能被弱密钥攻击，请更换")
