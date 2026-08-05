"""A股日线前复权入库的共享函数库（tushare → DuckDB）。

按交易日批量拉取全市场原始价 + 复权因子，落入 DuckDB 的 raw_kline / adj_factor，
再按 code 重算前复权（qfq）写入 kline 表，并刷新 stock_meta。

本模块不是入口：日常增量入库走「runner 抓 parquet（scripts/fetch_all_parquet.py）
→ 服务器加载（scripts/load_all_parquet.py）」；全量重建走 scripts/reingest_all.py。
这里的函数被上述两个脚本与测试复用。
"""
import os
import sys

import duckdb
import pandas as pd
from tqdm import tqdm

# 兼容两种运行方式：python -m src.data_collection.stock_price / 直接跑脚本文件
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    from src.data_collection import tushare_client as tsc
except ImportError:  # 直接以脚本路径运行时，兄弟模块在 sys.path[0]
    import tushare_client as tsc
from src import db
from src.config import START_DATE  # noqa: F401  # re-export：scripts/reingest_all.py 从此处导入

# 入库进度状态文件路径（由 scripts/load_all_parquet.py 写入/更新）
STATE_PATH = os.environ.get("INGEST_STATE_PATH", "ingest_state.json")


# =========================
# 股票列表
# =========================
def find_code_column(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        raise RuntimeError("Stock list DataFrame is empty.")
    for c in ["code", "ts_code", "证券代码", "股票代码", "symbol"]:
        if c in df.columns:
            return c
    raise RuntimeError(f"Cannot find code column. Columns: {df.columns.tolist()}")


def filter_mainboard(df: pd.DataFrame) -> pd.DataFrame:
    """只保留沪深主板：sh.60xxxx / sz.00xxxx。"""
    code_col = find_code_column(df)
    if code_col != "code":
        df = df.rename(columns={code_col: "code"})
    df = df[df["code"].str.match(r"^(sh\.60\d{4}|sz\.00\d{4})$", na=False)]
    df = df[df["code"].notna() & (df["code"] != "")]
    return df


def get_stock_list() -> pd.DataFrame:
    return filter_mainboard(tsc.fetch_stock_basic())


def name_map_frame(stock_df: pd.DataFrame) -> pd.DataFrame:
    """从股票列表取 code/code_name 两列（无名称列则返回空）。"""
    name_col = None
    for c in ["code_name", "证券简称", "name"]:
        if c in stock_df.columns:
            name_col = c
            break
    if name_col is None:
        return pd.DataFrame(columns=["code", "code_name"])
    return (
        stock_df[["code", name_col]]
        .rename(columns={name_col: "code_name"})
        .dropna()
        .drop_duplicates("code")
    )


# =========================
# 抓取
# =========================
def fetch_market_snapshot(trade_date: str):
    """拉某交易日全市场原始价 + 每日指标（换手率并入日线、估值单独成帧）与复权因子。"""
    raw = tsc.fetch_daily_by_date(trade_date)
    basic = tsc.fetch_daily_basic_by_date(trade_date)  # 换手率 + 估值，同一响应双用
    factor = tsc.fetch_adj_factor_by_date(trade_date)

    raw = filter_mainboard(raw) if not raw.empty else raw
    basic = filter_mainboard(basic) if not basic.empty else basic
    factor = filter_mainboard(factor) if not factor.empty else factor

    if not raw.empty:
        raw = raw.merge(basic[["code", "turn"]], on="code", how="left") if not basic.empty else raw.assign(turn=pd.NA)
    valuation = (
        basic[["code", "date", *tsc.VALUATION_METRIC_COLUMNS]] if not basic.empty else pd.DataFrame()
    )
    return raw, factor, valuation


# =========================
# 持久化（DuckDB，原子替换）
# =========================
def _open_write_copy() -> tuple[duckdb.DuckDBPyConnection, str, str]:
    """拷贝现有库为临时库并打开读写连接（不存在则新建）。返回 (conn, tmp, dest)。"""
    import shutil
    dest = db.DUCKDB_PATH
    tmp = dest + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    if os.path.exists(dest):
        shutil.copy2(dest, tmp)
    conn = duckdb.connect(tmp, read_only=False)
    db.init_schema(conn)
    return conn, tmp, dest


def existing_raw_codes() -> set[str]:
    if not db.database_exists():
        return set()
    try:
        with db.connect(read_only=True) as conn:
            return db.existing_raw_codes(conn)
    except duckdb.Error:
        return set()


def persist(stock_df, raw_rows_by_code, factor_rows_by_code, delisted=None, valuation_rows_by_code=None) -> dict:
    """把新抓取的数据写入 DuckDB，按 code 重算 qfq，刷新 stock_meta，清理退市股。返回统计。"""
    conn, tmp, dest = _open_write_copy()
    stats = {"UPDATED": 0, "EMPTY": 0, "ERROR": 0, "PURGED": 0}
    errors: list[tuple[str, str]] = []
    try:
        # 1) 原始价 + 复权因子 + 估值日频入库
        for code, chunks in raw_rows_by_code.items():
            if chunks:
                db.upsert_raw(pd.concat(chunks, ignore_index=True), conn)
        for code, chunks in factor_rows_by_code.items():
            if chunks:
                db.upsert_adj(pd.concat(chunks, ignore_index=True), conn)
        for code, chunks in (valuation_rows_by_code or {}).items():
            if chunks:
                db.upsert_valuation_daily(pd.concat(chunks, ignore_index=True), conn)

        # 2) 受影响的 code 重算前复权写 kline
        touched = sorted(set(raw_rows_by_code) | set(factor_rows_by_code))
        for code in tqdm(touched, desc="Recompute qfq -> kline"):
            try:
                raw_full = db.read_raw(code, conn)
                factor_full = db.read_adj(code, conn)
                if raw_full.empty or factor_full.empty:
                    stats["EMPTY"] += 1
                    continue
                qfq = tsc.compute_qfq(raw_full, factor_full, code)
                if qfq.empty:
                    stats["EMPTY"] += 1
                    continue
                db.upsert_kline(qfq, conn)
                stats["UPDATED"] += 1
            except Exception as e:
                stats["ERROR"] += 1
                errors.append((code, str(e)))

        # 3) 刷新代码->名称
        db.upsert_meta(name_map_frame(stock_df), conn)

        # 4) 清理退市股：list_status='D' + 名字带「退」（退市整理期），从所有表删除
        stats["PURGED"] = db.purge_delisted(conn, delisted)
    finally:
        conn.close()

    db.atomic_swap(tmp, dest)
    stats["errors"] = errors
    return stats
