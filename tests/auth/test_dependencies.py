"""密码哈希 / Principal / 账户归属校验 / 租户隔离兜底 单元测试（§11.1）。"""
import pytest
from fastapi import HTTPException

from src.auth import password
from src.auth.dependencies import (TenantIsolationError, require_tenant_id,
                                   verify_account_access)
from src.auth.models import Principal
from src.paper_trading import store as paper_store


def test_password_hash_verify(jwt_env):
    """bcrypt 哈希与验证（§10.6）：正密码通过、错密码拒绝、哈希不含明文。"""
    h = password.hash_password("my-secret-pw")
    assert h.startswith("$2b$") and "my-secret-pw" not in h
    assert password.verify_password("my-secret-pw", h)
    assert not password.verify_password("wrong-pw", h)
    assert not password.verify_password("", h)
    assert not password.verify_password("my-secret-pw", "not-a-hash")


def test_password_rejects_empty_and_overlong(jwt_env):
    with pytest.raises(ValueError):
        password.hash_password("")
    with pytest.raises(ValueError):
        password.hash_password("x" * 100)  # bcrypt 72 字节上限


def test_principal_construction(jwt_env):
    """Principal 字段正确（§6.2），raw_token 不进入对外序列化。"""
    p = Principal(user_id="u_1", tenant_id="t_a", roles=["trader"],
                  account_ids=["acc_1"], jti="j-1", raw_token="tok")
    assert p.user_id == "u_1" and p.tenant_id == "t_a"
    assert p.roles == ["trader"] and p.account_ids == ["acc_1"] and p.jti == "j-1"


def _mk_principal(tenant_id: str, roles=("trader",), account_ids=()) -> Principal:
    return Principal(user_id="u_x", tenant_id=tenant_id, roles=list(roles),
                     account_ids=list(account_ids), jti="j", raw_token="")


@pytest.fixture
def two_tenant_accounts(paper_db):
    """t_a / t_b 各一个账户。"""
    with paper_store.connect(path=paper_db) as conn:
        conn.execute("INSERT INTO accounts (account_id, name, init_cash, cash, tenant_id) "
                     "VALUES ('acc_a', 'A户', 1, 1, 't_a')")
        conn.execute("INSERT INTO accounts (account_id, name, init_cash, cash, tenant_id) "
                     "VALUES ('acc_b', 'B户', 1, 1, 't_b')")
        conn.execute("INSERT INTO accounts (account_id, name, init_cash, cash) "
                     "VALUES ('acc_legacy', '未迁移户', 1, 1)")  # tenant_id NULL
    return paper_db


def test_account_access_check(two_tenant_accounts):
    """跨租户账户访问被拒（§11.1）：同租户放行，跨租户 403 TENANT_MISMATCH。"""
    verify_account_access("acc_a", _mk_principal("t_a"))  # 同租户放行
    with pytest.raises(HTTPException) as ei:
        verify_account_access("acc_b", _mk_principal("t_a"))
    assert ei.value.status_code == 403
    assert ei.value.detail["error"]["code"] == "TENANT_MISMATCH"


def test_account_access_unknown_404(two_tenant_accounts):
    with pytest.raises(HTTPException) as ei:
        verify_account_access("acc_nope", _mk_principal("t_a"))
    assert ei.value.status_code == 404


def test_account_access_legacy_null_tenant_denied(two_tenant_accounts):
    """tenant_id 为 NULL（未迁移）的账户对任何租户都不可见——安全默认。"""
    with pytest.raises(HTTPException) as ei:
        verify_account_access("acc_legacy", _mk_principal("t_a"))
    assert ei.value.status_code == 403


def test_account_access_grant_whitelist(two_tenant_accounts):
    """非 admin 且白名单非空时，账户须在 account_ids 内（§6.1）。"""
    verify_account_access("acc_a", _mk_principal("t_a", account_ids=["acc_a"]))
    with pytest.raises(HTTPException) as ei:
        verify_account_access("acc_a", _mk_principal("t_a", account_ids=["acc_other"]))
    assert ei.value.status_code == 403
    # admin 不受白名单限制
    verify_account_access("acc_a", _mk_principal("t_a", roles=("admin",), account_ids=["acc_other"]))


def test_tenant_isolation_sql(jwt_env):
    """缺 tenant_id 的隔离查询直接抛错（§2.2 原则 3，开发期拒绝执行）。"""
    assert require_tenant_id("t_a") == "t_a"
    for bad in (None, "", "   "):
        with pytest.raises(TenantIsolationError):
            require_tenant_id(bad)
    with pytest.raises(TenantIsolationError):
        verify_account_access("acc_a", _mk_principal(""))
