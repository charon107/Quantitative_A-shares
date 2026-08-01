"""启动期配置校验单元测试（§7.12 / §8.1 约束）。"""
import pytest

from src.auth import config as auth_config
from src.auth.config import AuthConfigError
from tests.auth.conftest import TEST_SECRET


def test_validate_ok(jwt_env):
    auth_config.validate_startup()  # jwt 模式 + 合法密钥，不抛


def test_validate_rejects_bad_mode(jwt_env):
    jwt_env.AUTH_MODE = "weird"
    with pytest.raises(AuthConfigError):
        auth_config.validate_startup()


def test_validate_rejects_bad_algorithm(jwt_env):
    jwt_env.JWT_ALGORITHM = "none"
    with pytest.raises(AuthConfigError):
        auth_config.validate_startup()


def test_validate_rejects_ttl_over_cap(jwt_env):
    jwt_env.JWT_ACCESS_TTL = 3601
    with pytest.raises(AuthConfigError):
        auth_config.validate_startup()
    jwt_env.JWT_ACCESS_TTL = 900
    jwt_env.JWT_REFRESH_TTL = 30 * 86400 + 1
    with pytest.raises(AuthConfigError):
        auth_config.validate_startup()


def test_validate_rejects_short_secret_in_jwt_mode(jwt_env):
    """AUTH_MODE=jwt 时 JWT_SECRET 缺失/过短拒绝启动（§2.2 原则 4）。"""
    jwt_env.JWT_SECRET = ""
    with pytest.raises(AuthConfigError):
        auth_config.validate_startup()
    jwt_env.JWT_SECRET = "too-short"
    with pytest.raises(AuthConfigError):
        auth_config.validate_startup()


def test_validate_hybrid_missing_secret_warns_only(jwt_env):
    """hybrid 模式缺密钥不拒绝启动（受保护端点运行时 503）。"""
    jwt_env.AUTH_MODE = "hybrid"
    jwt_env.JWT_SECRET = ""
    auth_config.validate_startup()  # 不抛
    assert not auth_config.jwt_ready()
    jwt_env.JWT_SECRET = TEST_SECRET
    assert auth_config.jwt_ready()
