"""鉴权流程集成测试（规范书 §11.2 / §11.3）。

TestClient + tmp_path 临时 paper 库 + freezegun 冻结时间。黑名单走内存桩
（生产路径是 Redis；Redis 缺席时按 §4.8 降级为不信任撤销，无法测轮转）。
M4 的 paper 路由越权用例（跨租户访问业务端点）本阶段不含，归属校验由
tests/auth/test_dependencies.py 在依赖层覆盖。
"""
import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import auth as auth_routes
from src.auth import dependencies, password, store
from tests.auth.conftest import TEST_SECRET, TEST_SECRET_2, jwt_env, mem_blacklist  # noqa: F401

PW = "pass-word-123"


@pytest.fixture
def auth_client(tmp_path, monkeypatch, jwt_env, mem_blacklist):
    """临时 paper 库 + 双租户种子数据 + TestClient。审计日志重定向到 tmp。"""
    monkeypatch.setenv("PAPER_DUCKDB_PATH", str(tmp_path / "paper.duckdb"))
    monkeypatch.setattr(dependencies, "_AUDIT_LOG", tmp_path / "auth.jsonl")
    auth_routes._mem_hits.clear()   # 进程内限流计数清零，避免跨用例干扰
    auth_routes._mem_locks.clear()
    store.create_tenant("t_a", "租户A")
    store.create_tenant("t_b", "租户B")
    store.create_user("t_a", "admin_a", password.hash_password(PW), ["admin", "trader"])
    store.create_user("t_b", "trader_b", password.hash_password(PW), ["trader"])
    return TestClient(app)


def _login(client, username, password=PW, tenant_id=None):
    body = {"username": username, "password": password}
    if tenant_id:
        body["tenant_id"] = tenant_id
    return client.post("/api/auth/login", json=body)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_full_flow(auth_client):
    """登录 → me → 租户/用户管理 → 授权 → 刷新（轮转）→ 登出（§11.2 主流程）。"""
    client = auth_client
    r = _login(client, "admin_a")
    assert r.status_code == 200, r.text
    pair = r.json()
    assert pair["token_type"] == "Bearer" and pair["expires_in"] == 900
    # Refresh Cookie：HttpOnly + SameSite=Strict + Path=/api/auth（§7.11）
    set_cookie = r.headers["set-cookie"]
    assert "wn_refresh=" in set_cookie and "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie and "Path=/api/auth" in set_cookie

    me = client.get("/api/auth/me", headers=_bearer(pair["access_token"]))
    assert me.status_code == 200
    body = me.json()
    assert body["tenant_id"] == "t_a" and "admin" in body["roles"]
    assert "raw_token" not in body

    # 租户管理（§6.3 管理级）：admin 可创建租户；只能管本租户
    assert client.post("/api/tenants", json={"tenant_id": "t_c", "name": "租户C"},
                       headers=_bearer(pair["access_token"])).status_code == 201
    assert client.post("/api/tenants/t_a/users",
                       json={"username": "trader_a2", "password": "pw-2-12345"},
                       headers=_bearer(pair["access_token"])).status_code == 201
    assert client.post("/api/tenants/t_c/users",
                       json={"username": "x", "password": "pw-x-12345"},
                       headers=_bearer(pair["access_token"])).status_code == 403

    # 模拟盘建户（M4：JWT 归属租户 t_a，自动写入 tenant_id）→ 授权
    acc = client.post("/api/paper/accounts", json={"name": "流程户", "init_cash": 100000},
                      headers=_bearer(pair["access_token"]))
    assert acc.status_code == 201, acc.text
    account_id = acc.json()["account_id"]
    user_id = store.get_user_by_username("trader_a2", "t_a")["user_id"]
    g = client.post("/api/tenants/t_a/grants", json={"user_id": user_id, "account_id": account_id},
                    headers=_bearer(pair["access_token"]))
    assert g.status_code == 201, g.text

    # 刷新（Cookie 携带）→ 新双 Token；旧 refresh 二次使用 → 401（§11.3 轮转）
    old_refresh = pair["refresh_token"]
    r2 = client.post("/api/auth/refresh")
    assert r2.status_code == 200, r2.text
    new_pair = r2.json()
    assert new_pair["access_token"] != pair["access_token"]
    assert client.post("/api/auth/refresh",
                       json={"refresh_token": old_refresh}).status_code == 401

    # 登出 → 旧 access 重放 → 401（§11.3 重放）
    assert client.post("/api/auth/logout",
                       headers=_bearer(new_pair["access_token"])).status_code == 200
    assert client.get("/api/auth/me",
                      headers=_bearer(new_pair["access_token"])).status_code == 401


def test_login_wrong_password_and_unknown_user(auth_client):
    """失败统一 INVALID_CREDENTIALS，不区分用户不存在/密码错误（§10.4）。"""
    r1 = _login(auth_client, "admin_a", "wrong-pw")
    r2 = _login(auth_client, "no_such_user")
    assert r1.status_code == 401 and r2.status_code == 401
    assert r1.json()["detail"] == r2.json()["detail"] == "INVALID_CREDENTIALS"


def test_login_lockout_after_5_failures(auth_client):
    """连续失败 5 次锁定 15 分钟：锁定后正确密码也 429（§10.4）。"""
    for _ in range(5):
        assert _login(auth_client, "trader_b", "bad").status_code == 401
    assert _login(auth_client, "trader_b").status_code == 429


def test_me_requires_token(auth_client):
    assert auth_client.get("/api/auth/me").status_code == 401


def test_access_expired(auth_client):
    """过期 access token → 401 TOKEN_EXPIRED（§11.2）。

    注意：不用 freezegun + TestClient 组合——TestClient 的 worker 线程跑 ASGI app
    时读的是真实时钟（freezegun 冻结只对当前线程生效），时间类用例直接构造
    已过期的 token 更确定。
    """
    now = int(time.time())
    expired = pyjwt.encode(
        {"iss": "wechatnum-api", "aud": "wechatnum-clients",
         "sub": "admin_a", "tenant_id": "t_a", "roles": ["admin"],
         "iat": now - 3600, "nbf": now - 3600, "exp": now - 60,
         "jti": "expired-jti-001", "token_type": "access"},
        TEST_SECRET, algorithm="HS256")
    r = auth_client.get("/api/auth/me", headers=_bearer(expired))
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "TOKEN_EXPIRED"


def test_refresh_token_cannot_access_business_api(auth_client):
    """用 refresh token 访问业务端点 → 403 TOKEN_TYPE_MISMATCH（§6.4）。"""
    pair = _login(auth_client, "admin_a").json()
    r = auth_client.get("/api/auth/me", headers=_bearer(pair["refresh_token"]))
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "TOKEN_TYPE_MISMATCH"


def test_tampered_payload_rejected(auth_client):
    """篡改 payload（提权 admin）→ 验签失败 401（§11.3）。"""
    pair = _login(auth_client, "trader_b").json()
    claims = pyjwt.decode(pair["access_token"], options={"verify_signature": False})
    claims["roles"] = ["admin"]
    forged = pyjwt.encode(claims, TEST_SECRET_2, algorithm="HS256")  # 攻击者没有真密钥
    assert auth_client.get("/api/auth/me", headers=_bearer(forged)).status_code == 401


def test_require_roles_forbidden(auth_client):
    """非 admin 访问租户管理 → 403（§6.3 管理级）。"""
    pair = _login(auth_client, "trader_b").json()
    r = auth_client.post("/api/tenants", json={"tenant_id": "t_x", "name": "X"},
                         headers=_bearer(pair["access_token"]))
    assert r.status_code == 403


def test_missing_secret_returns_503(auth_client, monkeypatch):
    """JWT_SECRET 缺失时受保护端点 503（§2.2 原则 4，安全默认关闭）。"""
    from src.auth import config as auth_config
    monkeypatch.setattr(auth_config, "JWT_SECRET", "")
    assert auth_client.get("/api/auth/me", headers=_bearer("any")).status_code == 503
    assert _login(auth_client, "admin_a").status_code == 503


def test_audit_log_written(auth_client, tmp_path):
    """登录成功/失败均落审计日志 logs/auth.jsonl（§10.5，测试重定向到 tmp）。"""
    _login(auth_client, "admin_a", "bad")
    _login(auth_client, "admin_a")
    content = (tmp_path / "auth.jsonl").read_text(encoding="utf-8")
    assert "login_failed" in content and "login_success" in content
    assert '"tenant_id": "t_a"' in content
