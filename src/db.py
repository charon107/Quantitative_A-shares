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

# 当前 schema 版本（存 meta_kv；迁移脚本据此判断是否需要迁移）
SCHEMA_VERSION = 2

# kline 表的规范列顺序（compute_qfq 产出的多余列如 adjustflag 在 upsert 时被忽略）
KLINE_COLUMNS = [
    "code", "date", "open", "high", "low", "close",
    "volume", "amount", "pctChg", "turn",
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
# 基本面财务（年报口径，来自 tushare fina_indicator + income + cashflow + balancesheet；
# debt_ratio = 总债务(短期借款+长期借款+应付债券)/总资产，策略条件3 的有息负债口径）
FUNDAMENTAL_COLUMNS = [
    "code", "year", "ann_date", "roe", "netprofit_yoy",
    "debt_ratio", "net_profit", "cfo",
]
# 逐年回测选股池（来自 fundamental_screen.run_selection）
SELECTED_COLUMNS = ["year", "code", "code_name"]
# 季度基本面（展示用；与 tushare_client.QUARTERLY_METRIC_COLUMNS 的指标部分保持一致，
# 有单测守护两者同步）。累计(YTD)列与 q_ 单季列并存，q_net_profit/q_revenue/q_cfo
# 由采集层差分预计算（Q1=累计）；比率为小数，金额为元
QUARTERLY_FUNDAMENTAL_COLUMNS = [
    "code", "end_date", "year", "quarter", "ann_date",
    "roe", "roe_dt", "roa", "netprofit_margin", "grossprofit_margin",
    "net_profit", "profit_dedt", "revenue", "eps", "bps", "debt_to_assets",
    "or_yoy", "netprofit_yoy", "dt_netprofit_yoy", "cfo",
    "total_assets", "total_liab", "total_debt",
    "q_roe", "q_dt_roe", "q_netprofit_margin", "q_gsprofit_margin",
    "q_net_profit", "q_revenue", "q_cfo",
    "q_sales_yoy", "q_sales_qoq", "q_netprofit_yoy", "q_netprofit_qoq",
]
# 估值日频（daily_basic；total_mv/circ_mv 万元、dv_* 百分数，按 tushare 原单位入库）
VALUATION_COLUMNS = [
    "code", "date", "pe", "pe_ttm", "pb", "ps", "ps_ttm",
    "dv_ratio", "dv_ttm", "total_mv", "circ_mv",
]
# 分红送股（已实施；cash_div 税后/cash_div_tax 税前 每股分红元，stk_div 每股送转股）
DIVIDEND_TABLE_COLUMNS = [
    "code", "end_date", "ann_date", "div_proc", "stk_div", "cash_div", "cash_div_tax",
    "record_date", "ex_date", "pay_date",
]
# 业绩预告（p_change ÷100 归一小数；net_profit_min/max 元）
FORECAST_TABLE_COLUMNS = [
    "code", "end_date", "ann_date", "type", "p_change_min", "p_change_max",
    "net_profit_min", "net_profit_max", "change_reason",
]
# 业绩快报（金额元；diluted_roe/yoy_* ÷100 归一小数）
EXPRESS_TABLE_COLUMNS = [
    "code", "end_date", "ann_date", "revenue", "operate_profit", "total_profit", "n_income",
    "diluted_eps", "bps", "diluted_roe", "yoy_sales", "yoy_op", "yoy_dedu_np",
]

SCHEMA_SQL = """
-- 元信息键值表（schema_version 等）
CREATE TABLE IF NOT EXISTS meta_kv (
    key   VARCHAR PRIMARY KEY,
    value VARCHAR
);

-- pctChg/turn 用 FLOAT：展示精度 2 位小数，7 位有效数字足够；OHLC/量额保持 DOUBLE
CREATE TABLE IF NOT EXISTS kline (
    code       VARCHAR NOT NULL,
    date       DATE    NOT NULL,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    close      DOUBLE,
    volume     DOUBLE,
    amount     DOUBLE,
    pctChg     FLOAT,
    turn       FLOAT,
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
    volume DOUBLE, amount DOUBLE, pctChg FLOAT, turn FLOAT,
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

-- 基本面财务（年报口径；roe/netprofit_yoy 已在采集层归一为小数；
-- debt_ratio = 总债务(短借+长借+应付债券)/总资产，策略条件3 的有息负债口径）
-- 比率列 FLOAT 足够（展示 0.01%）；net_profit/cfo 是元级绝对金额，保持 DOUBLE
CREATE TABLE IF NOT EXISTS stock_fundamental (
    code           VARCHAR NOT NULL,
    year           INTEGER NOT NULL,
    ann_date       VARCHAR,
    roe            FLOAT,
    netprofit_yoy  FLOAT,
    debt_ratio     FLOAT,
    net_profit     DOUBLE,
    cfo            DOUBLE,
    PRIMARY KEY (code, year)
);

-- 指数日线收盘价（如上证综指 sh.000001，用于选股股价图对照；指数不参与退市清理）
CREATE TABLE IF NOT EXISTS index_daily (
    code  VARCHAR NOT NULL,
    date  DATE    NOT NULL,
    close DOUBLE,
    PRIMARY KEY (code, date)
);

-- 逐年回测选股池（每年选出的股票；整表随每次选股全量替换）
CREATE TABLE IF NOT EXISTS selected_stocks (
    year      INTEGER NOT NULL,
    code      VARCHAR NOT NULL,
    code_name VARCHAR,
    PRIMARY KEY (year, code)
);

-- 季度基本面（展示用；选股仍用年度表 stock_fundamental，两表独立）。
-- 比率已在采集层 ÷100 归一为小数，金额为元；q_ 前缀为单季口径（差分预计算，Q1=累计）
CREATE TABLE IF NOT EXISTS stock_fundamental_quarterly (
    code               VARCHAR NOT NULL,
    end_date           VARCHAR NOT NULL,  -- 报告期末 'YYYY-MM-DD'
    year               INTEGER NOT NULL,
    quarter            TINYINT NOT NULL,  -- 1..4
    ann_date           VARCHAR,
    roe                FLOAT,
    roe_dt             FLOAT,
    roa                FLOAT,
    netprofit_margin   FLOAT,
    grossprofit_margin FLOAT,
    net_profit         DOUBLE,
    profit_dedt        DOUBLE,
    revenue            DOUBLE,
    eps                FLOAT,
    bps                FLOAT,
    debt_to_assets     FLOAT,
    or_yoy             FLOAT,
    netprofit_yoy      FLOAT,
    dt_netprofit_yoy   FLOAT,
    cfo                DOUBLE,
    total_assets       DOUBLE,
    total_liab         DOUBLE,
    total_debt         DOUBLE,  -- 有息总债务=短借+长借+应付债券（选股 debt_ratio 分子口径）
    q_roe              FLOAT,
    q_dt_roe           FLOAT,
    q_netprofit_margin FLOAT,
    q_gsprofit_margin  FLOAT,
    q_net_profit       DOUBLE,
    q_revenue          DOUBLE,
    q_cfo              DOUBLE,
    q_sales_yoy        FLOAT,
    q_sales_qoq        FLOAT,
    q_netprofit_yoy    FLOAT,
    q_netprofit_qoq    FLOAT,
    PRIMARY KEY (code, end_date)
);

-- 估值日频（daily_basic：PE/PB/PS/股息率/市值；total_mv/circ_mv 万元、dv_* 百分数）
CREATE TABLE IF NOT EXISTS stock_valuation_daily (
    code     VARCHAR NOT NULL,
    date     DATE    NOT NULL,
    pe       FLOAT,
    pe_ttm   FLOAT,
    pb       FLOAT,
    ps       FLOAT,
    ps_ttm   FLOAT,
    dv_ratio FLOAT,
    dv_ttm   FLOAT,
    total_mv DOUBLE,
    circ_mv  DOUBLE,
    PRIMARY KEY (code, date)
);

-- 分红送股（仅已实施；end_date 为分红年度报告期，中期分红为 06-30 等）
CREATE TABLE IF NOT EXISTS stock_dividend (
    code         VARCHAR NOT NULL,
    end_date     VARCHAR NOT NULL,
    ann_date     VARCHAR,
    div_proc     VARCHAR,
    stk_div      FLOAT,   -- 每股送转（股）
    cash_div     FLOAT,   -- 每股分红·税后（元）
    cash_div_tax FLOAT,   -- 每股分红·税前（元）
    record_date  VARCHAR,
    ex_date      VARCHAR,
    pay_date     VARCHAR,
    PRIMARY KEY (code, end_date)
);

-- 业绩预告（同一报告期多次修正预告按公告日区分保留）
CREATE TABLE IF NOT EXISTS stock_forecast (
    code           VARCHAR NOT NULL,
    end_date       VARCHAR NOT NULL,
    ann_date       VARCHAR NOT NULL,
    type           VARCHAR,  -- 预增/预减/扭亏/首亏/续亏/续盈/略增/略减
    p_change_min   FLOAT,    -- 预告净利润变动幅度下限（小数）
    p_change_max   FLOAT,
    net_profit_min DOUBLE,   -- 预告净利润下限（元）
    net_profit_max DOUBLE,
    change_reason  VARCHAR,
    PRIMARY KEY (code, end_date, ann_date)
);

-- 业绩快报（正式财报前的先行数据；金额元，比率小数）
CREATE TABLE IF NOT EXISTS stock_express (
    code           VARCHAR NOT NULL,
    end_date       VARCHAR NOT NULL,
    ann_date       VARCHAR,
    revenue        DOUBLE,
    operate_profit DOUBLE,
    total_profit   DOUBLE,
    n_income       DOUBLE,
    diluted_eps    FLOAT,
    bps            FLOAT,
    diluted_roe    FLOAT,
    yoy_sales      FLOAT,
    yoy_op         FLOAT,
    yoy_dedu_np    FLOAT,
    PRIMARY KEY (code, end_date)
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
def acquire_ingest_lock(path: str | None = None):
    """入库互斥锁：防止多个「copy 生产库 → 写临时库 → swap」进程并发互踩丢数据
    （load_all/load_fundamentals/load_valuation 共用同一把锁）。进程退出自动释放。

    非阻塞：已被占用时直接 SystemExit（workflow 失败可重跑，比静默丢写入安全）。
    Linux（服务器）用 flock；Windows（本地开发）无 fcntl，跳过。
    """
    try:
        import fcntl
    except ImportError:
        return None
    lock_path = path or (DUCKDB_PATH + ".ingest.lock")
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise SystemExit(f"[db] 已有入库进程在运行（{lock_path} 被锁），本次退出")
    return fh


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


def read_raw_since(code: str, since, conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """读某 code 自 since（含当天）起的原始日线（按 date 升序）。"""
    return conn.execute(
        "SELECT * FROM raw_kline WHERE code = ? AND date >= ? ORDER BY date",
        [code, since],
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


def ensure_fundamental_schema(conn: duckdb.DuckDBPyConnection) -> bool:
    """stock_fundamental 若还是旧结构（debt_to_assets 列 = 总负债口径）则整表重建。

    该表每次基本面刷新都由全量 parquet 重写，DROP+重建比列改名干净——避免旧口径
    数值残留在新列名下。返回是否发生了重建。调用前需先 init_schema 保证表存在。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info('stock_fundamental')").fetchall()}
    if "debt_ratio" in cols:
        return False
    conn.execute("DROP TABLE stock_fundamental")
    init_schema(conn)
    return True


def ensure_quarterly_schema(conn: duckdb.DuckDBPyConnection) -> bool:
    """stock_fundamental_quarterly 若还是旧结构（缺 total_debt 列）则整表重建。

    该表每次基本面刷新都由全量 parquet 重写，DROP+重建比 ALTER 干净（同
    ensure_fundamental_schema 的先例）。返回是否发生了重建。调用前需先 init_schema。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info('stock_fundamental_quarterly')").fetchall()}
    if "total_debt" in cols:
        return False
    conn.execute("DROP TABLE stock_fundamental_quarterly")
    init_schema(conn)
    return True


def upsert_fundamental(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 (code, year) UPSERT 年报财务。缺失列补 NULL。"""
    if df is None or df.empty or "code" not in df.columns:
        return 0
    frame = df.copy()
    for col in FUNDAMENTAL_COLUMNS:
        if col not in frame.columns:
            frame[col] = pd.NA
    frame = frame[FUNDAMENTAL_COLUMNS].dropna(subset=["code", "year"]).drop_duplicates(["code", "year"])
    conn.register("_fund_in", frame)
    conn.execute(
        f"INSERT OR REPLACE INTO stock_fundamental ({', '.join(FUNDAMENTAL_COLUMNS)}) "
        f"SELECT {', '.join(FUNDAMENTAL_COLUMNS)} FROM _fund_in"
    )
    conn.unregister("_fund_in")
    return len(frame)


def _upsert_generic(
    table: str,
    columns: list[str],
    key_cols: list[str],
    df: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    date_cols: tuple[str, ...] = (),
) -> int:
    """按主键 INSERT OR REPLACE 的通用 upsert：缺失列补 NULL、键列去空去重、
    date_cols 显式 CAST 为 DATE。返回写入行数。"""
    if df is None or df.empty or "code" not in df.columns:
        return 0
    frame = df.copy()
    for col in columns:
        if col not in frame.columns:
            frame[col] = pd.NA
    frame = frame[columns].dropna(subset=key_cols).drop_duplicates(key_cols)
    reg = f"_{table}_in"
    conn.register(reg, frame)
    select_cols = ", ".join(
        f"CAST({c} AS DATE) AS {c}" if c in date_cols else c for c in columns
    )
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) SELECT {select_cols} FROM {reg}"
    )
    conn.unregister(reg)
    return len(frame)


def upsert_fundamental_quarterly(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 (code, end_date) UPSERT 季度基本面。缺失列补 NULL。"""
    return _upsert_generic(
        "stock_fundamental_quarterly", QUARTERLY_FUNDAMENTAL_COLUMNS, ["code", "end_date"], df, conn
    )


def upsert_valuation_daily(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 (code, date) UPSERT 估值日频。缺失列补 NULL。"""
    return _upsert_generic(
        "stock_valuation_daily", VALUATION_COLUMNS, ["code", "date"], df, conn, date_cols=("date",)
    )


def upsert_dividend(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 (code, end_date) UPSERT 分红送股（已实施）。缺失列补 NULL。"""
    return _upsert_generic("stock_dividend", DIVIDEND_TABLE_COLUMNS, ["code", "end_date"], df, conn)


def upsert_forecast(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 (code, end_date, ann_date) UPSERT 业绩预告。缺失列补 NULL。"""
    return _upsert_generic(
        "stock_forecast", FORECAST_TABLE_COLUMNS, ["code", "end_date", "ann_date"], df, conn
    )


def upsert_express(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 (code, end_date) UPSERT 业绩快报。缺失列补 NULL。"""
    return _upsert_generic("stock_express", EXPRESS_TABLE_COLUMNS, ["code", "end_date"], df, conn)


def upsert_index_daily(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """按 (code, date) UPSERT 指数日线。期望列 code/date/close。"""
    if df is None or df.empty:
        return 0
    frame = df.copy()[["code", "date", "close"]]
    conn.register("_idx_in", frame)
    conn.execute(
        "INSERT OR REPLACE INTO index_daily (code, date, close) "
        "SELECT code, CAST(date AS DATE) AS date, close FROM _idx_in"
    )
    conn.unregister("_idx_in")
    return len(frame)


def replace_selected_stocks(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> int:
    """整表全量替换逐年选股池。期望列 year/code/code_name。"""
    conn.execute("DELETE FROM selected_stocks")
    if df is None or df.empty:
        return 0
    frame = df.copy()
    for col in SELECTED_COLUMNS:
        if col not in frame.columns:
            frame[col] = pd.NA
    frame = frame[SELECTED_COLUMNS].dropna(subset=["year", "code"]).drop_duplicates(["year", "code"])
    conn.register("_sel_in", frame)
    conn.execute(
        "INSERT OR REPLACE INTO selected_stocks (year, code, code_name) "
        "SELECT year, code, code_name FROM _sel_in"
    )
    conn.unregister("_sel_in")
    return len(frame)


# 所有按 code 存储的表（退市清理时统一删除）；index_daily 是指数不参与
PURGE_TABLES = (
    "kline", "raw_kline", "adj_factor", "stock_meta", "stock_info",
    "ths_hot", "stock_fundamental", "selected_stocks",
    "stock_fundamental_quarterly", "stock_valuation_daily",
    "stock_dividend", "stock_forecast", "stock_express",
)


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


def get_meta(key: str, conn: duckdb.DuckDBPyConnection) -> str | None:
    """读 meta_kv 中的一个键（表不存在或无此键返回 None）。"""
    try:
        row = conn.execute("SELECT value FROM meta_kv WHERE key = ?", [key]).fetchone()
    except duckdb.Error:
        return None
    return row[0] if row else None


def set_meta(key: str, value: str, conn: duckdb.DuckDBPyConnection) -> None:
    """写 meta_kv 中的一个键。"""
    conn.execute("INSERT OR REPLACE INTO meta_kv (key, value) VALUES (?, ?)", [key, str(value)])


def atomic_swap(tmp_path: str, dest_path: str | None = None) -> None:
    """把临时库文件原子替换到正式路径（同盘 os.replace 原子）。

    替换前把旧库 rename（零拷贝）到 backups/ 目录留作恢复点，并做滚动轮转
    （最近 N 份每日 + 周备份）。备份失败不阻塞入库，仅打印警告后直接替换。
    """
    dest = dest_path or DUCKDB_PATH
    from src import backup

    try:
        backup.snapshot_before_swap(dest)
    except OSError as e:
        print(f"[db] 旧库备份失败（不影响入库）：{e}")
    os.replace(tmp_path, dest)
    try:
        backup.rotate_backups(dest)
    except OSError as e:
        print(f"[db] 备份轮转失败（不影响入库）：{e}")
