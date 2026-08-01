"""tests/auth 共享夹具：JWT 测试配置 + 内存黑名单 + 临时 paper 库。"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src import cache  # noqa: E402
from src.auth import config as auth_config  # noqa: E402
from src.auth import jwt_handler  # noqa: E402
from src.paper_trading import store as paper_store  # noqa: E402

TEST_SECRET = "unit-test-secret-key-0123456789abcdef"  # ≥32 字节
TEST_SECRET_2 = "unit-test-secret-key-2-0123456789abcd"


@pytest.fixture
def jwt_env(monkeypatch):
    """把 auth 配置指向确定性的测试值；禁用 Redis（黑名单按 §4.8 降级）。"""
    monkeypatch.setattr(auth_config, "JWT_SECRET", TEST_SECRET)
    monkeypatch.setattr(auth_config, "JWT_SECRET_PREVIOUS", "")
    monkeypatch.setattr(auth_config, "JWT_ISSUER", "wechatnum-api")
    monkeypatch.setattr(auth_config, "JWT_AUDIENCE", "wechatnum-clients")
    monkeypatch.setattr(auth_config, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(auth_config, "JWT_ACCESS_TTL", 900)
    monkeypatch.setattr(auth_config, "JWT_REFRESH_TTL", 604800)
    monkeypatch.setattr(auth_config, "JWT_LEEWAY", 30)
    monkeypatch.setattr(auth_config, "AUTH_MODE", "jwt")
    monkeypatch.setattr(auth_config, "BCRYPT_ROUNDS", 10)  # 测试提速
    monkeypatch.setattr(cache, "REDIS_ENABLED", False)
    monkeypatch.setattr(cache, "_redis_client", None)
    monkeypatch.setattr(cache, "_redis_available", False)
    return auth_config


@pytest.fixture
def mem_blacklist(monkeypatch):
    """把 jti 黑名单替换为进程内集合（测试撤销/轮转语义；生产路径是 Redis）。"""
    revoked: set[str] = set()
    monkeypatch.setattr(jwt_handler, "is_revoked", lambda jti: jti in revoked)

    def _revoke(jti: str, ttl_seconds: int) -> bool:
        revoked.add(jti)
        return True

    monkeypatch.setattr(jwt_handler, "revoke_jti", _revoke)
    return revoked


@pytest.fixture
def paper_db(tmp_path, monkeypatch):
    """临时 paper.duckdb（含多租户新 schema），PAPER_DUCKDB_PATH 指过去。"""
    path = str(tmp_path / "paper.duckdb")
    with paper_store.connect(path=path) as conn:
        paper_store.init_schema(conn)
    monkeypatch.setenv("PAPER_DUCKDB_PATH", path)
    return path
