"""模拟盘业务库（paper.duckdb）存储层。

与每日被原子替换的 market.duckdb 完全解耦：独立单文件、可写、短连接
（开→写→关），写操作遇 DuckDB 单写者文件锁冲突时有限重试（与常驻
API 进程/撮合 CLI 并发时的避让）。
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

import duckdb

PAPER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id VARCHAR PRIMARY KEY,
    name       VARCHAR NOT NULL,
    init_cash  DOUBLE  NOT NULL,
    cash       DOUBLE  NOT NULL,   -- 可用资金
    frozen     DOUBLE  NOT NULL DEFAULT 0,  -- 冻结资金（未成交买单占用）
    created_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    status     VARCHAR NOT NULL DEFAULT 'active'
);

-- 委托。UNIQUE(account_id, request_id)：客户端幂等键，防重复提交
CREATE TABLE IF NOT EXISTS orders (
    order_id      VARCHAR PRIMARY KEY,
    account_id    VARCHAR NOT NULL,
    request_id    VARCHAR NOT NULL,
    code          VARCHAR NOT NULL,
    side          VARCHAR NOT NULL,            -- buy / sell
    price_type    VARCHAR NOT NULL,            -- market / limit
    limit_price   DOUBLE,
    qty           INTEGER NOT NULL,
    status        VARCHAR NOT NULL,            -- pending / filled / cancelled / expired / rejected
    reject_reason VARCHAR,
    ref_price     DOUBLE,                      -- 下单时参考价（限价或当时最新收盘价）
    frozen_amount DOUBLE NOT NULL DEFAULT 0,   -- 买单提交时冻结的资金额
    created_at    TIMESTAMP NOT NULL DEFAULT current_timestamp,
    updated_at    TIMESTAMP NOT NULL DEFAULT current_timestamp,
    UNIQUE (account_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_account ON orders(account_id, status);

CREATE TABLE IF NOT EXISTS fills (
    fill_id    VARCHAR PRIMARY KEY,
    order_id   VARCHAR NOT NULL,
    account_id VARCHAR NOT NULL,
    code       VARCHAR NOT NULL,
    side       VARCHAR NOT NULL,
    price      DOUBLE NOT NULL,
    qty        INTEGER NOT NULL,
    amount     DOUBLE NOT NULL,   -- price*qty
    commission DOUBLE NOT NULL,
    stamp_tax  DOUBLE NOT NULL,
    fee        DOUBLE NOT NULL,   -- commission + stamp_tax
    trade_date DATE NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS idx_fills_account ON fills(account_id, trade_date);

CREATE TABLE IF NOT EXISTS positions (
    account_id VARCHAR NOT NULL,
    code       VARCHAR NOT NULL,
    qty        INTEGER NOT NULL,
    cost_price DOUBLE NOT NULL,   -- 摊薄成本（移动加权平均，含买入费用）
    updated_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (account_id, code)
);

-- 资金流水（对账依据，必须可回溯）。balance_after 为变动后可用资金
CREATE TABLE IF NOT EXISTS cash_flows (
    flow_id       VARCHAR PRIMARY KEY,
    account_id    VARCHAR NOT NULL,
    type          VARCHAR NOT NULL,  -- freeze / unfreeze / buy / sell / reset
    amount        DOUBLE NOT NULL,   -- 有符号：正=流入可用资金
    balance_after DOUBLE NOT NULL,
    ref_id        VARCHAR,           -- 关联 order_id / fill_id / reset_id
    created_at    TIMESTAMP NOT NULL DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS idx_flows_account ON cash_flows(account_id, created_at);

-- 每日净值快照（每次撮合运行后按收盘价生成）
CREATE TABLE IF NOT EXISTS equity_snapshots (
    account_id   VARCHAR NOT NULL,
    trade_date   DATE NOT NULL,
    cash         DOUBLE NOT NULL,
    frozen       DOUBLE NOT NULL,
    market_value DOUBLE NOT NULL,
    total_asset  DOUBLE NOT NULL,
    PRIMARY KEY (account_id, trade_date)
);

-- 重置前总览快照（JSON），便于找回与对账
CREATE TABLE IF NOT EXISTS account_resets (
    reset_id      VARCHAR PRIMARY KEY,
    account_id    VARCHAR NOT NULL,
    snapshot_json VARCHAR NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT current_timestamp
);

-- ===== 多租户（规范书 §5.2/§5.3）。ALTER + CREATE INDEX 均幂等，兼容既有库文件 =====

-- accounts 追加 tenant_id（NULL = 待迁移回填，由 scripts/migrate_add_tenant.py 处理）
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS tenant_id VARCHAR;
CREATE INDEX IF NOT EXISTS idx_accounts_tenant ON accounts(tenant_id);

-- 租户表
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   VARCHAR PRIMARY KEY,
    name        VARCHAR NOT NULL,
    status      VARCHAR NOT NULL DEFAULT 'active',  -- active / suspended / deleted
    created_at  TIMESTAMP NOT NULL DEFAULT current_timestamp,
    updated_at  TIMESTAMP NOT NULL DEFAULT current_timestamp
);

-- 用户表（登录主体）
CREATE TABLE IF NOT EXISTS users (
    user_id       VARCHAR PRIMARY KEY,
    tenant_id     VARCHAR NOT NULL,
    username      VARCHAR NOT NULL,          -- 租户内唯一
    password_hash VARCHAR NOT NULL,          -- passlib bcrypt
    roles         VARCHAR NOT NULL DEFAULT 'trader',  -- 逗号分隔：admin,trader
    status        VARCHAR NOT NULL DEFAULT 'active',  -- active / disabled
    created_at    TIMESTAMP NOT NULL DEFAULT current_timestamp,
    updated_at    TIMESTAMP NOT NULL DEFAULT current_timestamp,
    UNIQUE (tenant_id, username)
);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);

-- 用户 ↔ 账户授权（一个用户可访问租户内若干账户；空表表示按角色默认）
CREATE TABLE IF NOT EXISTS user_account_grants (
    user_id    VARCHAR NOT NULL,
    account_id VARCHAR NOT NULL,
    granted_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (user_id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_grants_user ON user_account_grants(user_id);
CREATE INDEX IF NOT EXISTS idx_grants_account ON user_account_grants(account_id);
"""


def default_path() -> str:
    """paper.duckdb 路径：PAPER_DUCKDB_PATH 环境变量优先，默认项目根。"""
    env = os.environ.get("PAPER_DUCKDB_PATH")
    if env:
        return env
    root = Path(__file__).resolve().parents[2]
    return str(root / "paper.duckdb")


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(PAPER_SCHEMA_SQL)


@contextmanager
def connect(path: str | None = None, retries: int = 5, retry_interval: float = 0.5):
    """打开 paper.duckdb 读写连接（不存在则建库并初始化 schema）。

    DuckDB 单写者文件锁冲突（如与撮合 CLI 同时写）时按 retries 重试。
    """
    db_path = path or default_path()
    conn = None
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            conn = duckdb.connect(db_path, read_only=False)
            break
        except duckdb.IOException as e:
            last_err = e
            if attempt < retries:
                time.sleep(retry_interval)
    if conn is None:
        raise last_err  # type: ignore[misc]
    try:
        init_schema(conn)
        yield conn
    finally:
        conn.close()


def database_exists(path: str | None = None) -> bool:
    return Path(path or default_path()).exists()
