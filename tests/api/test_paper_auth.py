"""模拟盘鉴权集成测试（规范书 §7.5 / §11.3）。

覆盖 M4 改造：跨租户越权 403、写操作需 JWT、角色不足 403、create_account 归属租户、
授权白名单、hybrid 无 token 读回退（legacy 凭证）、jwt 模式强制登录。
行情库用根 conftest 的 duck 合成数据，下单类用例可真实走到撮合前置校验。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import auth as auth_routes
from src.auth import dependencies, password, store
from src.paper_trading import store as paper_store
from tests.auth.conftest import TEST_SECRET, jwt_env, mem_blacklist  # noqa: F401

PW = "pass-word-123"


@pytest.fixture
def paper_client(tmp_path, monkeypatch, duck, jwt_env, mem_blacklist):
    """hybrid 模式（生产默认）+ 临时 paper 库 + 双租户/三用户/两账户 + TestClient。"""
    monkeypatch.setattr(jwt_env, "AUTH_MODE", "hybrid")
    monkeypatch.setattr(dependencies, "_AUDIT_LOG", tmp_path / "auth.jsonl")
    monkeypatch.setenv("PAPER_DUCKDB_PATH", str(tmp_path / "paper.duckdb"))
    auth_routes._mem_hits.clear()
    auth_routes._mem_locks.clear()

    store.create_tenant("t_a", "租户A")
    store.create_tenant("t_b", "租户B")
    store.create_user("t_a", "admin_a", password.hash_password(PW), ["admin", "trader"])
    store.create_user("t_a", "viewer_a", password.hash_password(PW), ["viewer"])
    store.create_user("t_b", "trader_b", password.hash_password(PW), ["trader"])
    with paper_store.connect() as conn:
        conn.execute("INSERT INTO accounts (account_id, name, init_cash, cash, tenant_id) "
                     "VALUES ('acc_a', 'A户', 100000, 100000, 't_a')")
        conn.execute("INSERT INTO accounts (account_id, name, init_cash, cash, tenant_id) "
                     "VALUES ('acc_b', 'B户', 100000, 100000, 't_b')")
    return TestClient(app)


def _login(client, username, pw=PW):
    r = client.post("/api/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _order_body():
    return {"request_id": uuid.uuid4().hex, "code": "sh.600000", "side": "buy",
            "price_type": "market", "qty": 100}


def test_cross_tenant_read_forbidden(paper_client):
    """A 租户用户读 B 租户账户 → 403 TENANT_MISMATCH（§11.3 越权）。"""
    tok = _login(paper_client, "admin_a")
    r = paper_client.get("/api/paper/accounts/acc_b/overview", headers=_bearer(tok))
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "TENANT_MISMATCH"


def test_cross_tenant_write_forbidden(paper_client):
    """A 租户用户写 B 租户账户 → 403。"""
    tok = _login(paper_client, "admin_a")
    r = paper_client.post("/api/paper/accounts/acc_b/orders",
                          json=_order_body(), headers=_bearer(tok))
    assert r.status_code == 403


def test_write_requires_login(paper_client):
    """hybrid 模式无 token 写 → 401（写操作必须登录，§9.2 阶段 1）。"""
    r = paper_client.post("/api/paper/accounts/acc_a/orders", json=_order_body())
    assert r.status_code == 401


def test_viewer_cannot_write(paper_client):
    """viewer 角色写 → 403（§7.5 写操作需 trader/admin）。"""
    tok = _login(paper_client, "viewer_a")
    r = paper_client.post("/api/paper/accounts/acc_a/orders",
                          json=_order_body(), headers=_bearer(tok))
    assert r.status_code == 403


def test_hybrid_read_legacy_fallback(paper_client):
    """hybrid 无 token 读 → account_id 凭证回退（§7.5 仅读 + DEPRECATED）。"""
    r = paper_client.get("/api/paper/accounts/acc_a/overview")
    assert r.status_code == 200
    assert r.json()["account_id"] == "acc_a"


def test_jwt_mode_no_token_read_forbidden(paper_client, monkeypatch):
    """jwt 模式无 token 读 → 401（强制登录，不降级，§2.3 阶段 3）。"""
    from src.auth import config as auth_config
    monkeypatch.setattr(auth_config, "AUTH_MODE", "jwt")
    r = paper_client.get("/api/paper/accounts/acc_a/overview")
    assert r.status_code == 401


def test_create_account_belongs_to_tenant(paper_client):
    """create_account 归属 principal.tenant_id，忽略客户端（§7.5）。"""
    tok = _login(paper_client, "admin_a")
    r = paper_client.post("/api/paper/accounts",
                          json={"name": "新户", "init_cash": 50000}, headers=_bearer(tok))
    assert r.status_code == 201, r.text
    with paper_store.connect() as conn:
        tenant = conn.execute(
            "SELECT tenant_id FROM accounts WHERE account_id = ?",
            [r.json()["account_id"]]).fetchone()[0]
    assert tenant == "t_a"


def test_list_my_accounts_tenant_scoped(paper_client):
    """GET /api/paper/accounts 只返回本租户账户（§7.11 登录态拉取）。"""
    tok = _login(paper_client, "admin_a")
    r = paper_client.get("/api/paper/accounts", headers=_bearer(tok))
    assert r.status_code == 200
    ids = {i["account_id"] for i in r.json()["items"]}
    assert ids == {"acc_a"}


def test_granted_account_whitelist(paper_client):
    """授权白名单：viewer 只能访问被授权的账户（§6.1）。"""
    client = paper_client
    tok_admin = _login(client, "admin_a")
    viewer_id = store.get_user_by_username("viewer_a", "t_a")["user_id"]
    r = client.post("/api/tenants/t_a/grants",
                    json={"user_id": viewer_id, "account_id": "acc_a"},
                    headers=_bearer(tok_admin))
    assert r.status_code == 201, r.text

    tok_viewer = _login(client, "viewer_a")
    assert client.get("/api/paper/accounts/acc_a/overview",
                      headers=_bearer(tok_viewer)).status_code == 200
    assert client.get("/api/paper/accounts/acc_b/overview",
                      headers=_bearer(tok_viewer)).status_code == 403  # 跨租户

    new_id = client.post("/api/paper/accounts",
                         json={"name": "未授权户", "init_cash": 1},
                         headers=_bearer(tok_viewer)).json()["account_id"]
    assert client.get(f"/api/paper/accounts/{new_id}/overview",
                      headers=_bearer(tok_viewer)).status_code == 403  # 同租户但未授权


def test_legacy_mode_write_allowed(paper_client, monkeypatch):
    """legacy 模式写操作回滚（§9.3）：account_id 凭证直接可用，业务校验走 service。"""
    from src.auth import config as auth_config
    monkeypatch.setattr(auth_config, "AUTH_MODE", "legacy")
    r = paper_client.post("/api/paper/accounts/acc_a/orders", json=_order_body())
    assert r.status_code == 201, r.text  # 下单成功（真实行情 + legacy 凭证）
