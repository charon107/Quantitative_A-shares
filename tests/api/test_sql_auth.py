"""SQL 网关双模式鉴权测试（规范书 §7.8 / §11.3）。

覆盖：legacy 静态 token（正确/错误/未配置 404）、jwt 模式（scope 校验、
拒绝静态 token）、hybrid 模式（JWT 优先 + 静态 token 回退）、无 token 401。
"""
import pyarrow.ipc as ipc
import pytest
from fastapi.testclient import TestClient

from src import config
from src.api.main import app
from src.auth import config as auth_config
from src.auth import jwt_handler
from tests.auth.conftest import jwt_env  # noqa: F401

SQL_TOKEN = "sql-test-token-0123456789"


@pytest.fixture
def sql_client(monkeypatch, duck, jwt_env):
    """行情库 + JWT 配置 + 静态 token + 租户，默认 hybrid 模式。"""
    from src.auth import store
    store.create_tenant("t_a", "租户A")  # JWT 路径的 _check_tenant_active 需要
    monkeypatch.setattr(config, "SQL_API_TOKEN", SQL_TOKEN)
    monkeypatch.setattr(jwt_env, "AUTH_MODE", "hybrid")
    return TestClient(app)


def _read_rows(resp):
    reader = ipc.open_stream(resp.content)
    return reader.read_all().to_pylist()


def _post(client, headers=None):
    return client.post("/api/sql", json={"sql": "SELECT 1 AS x"},
                       headers=headers or {})


def _jwt_auth(scope=""):
    pair = jwt_handler.issue_token_pair(
        user_id="u_sql", tenant_id="t_a", roles=["trader"], scope=scope)
    return {"Authorization": f"Bearer {pair['access_token']}"}


def test_legacy_static_token_ok(sql_client, monkeypatch):
    """legacy：正确静态 token → 200（现状保持，§9.3 回滚基线）。"""
    monkeypatch.setattr(auth_config, "AUTH_MODE", "legacy")
    r = _post(sql_client, {"Authorization": f"Bearer {SQL_TOKEN}"})
    assert r.status_code == 200, r.text
    assert _read_rows(r) == [{"x": 1}]


def test_legacy_wrong_token_403(sql_client, monkeypatch):
    monkeypatch.setattr(auth_config, "AUTH_MODE", "legacy")
    assert _post(sql_client, {"Authorization": "Bearer wrong-token"}).status_code == 403


def test_legacy_no_token_404(sql_client, monkeypatch):
    """legacy + 未配置静态 token → 端点 404 关闭（安全默认）。"""
    monkeypatch.setattr(auth_config, "AUTH_MODE", "legacy")
    monkeypatch.setattr(config, "SQL_API_TOKEN", "")
    assert _post(sql_client).status_code == 404


def test_jwt_scope_sql_read_ok(sql_client, monkeypatch):
    """jwt：scope 含 sql:read 的 JWT → 200（§7.8）。"""
    monkeypatch.setattr(auth_config, "AUTH_MODE", "jwt")
    r = _post(sql_client, _jwt_auth(scope="sql:read"))
    assert r.status_code == 200
    assert _read_rows(r) == [{"x": 1}]


def test_jwt_without_scope_403(sql_client, monkeypatch):
    """jwt：JWT 有效但 scope 无 sql:read → 403。"""
    monkeypatch.setattr(auth_config, "AUTH_MODE", "jwt")
    r = _post(sql_client, _jwt_auth(scope="paper:write"))
    assert r.status_code == 403


def test_jwt_mode_rejects_static_token(sql_client, monkeypatch):
    """jwt：静态 token 无法作为 JWT 通过 → 401（§2.3 阶段 3，不回退）。"""
    monkeypatch.setattr(auth_config, "AUTH_MODE", "jwt")
    r = _post(sql_client, {"Authorization": f"Bearer {SQL_TOKEN}"})
    assert r.status_code == 401


def test_hybrid_static_token_fallback(sql_client, monkeypatch):
    """hybrid：静态 token 被当 JWT 解析失败 → 回退静态校验通过（§7.8 回退）。"""
    monkeypatch.setattr(auth_config, "AUTH_MODE", "hybrid")
    r = _post(sql_client, {"Authorization": f"Bearer {SQL_TOKEN}"})
    assert r.status_code == 200


def test_hybrid_jwt_priority(sql_client, monkeypatch):
    """hybrid：有效 JWT（sql:read）优先于静态 token。"""
    monkeypatch.setattr(auth_config, "AUTH_MODE", "hybrid")
    r = _post(sql_client, _jwt_auth(scope="sql:read"))
    assert r.status_code == 200


def test_no_token_401(sql_client):
    """无 Authorization 头 → 401。"""
    assert _post(sql_client).status_code == 401
