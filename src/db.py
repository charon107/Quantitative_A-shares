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
# 公司信息
COMPANY_COLUMNS = [
    "code", "code_name", "fullname", "area", "industry", "market", "list_date",
    "chairman", "manager", "secretary", "reg_capital", "setup_date",
    "province", "city", "employees", "website", "email", "office",
    "main_business", "introduction", "business_scope",
]
# 同花顺人气榜
HOT_COLUMNS = [
    "code", "code_name", "rank_no", "current_price", "pct_change",
    "hot", "concept", "rank_reason", "trade_date",
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

-- 公司信息（来自 tushare stock_basic + stock_company）
CREATE TABLE IF NOT EXISTS stock_info (
    code           VARCHAR PRIMARY KEY,
    code_name      VARCHAR,
    fullname       VARCHAR,
    area           VARCHAR,
    industry       VARCHAR,
    market         VARCHAR,
    list_date      VARCHAR,
    chairman       VARCHAR,
    manager        VARCHAR,
    secretary      VARCHAR,
    reg_capital    DOUBLE,
    setup_date     VARCHAR,
    province       VARCHAR,
    city           VARCHAR,
    employees      BIGINT,
    website        VARCHAR,
    email          VARCHAR,
    office         VARCHAR,
    main_business  VARCHAR,
    introduction   VARCHAR,
    business_scope VARCHAR
);

-- 同花顺人气榜（每日快照，只存最新一日；rank 是 SQL 关键字，列名用 rank_no）
CREATE TABLE IF NOT EXISTS ths_hot (
    code          VARCHAR PRIMARY KEY,
    code_name     VARCHAR,
    rank_no       INTEGER,
    current_price DOUBLE,
    pct_change    DOUBLE,
    hot           DOUBLE,
    concept       VARCHAR,
    rank_reason   VARCHAR,
    trade_date    VARCHAR
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


def upsert_company(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 code UPSERT 公司信息。缺失列补 NULL。"""
    if df is None or df.empty or "code" not in df.columns:
        return 0
    frame = df.copy()
    for col in COMPANY_COLUMNS:
        if col not in frame.columns:
            frame[col] = pd.NA
    frame = frame[COMPANY_COLUMNS].dropna(subset=["code"]).drop_duplicates("code")
    conn.register("_company_in", frame)
    conn.execute(
        f"INSERT OR REPLACE INTO stock_info ({', '.join(COMPANY_COLUMNS)}) "
        f"SELECT {', '.join(COMPANY_COLUMNS)} FROM _company_in"
    )
    conn.unregister("_company_in")
    return len(frame)


def upsert_ths_hot(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 code UPSERT 同花顺人气榜。缺失列补 NULL。"""
    if df is None or df.empty or "code" not in df.columns:
        return 0
    frame = df.copy()
    for col in HOT_COLUMNS:
        if col not in frame.columns:
            frame[col] = pd.NA
    frame = frame[HOT_COLUMNS].dropna(subset=["code"]).drop_duplicates("code")
    conn.register("_hot_in", frame)
    conn.execute(
        f"INSERT OR REPLACE INTO ths_hot ({', '.join(HOT_COLUMNS)}) "
        f"SELECT {', '.join(HOT_COLUMNS)} FROM _hot_in"
    )
    conn.unregister("_hot_in")
    return len(frame)


# 所有按 code 存储的表（退市清理时统一删除）
PURGE_TABLES = ("kline", "raw_kline", "adj_factor", "stock_meta", "stock_info", "ths_hot")


def delete_codes(codes, conn: duckdb.DuckDBPyConnection) -> int:
    """从所有按 code 存储的表删除给定 code（退市清理）。返回删除的 code 数。"""
    codes = [c for c in dict.fromkeys(codes) if c]  # 去重去空，保序
    if not codes:
        return 0
    placeholders = ", ".join(["?"] * len(codes))
    for tbl in PURGE_TABLES:
        conn.execute(f"DELETE FROM {tbl} WHERE code IN ({placeholders})", codes)
    return len(codes)


def delisted_named_codes(conn: duckdb.DuckDBPyConnection) -> set[str]:
    """stock_meta 中名字标记退市的 code（以「退市」开头或以「退」结尾）。

    退市整理期股票仍在交易、tushare 仍是 list_status='L'，list_status='D' 抓不到，
    只能靠命名特征（退市XX / XX退）识别。A股主板几乎不会有正常股名字带这种特征。
    """
    rows = conn.execute(
        "SELECT code FROM stock_meta WHERE code_name LIKE '退市%' OR code_name LIKE '%退'"
    ).fetchall()
    return {r[0] for r in rows}


def purge_delisted(conn: duckdb.DuckDBPyConnection, extra_codes=None) -> int:
    """清理退市股：tushare 的 list_status='D' 集合(extra_codes) ∪ 名字带「退」的。

    需在 upsert_meta 刷新名称之后调用，保证 stock_meta 里是最新名称。返回删除的 code 数。
    """
    codes = set(extra_codes or [])
    codes |= delisted_named_codes(conn)
    return delete_codes(codes, conn)


def atomic_swap(tmp_path: str, dest_path: str | None = None) -> None:
    """把临时库文件原子替换到正式路径（同盘 os.replace 原子）。"""
    dest = dest_path or DUCKDB_PATH
    os.replace(tmp_path, dest)
