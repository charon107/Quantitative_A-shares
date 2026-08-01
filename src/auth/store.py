"""tenants / users / user_account_grants 持久化（规范书 §5.2 / §7.3）。

三张表落在 paper.duckdb（与既有模拟盘业务库同库，避免跨库 JOIN），连接沿用
src.paper_trading.store 的读写短连接范式。所有函数幂等友好：重复创建返回既有行。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from src.paper_trading import store as paper_store


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ========== 租户 ==========

def create_tenant(tenant_id: str, name: str) -> dict:
    """创建租户（已存在则返回既有行，幂等）。"""
    with paper_store.connect() as c:
        row = c.execute("SELECT tenant_id, name, status, created_at FROM tenants WHERE tenant_id = ?",
                        [tenant_id]).fetchone()
        if row is None:
            c.execute("INSERT INTO tenants (tenant_id, name) VALUES (?, ?)", [tenant_id, name])
            row = c.execute("SELECT tenant_id, name, status, created_at FROM tenants WHERE tenant_id = ?",
                            [tenant_id]).fetchone()
        return _tenant_dict(row)


def get_tenant(tenant_id: str) -> dict | None:
    with paper_store.connect() as c:
        row = c.execute("SELECT tenant_id, name, status, created_at FROM tenants WHERE tenant_id = ?",
                        [tenant_id]).fetchone()
        return _tenant_dict(row) if row else None


def set_tenant_status(tenant_id: str, status: str) -> bool:
    """停用/恢复租户（active / suspended / deleted）。返回是否有行被更新。"""
    with paper_store.connect() as c:
        c.execute("UPDATE tenants SET status = ?, updated_at = current_timestamp WHERE tenant_id = ?",
                  [status, tenant_id])
        return c.execute("SELECT COUNT(*) FROM tenants WHERE tenant_id = ?", [tenant_id]).fetchone()[0] > 0


def _tenant_dict(row) -> dict:
    return {"tenant_id": row[0], "name": row[1], "status": row[2],
            "created_at": _fmt_ts(row[3])}


# ========== 用户 ==========

def create_user(tenant_id: str, username: str, password_hash: str,
                roles: list[str] | None = None) -> dict:
    """创建用户（同 (tenant_id, username) 已存在则返回既有行，幂等）。"""
    roles_str = ",".join(roles or ["trader"])
    with paper_store.connect() as c:
        row = c.execute(
            "SELECT user_id, tenant_id, username, roles, status, created_at FROM users "
            "WHERE tenant_id = ? AND username = ?", [tenant_id, username]).fetchone()
        if row is None:
            if c.execute("SELECT COUNT(*) FROM tenants WHERE tenant_id = ?", [tenant_id]).fetchone()[0] == 0:
                raise ValueError(f"租户不存在: {tenant_id}")
            user_id = _new_id("u")
            c.execute(
                "INSERT INTO users (user_id, tenant_id, username, password_hash, roles) "
                "VALUES (?, ?, ?, ?, ?)", [user_id, tenant_id, username, password_hash, roles_str])
            row = c.execute(
                "SELECT user_id, tenant_id, username, roles, status, created_at FROM users "
                "WHERE user_id = ?", [user_id]).fetchone()
        return _user_dict(row)


def get_user_by_username(username: str, tenant_id: str | None = None) -> dict | None:
    """按用户名查用户（含 password_hash，仅登录用）。

    不传 tenant_id 时全局定位：用户名在多租户同时存在时返回 None 的歧义由调用方
    处理（登录路由会要求显式 tenant_id）。
    """
    sql = ("SELECT user_id, tenant_id, username, password_hash, roles, status, created_at "
           "FROM users WHERE username = ?")
    params: list = [username]
    if tenant_id:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    with paper_store.connect() as c:
        rows = c.execute(sql, params).fetchall()
    if len(rows) != 1:
        return None
    return _user_dict_with_hash(rows[0])


def get_user_by_id(user_id: str) -> dict | None:
    with paper_store.connect() as c:
        row = c.execute(
            "SELECT user_id, tenant_id, username, roles, status, created_at FROM users "
            "WHERE user_id = ?", [user_id]).fetchone()
        return _user_dict(row) if row else None


def _user_dict(row) -> dict:
    """无 password_hash 列的用户行（SELECT user_id, tenant_id, username, roles, status, created_at）。"""
    return {"user_id": row[0], "tenant_id": row[1], "username": row[2],
            "roles": [r for r in row[3].split(",") if r], "status": row[4],
            "created_at": _fmt_ts(row[5])}


def _user_dict_with_hash(row) -> dict:
    """含 password_hash 列的用户行（仅登录校验用，禁止外泄到响应）。"""
    return {"user_id": row[0], "tenant_id": row[1], "username": row[2],
            "password_hash": row[3], "roles": [r for r in row[4].split(",") if r],
            "status": row[5], "created_at": _fmt_ts(row[6])}


# ========== 用户 ↔ 账户授权 ==========

def grant_account(user_id: str, account_id: str) -> None:
    """授权用户访问某账户（幂等）。调用方需先校验账户归属租户。"""
    with paper_store.connect() as c:
        c.execute(
            "INSERT INTO user_account_grants (user_id, account_id) "
            "SELECT ?, ? WHERE NOT EXISTS ("
            "  SELECT 1 FROM user_account_grants WHERE user_id = ? AND account_id = ?)",
            [user_id, account_id, user_id, account_id])


def list_grants(user_id: str) -> list[str]:
    with paper_store.connect() as c:
        rows = c.execute(
            "SELECT account_id FROM user_account_grants WHERE user_id = ? ORDER BY granted_at",
            [user_id]).fetchall()
        return [r[0] for r in rows]


def _fmt_ts(value) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)
