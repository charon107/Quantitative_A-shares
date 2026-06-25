import os
import sys
import json
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd
from tqdm import tqdm

# 与本文件同目录的兄弟模块：直接按文件路径 `python src/data_collection/stock_price.py`
# 运行时，sys.path[0] 就是这个目录，无需 `from src.data_collection import`（那种写法
# 要求项目根目录在 sys.path 里，但直接跑脚本文件不会自动满足这个条件）。
import tushare_client as tsc

# =========================
# 配置区
# =========================
START_DATE = "2025-01-01"              # 拉取起始日期
BASE_DIR = "股价数据_parquet_fq"      # 数据保存根目录

# 永久性错误（token失效/权限不足/积分不够）熔断后的冷却时长（小时）：
# 期间的 run 直接跳过，不再触发任何 tushare 请求。
FATAL_COOLDOWN_HOURS = float(os.environ.get("TUSHARE_FATAL_COOLDOWN_HOURS", "6"))

# =========================
# 路径区
# =========================
DATA_DIR = os.path.join(BASE_DIR, "kline_fq")        # 前复权日线 parquet（派生缓存，可重算）
RAW_DIR = os.path.join(BASE_DIR, "raw_kline")        # 未复权日线原始价（按日追加，永久保留）
FACTOR_DIR = os.path.join(BASE_DIR, "adj_factor_ts")  # 复权因子历史（按日追加，永久保留）
STATE_PATH = os.path.join(BASE_DIR, "state.json")
NAME_MAP_PATH = os.path.join(BASE_DIR, "code_name_map.parquet")  # 代码->公司名称映射（供看板显示）


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(FACTOR_DIR, exist_ok=True)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_run": None}


def save_state(state: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def read_parquet_if_exists(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        return pd.read_parquet(path)
    return pd.DataFrame()


def find_code_column(df: pd.DataFrame) -> str:
    """
    自动识别股票代码列名
    """
    if df is None or df.empty:
        raise RuntimeError("Stock list DataFrame is empty.")
    candidates = ["code", "ts_code", "证券代码", "股票代码", "symbol"]
    for c in candidates:
        if c in df.columns:
            return c
    raise RuntimeError(f"Cannot find code column. Columns: {df.columns.tolist()}")


def filter_mainboard(df: pd.DataFrame) -> pd.DataFrame:
    """
    只保留沪深主板：
    - 上交所主板：sh.60xxxx
    - 深交所主板：sz.00xxxx
    """
    code_col = find_code_column(df)
    if code_col != "code":
        df = df.rename(columns={code_col: "code"})

    # 严格匹配主板代码段（排除科创688、创业300、北交所bj等）
    df = df[df["code"].str.match(r"^(sh\.60\d{4}|sz\.00\d{4})$", na=False)]
    df = df[df["code"].notna() & (df["code"] != "")]
    return df


def get_stock_list() -> pd.DataFrame:
    """获取沪深主板股票列表（约3000+）。"""
    df = tsc.fetch_stock_basic()
    return filter_mainboard(df)


def save_name_map(stock_df: pd.DataFrame):
    """
    保存代码->公司名称映射到 parquet，供可视化看板显示公司名称。

    数据随 股价数据_parquet_fq/ 目录一起同步到 Hugging Face，
    看板只读本地文件，无需自己联网调 tushare。

    fetch_stock_basic 返回 code_name 列；若没有名称列则跳过（看板回退到只显示代码）。
    """
    name_col = None
    for candidate in ["code_name", "证券简称", "name"]:
        if candidate in stock_df.columns:
            name_col = candidate
            break

    if name_col is None:
        print("[save_name_map] 警告：股票列表无名称列，跳过名称映射保存")
        return

    name_map = (
        stock_df[["code", name_col]]
        .rename(columns={name_col: "code_name"})
        .dropna()
        .drop_duplicates("code")
    )
    name_map.to_parquet(NAME_MAP_PATH, index=False)
    print(f"[save_name_map] 已保存 {len(name_map)} 条代码->名称映射: {NAME_MAP_PATH}")


def build_name_map_only():
    """
    仅生成代码->名称映射文件（不拉取K线），用于快速初始化看板名称显示。

    用法：python src/data_collection/stock_price.py name-map
    """
    ensure_dirs()
    stock_df = get_stock_list()
    save_name_map(stock_df)


def next_day(yyyy_mm_dd: str) -> str:
    d = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d")
    return (d + timedelta(days=1)).strftime("%Y-%m-%d")


def _trip_fatal_breaker(state: dict, error_msg: str):
    """检测到 tushare 永久性错误：记录冷却期截止时间，供后续 run 快速跳过。"""
    cooldown_until = datetime.now() + timedelta(hours=FATAL_COOLDOWN_HOURS)
    state["fatal_blocked_until"] = cooldown_until.isoformat()
    save_state(state)
    print(f"[Fatal] tushare 返回永久性错误，立即停止本次运行：{error_msg}")
    print(f"[Fatal] 冷却至 {cooldown_until.isoformat()} 前，期间的 run 将自动跳过。")


def determine_needed_dates(state: dict) -> list[str]:
    """根据 state.json 里的 last_complete_date 决定还需要回补哪些交易日。"""
    last_complete = state.get("last_complete_date")
    start = next_day(last_complete) if last_complete else START_DATE
    today = datetime.today().strftime("%Y-%m-%d")
    if start > today:
        return []
    dates = tsc.fetch_trade_dates(start, today)
    return [d.strftime("%Y-%m-%d") for d in dates]


def fetch_market_snapshot(trade_date: str) -> pd.DataFrame:
    """
    一次拉某个交易日全市场的原始价 + 换手率（合并成一张表），筛成沪深主板。
    复权因子单独返回（落地到另一个缓存目录，结构不同）。
    """
    raw = tsc.fetch_daily_by_date(trade_date)
    turn = tsc.fetch_turnover_by_date(trade_date)
    factor = tsc.fetch_adj_factor_by_date(trade_date)

    raw = filter_mainboard(raw) if not raw.empty else raw
    turn = filter_mainboard(turn) if not turn.empty else turn
    factor = filter_mainboard(factor) if not factor.empty else factor

    if not raw.empty:
        raw = raw.merge(turn[["code", "turn"]], on="code", how="left") if not turn.empty else raw.assign(turn=pd.NA)

    return raw, factor


def _merge_and_save(dir_path: str, code: str, new_chunks: list[pd.DataFrame] | None) -> pd.DataFrame:
    """把新拉到的数据追加进本地永久缓存（按日期去重排序），写回并返回合并后的完整数据。"""
    path = os.path.join(dir_path, f"{code}.parquet")
    existing = read_parquet_if_exists(path)
    if not new_chunks:
        return existing

    new_df = pd.concat(new_chunks, ignore_index=True)
    date_col = "date" if "date" in new_df.columns else "trade_date"
    merged = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
    merged[date_col] = pd.to_datetime(merged[date_col], errors="coerce")
    merged = merged.dropna(subset=[date_col])
    merged = merged.sort_values([date_col]).drop_duplicates(subset=[date_col], keep="last").reset_index(drop=True)
    merged.to_parquet(path, index=False)
    return merged


def _backfill_new_listing(code: str, raw_rows_by_code: dict, factor_rows_by_code: dict):
    """全新股票（本地从未出现过，且本次按日批量也没扫到）：单独按股票全量回补一次。

    这种情况很少见（新股上市/新进沪深主板），所以用单股票请求不会有量级问题。
    """
    raw_hist = tsc.fetch_daily_raw(code, START_DATE, "")
    factor_hist = tsc.fetch_adj_factor_series(code, START_DATE, "")
    if not raw_hist.empty:
        turn_hist = tsc.fetch_turnover(code, START_DATE, "")
        if not turn_hist.empty:
            raw_hist = raw_hist.merge(turn_hist, on="date", how="left")
        else:
            raw_hist["turn"] = pd.NA
        raw_rows_by_code[code].append(raw_hist)
    if not factor_hist.empty:
        factor_rows_by_code[code].append(factor_hist)


def main():
    ensure_dirs()
    state = load_state()

    # -------- 永久性错误冷却期检查：命中过熔断时，本次直接跳过，不再触发任何 tushare 请求 --------
    cooldown_until = state.get("fatal_blocked_until")
    if cooldown_until and datetime.now() < datetime.fromisoformat(cooldown_until):
        print(f"[Skip] tushare 永久性错误冷却期内（至 {cooldown_until}），本次不再尝试。")
        return

    try:
        stock_df = get_stock_list()
        needed_dates = determine_needed_dates(state)
    except tsc.TushareFatalError as e:
        _trip_fatal_breaker(state, str(e))
        sys.exit(1)

    codes = stock_df["code"].tolist()
    # 顺便刷新代码->名称映射，供看板显示公司名称（随 HF 同步）
    save_name_map(stock_df)

    if not needed_dates:
        print("[Skip] 数据已是最新，无需更新。")
        state["last_run"] = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
        return

    # 仅供本地冒烟测试：STOCK_DATE_LIMIT=N 只回补最近 N 个交易日
    limit_days = int(os.environ.get("STOCK_DATE_LIMIT", "0"))
    if limit_days > 0:
        needed_dates = needed_dates[-limit_days:]

    print(f"需要回补 {len(needed_dates)} 个交易日：{needed_dates[0]} ~ {needed_dates[-1]}")

    raw_rows_by_code: dict[str, list] = defaultdict(list)
    factor_rows_by_code: dict[str, list] = defaultdict(list)
    errors = []
    actual_last_date = None  # 真正拿到非空数据的最晚交易日（可能比 needed_dates[-1] 早，
                              # 比如"今天"收盘后数据还没发布时，不能把它当成已完成）

    # -------- 按交易日批量拉取全市场数据（一天一次请求，不是一只股票一次）--------
    try:
        for d in tqdm(needed_dates, desc="Fetching market snapshots"):
            raw, factor = fetch_market_snapshot(d)
            # kline_fq 是从 raw 派生的，只要 raw 还没发布就不能推进
            # last_complete_date——哪怕 factor 当天碰巧有数据也不行，否则那天的
            # 真实股价会被永久跳过，再也补不回来。
            if raw.empty:
                print(f"[Warn] {d} 暂无日线数据（可能还没发布），跳过这一天。")
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

    # -------- 全新股票兜底：本地从未出现过且这次也没扫到，单独按股票全量回补 --------
    existing_raw_codes = {fn[:-len(".parquet")] for fn in os.listdir(RAW_DIR) if fn.endswith(".parquet")}
    new_codes = [c for c in codes if c not in existing_raw_codes and c not in raw_rows_by_code]
    for code in tqdm(new_codes, desc="Backfilling brand-new listings"):
        try:
            _backfill_new_listing(code, raw_rows_by_code, factor_rows_by_code)
        except tsc.TushareFatalError as e:
            _trip_fatal_breaker(state, str(e))
            sys.exit(1)
        except Exception as e:
            errors.append((code, str(e)))

    # -------- 落盘 + 本地重算前复权（纯本地计算，不再发任何请求）--------
    all_codes = sorted(set(raw_rows_by_code) | set(factor_rows_by_code))
    stats = {"UPDATED": 0, "EMPTY": 0, "ERROR": 0}
    for code in tqdm(all_codes, desc="Writing parquet + recompute qfq"):
        try:
            raw_full = _merge_and_save(RAW_DIR, code, raw_rows_by_code.get(code))
            factor_full = _merge_and_save(FACTOR_DIR, code, factor_rows_by_code.get(code))
            if raw_full.empty or factor_full.empty:
                stats["EMPTY"] += 1
                continue
            qfq = tsc.compute_qfq(raw_full, factor_full, code)
            if qfq.empty:
                stats["EMPTY"] += 1
                continue
            qfq.to_parquet(os.path.join(DATA_DIR, f"{code}.parquet"), index=False)
            stats["UPDATED"] += 1
        except Exception as e:
            stats["ERROR"] += 1
            errors.append((code, str(e)))

    # -------- 持久化 --------
    # 只有真正拿到过非空数据才推进 last_complete_date；如果整段区间都没数据
    # （比如"今天"收盘后数据还没发布），保持原值不变，下次运行会再补这一天。
    if actual_last_date:
        state["last_complete_date"] = actual_last_date
    state["last_run"] = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)

    print("\n=== DONE ===")
    print("Saved to:", os.path.abspath(BASE_DIR))
    print(f"Dates requested: {len(needed_dates)} ({needed_dates[0]} ~ {needed_dates[-1]})")
    print(f"Last complete date: {actual_last_date or '(无变化)'}")
    print(f"New listings backfilled: {len(new_codes)}")
    print(f"Stocks touched: {len(all_codes)}")
    print("Stats:", stats)
    if errors:
        print("\n--- Errors (showing up to 30) ---")
        for code, msg in errors[:30]:
            print(code, "=>", msg)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "name-map":
        # 轻量模式：只生成代码->名称映射，不拉取K线
        build_name_map_only()
    else:
        main()
