"""A股日线前复权入库（tushare → DuckDB）。

按交易日批量拉取全市场原始价 + 复权因子，落入 DuckDB 的 raw_kline / adj_factor，
再按 code 重算前复权（qfq）写入 kline 表，并刷新 stock_meta。

采用「拷贝现有库 → 写临时库 → os.replace 原子替换」，避免与 API 的只读连接争锁。

运行：
    uv run python -m src.data_collection.stock_price          # 增量入库
    uv run python -m src.data_collection.stock_price name-map # 仅刷新代码->名称
"""
import os
import sys
import json
from collections import defaultdict
from datetime import datetime, timedelta

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

# =========================
# 配置
# =========================
START_DATE = "2025-01-01"  # 拉取起始日期
STATE_PATH = os.environ.get("INGEST_STATE_PATH", "ingest_state.json")

# 永久性错误（token失效/权限不足/积分不够）熔断后的冷却时长（小时）
FATAL_COOLDOWN_HOURS = float(os.environ.get("TUSHARE_FATAL_COOLDOWN_HOURS", "6"))


# =========================
# 状态
# =========================
def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_run": None}


def save_state(state: dict):
    parent = os.path.dirname(os.path.abspath(STATE_PATH))
    os.makedirs(parent, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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
# 交易日
# =========================
def next_day(yyyy_mm_dd: str) -> str:
    d = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d")
    return (d + timedelta(days=1)).strftime("%Y-%m-%d")


def determine_needed_dates(state: dict) -> list[str]:
    last_complete = state.get("last_complete_date")
    start = next_day(last_complete) if last_complete else START_DATE
    today = datetime.today().strftime("%Y-%m-%d")
    if start > today:
        return []
    dates = tsc.fetch_trade_dates(start, today)
    return [d.strftime("%Y-%m-%d") for d in dates]


def _trip_fatal_breaker(state: dict, error_msg: str):
    cooldown_until = datetime.now() + timedelta(hours=FATAL_COOLDOWN_HOURS)
    state["fatal_blocked_until"] = cooldown_until.isoformat()
    save_state(state)
    print(f"[Fatal] tushare 永久性错误，立即停止：{error_msg}")
    print(f"[Fatal] 冷却至 {cooldown_until.isoformat()} 前自动跳过。")


# =========================
# 抓取
# =========================
def fetch_market_snapshot(trade_date: str):
    """拉某交易日全市场原始价 + 换手率（合并）与复权因子（单独）。"""
    raw = tsc.fetch_daily_by_date(trade_date)
    turn = tsc.fetch_turnover_by_date(trade_date)
    factor = tsc.fetch_adj_factor_by_date(trade_date)

    raw = filter_mainboard(raw) if not raw.empty else raw
    turn = filter_mainboard(turn) if not turn.empty else turn
    factor = filter_mainboard(factor) if not factor.empty else factor

    if not raw.empty:
        raw = raw.merge(turn[["code", "turn"]], on="code", how="left") if not turn.empty else raw.assign(turn=pd.NA)
    return raw, factor


def _backfill_new_listing(code: str, raw_rows_by_code: dict, factor_rows_by_code: dict):
    """全新股票：单股全量回补一次。"""
    raw_hist = tsc.fetch_daily_raw(code, START_DATE, "")
    factor_hist = tsc.fetch_adj_factor_series(code, START_DATE, "")
    if not raw_hist.empty:
        turn_hist = tsc.fetch_turnover(code, START_DATE, "")
        if not turn_hist.empty:
            raw_hist = raw_hist.merge(turn_hist, on="date", how="left")
        else:
            raw_hist["turn"] = pd.NA
        raw_hist["code"] = code
        raw_rows_by_code[code].append(raw_hist)
    if not factor_hist.empty:
        factor_rows_by_code[code].append(factor_hist)


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


def persist(stock_df, raw_rows_by_code, factor_rows_by_code) -> dict:
    """把新抓取的数据写入 DuckDB，按 code 重算 qfq，刷新 stock_meta。返回统计。"""
    conn, tmp, dest = _open_write_copy()
    stats = {"UPDATED": 0, "EMPTY": 0, "ERROR": 0}
    errors: list[tuple[str, str]] = []
    try:
        # 1) 原始价 + 复权因子入库
        for code, chunks in raw_rows_by_code.items():
            if chunks:
                db.upsert_raw(pd.concat(chunks, ignore_index=True), conn)
        for code, chunks in factor_rows_by_code.items():
            if chunks:
                db.upsert_adj(pd.concat(chunks, ignore_index=True), conn)

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
    finally:
        conn.close()

    db.atomic_swap(tmp, dest)
    stats["errors"] = errors
    return stats


def build_name_map_only():
    """仅刷新 stock_meta（不拉 K线）。"""
    stock_df = get_stock_list()
    conn, tmp, dest = _open_write_copy()
    try:
        n = db.upsert_meta(name_map_frame(stock_df), conn)
    finally:
        conn.close()
    db.atomic_swap(tmp, dest)
    print(f"[name-map] 已刷新 {n} 条代码->名称到 {dest}")


# =========================
# 主流程
# =========================
def main():
    state = load_state()

    cooldown_until = state.get("fatal_blocked_until")
    if cooldown_until and datetime.now() < datetime.fromisoformat(cooldown_until):
        print(f"[Skip] tushare 冷却期内（至 {cooldown_until}），本次跳过。")
        return

    try:
        stock_df = get_stock_list()
        needed_dates = determine_needed_dates(state)
    except tsc.TushareFatalError as e:
        _trip_fatal_breaker(state, str(e))
        sys.exit(1)

    codes = stock_df["code"].tolist()

    if not needed_dates:
        print("[Skip] 数据已是最新。仅刷新名称映射。")
        build_name_map_only()
        state["last_run"] = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
        return

    limit_days = int(os.environ.get("STOCK_DATE_LIMIT", "0"))
    if limit_days > 0:
        needed_dates = needed_dates[-limit_days:]

    print(f"需要回补 {len(needed_dates)} 个交易日：{needed_dates[0]} ~ {needed_dates[-1]}")

    raw_rows_by_code: dict[str, list] = defaultdict(list)
    factor_rows_by_code: dict[str, list] = defaultdict(list)
    actual_last_date = None

    # 按交易日批量拉全市场
    try:
        for d in tqdm(needed_dates, desc="Fetching market snapshots"):
            raw, factor = fetch_market_snapshot(d)
            if raw.empty:
                print(f"[Warn] {d} 暂无日线（可能未发布），跳过。")
                continue
            for code, group in raw.groupby("code"):
                raw_rows_by_code[code].append(group)
            if not factor.empty:
                for code, group in factor.groupby("code"):
                    factor_rows_by_code[code].append(group)
            actual_last_date = d
    except tsc.TushareFatalError as e:
        _trip_fatal_breaker(state, str(e))
        sys.exit(1)

    # 全新股票兜底
    have = existing_raw_codes()
    new_codes = [c for c in codes if c not in have and c not in raw_rows_by_code]
    for code in tqdm(new_codes, desc="Backfilling brand-new listings"):
        try:
            _backfill_new_listing(code, raw_rows_by_code, factor_rows_by_code)
        except tsc.TushareFatalError as e:
            _trip_fatal_breaker(state, str(e))
            sys.exit(1)
        except Exception:
            pass

    stats = persist(stock_df, raw_rows_by_code, factor_rows_by_code)

    if actual_last_date:
        state["last_complete_date"] = actual_last_date
    state["last_run"] = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)

    print("\n=== DONE ===")
    print("DuckDB:", os.path.abspath(db.DUCKDB_PATH))
    print(f"Dates: {len(needed_dates)} ({needed_dates[0]} ~ {needed_dates[-1]})")
    print(f"Last complete: {actual_last_date or '(无变化)'}")
    print(f"New listings: {len(new_codes)} | Touched: {len(set(raw_rows_by_code) | set(factor_rows_by_code))}")
    print("Stats:", {k: v for k, v in stats.items() if k != "errors"})
    if stats.get("errors"):
        print("\n--- Errors (up to 30) ---")
        for code, msg in stats["errors"][:30]:
            print(code, "=>", msg)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "name-map":
        build_name_map_only()
    else:
        main()
