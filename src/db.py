"""DuckDB 仓储层 — 看板只读查询 + 入库写入的统一入口。

设计：
  - 看板/API 侧用 ``connect(read_only=True)`` 短连接（开→查→关）。DuckDB 的只读连接
    允许多进程并发读，互不阻塞。
  - 入库侧写一个临时库文件，再 ``atomic_swap`` 原子替换，避免与正在读取的进程争锁。
  - 连接显式限制 ``memory_limit`` / ``threads``，适配 1.6GB 内存的小服务器。

环境变量：
  - ``DUCKDB_PATH``           数据库文件路径（默认 ``market.duckdb``）
  - ``DUCKDB_MEMORY_LIMIT``   单连接内存上限（默认 ``400MB``）
  - ``DUCKDB_THREADS``        线程数（默认 ``2``）
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pandas as pd

# ========== 配置 ==========
DUCKDB_PATH = os.environ.get("DUCKDB_PATH", "market.duckdb")
MEMORY_LIMIT = os.environ.get("DUCKDB_MEMORY_LIMIT", "400MB")
THREADS = int(os.environ.get("DUCKDB_THREADS", "2"))

# kline 表的规范列顺序（与 tushare_client.compute_qfq 产出一致）
KLINE_COLUMNS = [
    "code", "date", "open", "high", "low", "close",
    "volume", "amount", "pctChg", "turn", "adjustflag",
]
# 原始未复权日线（入库内部，用于分红后重算前复权）
RAW_COLUMNS = [
    "code", "date", "open", "high", "low", "close",
    "volume", "amount", "pctChg", "turn",
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kline (
    code       VARCHAR NOT NULL,
    date       DATE    NOT NULL,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    close      DOUBLE,
    volume     DOUBLE,
    amount     DOUBLE,
    pctChg     DOUBLE,
    turn       DOUBLE,
    adjustflag VARCHAR,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_kline_date ON kline(date);

CREATE TABLE IF NOT EXISTS stock_meta (
    code      VARCHAR PRIMARY KEY,
    code_name VARCHAR
);

-- 入库内部：原始未复权日线（永久保留，分红时重算 qfq 的依据）
CREATE TABLE IF NOT EXISTS raw_kline (
    code   VARCHAR NOT NULL,
    date   DATE    NOT NULL,
    open   DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    volume DOUBLE, amount DOUBLE, pctChg DOUBLE, turn DOUBLE,
    PRIMARY KEY (code, date)
);

-- 入库内部：复权因子历史
CREATE TABLE IF NOT EXISTS adj_factor (
    code       VARCHAR NOT NULL,
    trade_date DATE    NOT NULL,
    adj_factor DOUBLE,
    PRIMARY KEY (code, trade_date)
);
"""


def _apply_pragmas(conn: duckdb.DuckDBPyConnection) -> None:
    """对每个连接施加内存/线程限制（失败不致命，仅退化为默认值）。"""
    try:
        conn.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
        conn.execute(f"SET threads={THREADS}")
    except duckdb.Error:
        pass


@contextmanager
def connect(read_only: bool = True, path: str | None = None):
    """打开一个 DuckDB 连接的上下文管理器，退出时自动关闭。

    read_only=True 时若库文件不存在会抛 duckdb.Error，由调用方决定如何降级。
    """
    db_path = path or DUCKDB_PATH
    conn = duckdb.connect(db_path, read_only=read_only)
    try:
        _apply_pragmas(conn)
        yield conn
    finally:
        conn.close()


def query_df(sql: str, params: list | tuple | None = None, *, path: str | None = None) -> pd.DataFrame:
    """只读执行一条 SQL，返回 DataFrame。开→查→关，适合短查询。"""
    with connect(read_only=True, path=path) as conn:
        return conn.execute(sql, list(params) if params else []).df()


def database_exists(path: str | None = None) -> bool:
    """库文件是否存在。"""
    return Path(path or DUCKDB_PATH).exists()


# ========== 写入（入库侧使用） ==========
def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """在给定（读写）连接上建表与索引（幂等）。"""
    conn.execute(SCHEMA_SQL)


def upsert_kline(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 (code, date) UPSERT 前复权日线。返回写入行数。"""
    if df is None or df.empty:
        return 0
    frame = df.copy()
    # 缺失的可选列补齐，保证列齐全；多余列忽略
    for col in KLINE_COLUMNS:
        if col not in frame.columns:
            frame[col] = pd.NA
    frame = frame[KLINE_COLUMNS]
    conn.register("_kline_in", frame)
    # date 列显式转 DATE，避免 timestamp/字符串隐式转换报错
    select_cols = ", ".join(
        "CAST(date AS DATE) AS date" if c == "date" else c for c in KLINE_COLUMNS
    )
    conn.execute(
        f"INSERT OR REPLACE INTO kline ({', '.join(KLINE_COLUMNS)}) "
        f"SELECT {select_cols} FROM _kline_in"
    )
    conn.unregister("_kline_in")
    return len(frame)


def upsert_meta(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 code UPSERT 代码->名称映射。期望列 code/code_name。返回写入行数。"""
    if df is None or df.empty or "code" not in df.columns:
        return 0
    frame = df.copy()
    if "code_name" not in frame.columns:
        frame["code_name"] = pd.NA
    frame = frame[["code", "code_name"]].dropna(subset=["code"]).drop_duplicates("code")
    conn.register("_meta_in", frame)
    conn.execute(
        "INSERT OR REPLACE INTO stock_meta (code, code_name) "
        "SELECT code, code_name FROM _meta_in"
    )
    conn.unregister("_meta_in")
    return len(frame)


def upsert_raw(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 (code, date) UPSERT 原始未复权日线。"""
    if df is None or df.empty:
        return 0
    frame = df.copy()
    for col in RAW_COLUMNS:
        if col not in frame.columns:
            frame[col] = pd.NA
    frame = frame[RAW_COLUMNS]
    conn.register("_raw_in", frame)
    select_cols = ", ".join(
        "CAST(date AS DATE) AS date" if c == "date" else c for c in RAW_COLUMNS
    )
    conn.execute(
        f"INSERT OR REPLACE INTO raw_kline ({', '.join(RAW_COLUMNS)}) "
        f"SELECT {select_cols} FROM _raw_in"
    )
    conn.unregister("_raw_in")
    return len(frame)


def upsert_adj(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 (code, trade_date) UPSERT 复权因子。期望列 code/trade_date/adj_factor。"""
    if df is None or df.empty:
        return 0
    frame = df.copy()[["code", "trade_date", "adj_factor"]]
    conn.register("_adj_in", frame)
    conn.execute(
        "INSERT OR REPLACE INTO adj_factor (code, trade_date, adj_factor) "
        "SELECT code, CAST(trade_date AS DATE) AS trade_date, adj_factor FROM _adj_in"
    )
    conn.unregister("_adj_in")
    return len(frame)


def existing_raw_codes(conn: duckdb.DuckDBPyConnection) -> set[str]:
    """raw_kline 中已存在的全部 code（用于识别全新上市股票）。"""
    rows = conn.execute("SELECT DISTINCT code FROM raw_kline").fetchall()
    return {r[0] for r in rows}


def read_raw(code: str, conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """读某 code 的完整原始日线（按 date 升序）。"""
    return conn.execute(
        "SELECT * FROM raw_kline WHERE code = ? ORDER BY date", [code]
    ).df()


def read_adj(code: str, conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """读某 code 的完整复权因子（trade_date 升序）。列名兼容 compute_qfq。"""
    return conn.execute(
        "SELECT code, trade_date, adj_factor FROM adj_factor WHERE code = ? ORDER BY trade_date",
        [code],
    ).df()


def atomic_swap(tmp_path: str, dest_path: str | None = None) -> None:
    """把临时库文件原子替换到正式路径（同盘 os.replace 原子）。"""
    dest = dest_path or DUCKDB_PATH
    os.replace(tmp_path, dest)
