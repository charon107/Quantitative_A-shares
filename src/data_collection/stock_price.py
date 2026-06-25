import os
import sys
import json
import signal
import random
import hashlib
from datetime import datetime, timedelta
from multiprocessing import Lock, Value
from multiprocessing.pool import Pool

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

# 检查复权因子是否变化：每次运行回看最近 N 天（越大越保险，越慢一点）
FACTOR_LOOKBACK_DAYS = 30

# 并行抓取的进程数（tushare 是纯 HTTP 调用，真正的限速由 tushare_client 里
# 跨进程共享的全局节流器保证，不会因为加 worker 而超过账号每分钟调用上限；
# worker 数主要影响"等节流间隔的同时还能干多少解析/写盘活"）。
WORKERS = int(os.environ.get("TUSHARE_WORKERS", "8"))

# chunksize：主进程与 worker 间的 IPC 粒度（增大减少 pickle 轮次）
CHUNKSIZE = int(os.environ.get("TUSHARE_CHUNKSIZE", "16"))

# 因子检查周期：距上次检查超过此天数才重新查因子
FACTOR_CHECK_INTERVAL_DAYS = int(os.environ.get("FACTOR_CHECK_INTERVAL_DAYS", "7"))
FACTOR_CHECK_DATES_PATH = os.path.join(BASE_DIR, "factor_check_dates.parquet")

# 永久性错误（token失效/权限不足/积分不够）熔断后的冷却时长（小时）：
# 期间的 run 直接跳过，不再触发任何 tushare 请求。
FATAL_COOLDOWN_HOURS = float(os.environ.get("TUSHARE_FATAL_COOLDOWN_HOURS", "6"))

# =========================
# 路径区
# =========================
DATA_DIR = os.path.join(BASE_DIR, "kline_fq")           # 前复权日线 parquet
# 复权因子换成新目录：tushare 的因子 schema（code/trade_date/adj_factor）和
# baostock 的（code/dividOperateDate/foreAdjustFactor/...）不兼容，复用旧目录
# 会读出脏数据；用新目录会让每只股票在迁移后第一次跑触发一次 FULL_REBUILD，
# 这是迁移的预期一次性成本。
FACTOR_DIR = os.path.join(BASE_DIR, "adj_factor_ts")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
NAME_MAP_PATH = os.path.join(BASE_DIR, "code_name_map.parquet")  # 代码->公司名称映射（供看板显示）


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FACTOR_DIR, exist_ok=True)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_run": None}


def save_state(state: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_factor_check_dates() -> dict[str, str]:
    """加载每只股票上次检查复权因子的日期。返回 {code: "YYYY-MM-DD"}。"""
    if os.path.exists(FACTOR_CHECK_DATES_PATH):
        try:
            df = pd.read_parquet(FACTOR_CHECK_DATES_PATH)
            if "code" in df.columns and "last_checked" in df.columns:
                return dict(zip(df["code"].astype(str), df["last_checked"].astype(str)))
        except Exception:
            pass
    return {}


def save_factor_check_dates(dates: dict[str, str]):
    """保存每只股票上次检查复权因子的日期到 parquet。"""
    if not dates:
        return
    df = pd.DataFrame([
        {"code": k, "last_checked": v} for k, v in dates.items()
    ])
    df.to_parquet(FACTOR_CHECK_DATES_PATH, index=False)


def should_check_factor(code: str, factor_check_dates: dict[str, str]) -> bool:
    """判断某只股票的复权因子是否需要重新检查。"""
    last_checked = factor_check_dates.get(code)
    if last_checked is None:
        return True
    try:
        last_date = datetime.strptime(last_checked, "%Y-%m-%d")
        return (datetime.today() - last_date).days >= FACTOR_CHECK_INTERVAL_DAYS
    except ValueError:
        return True


def read_parquet_if_exists(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        return pd.read_parquet(path)
    return pd.DataFrame()


def stable_hash_df(df: pd.DataFrame, cols: list[str]) -> str:
    """
    对DataFrame做稳定hash，用于判断复权因子是否变化
    """
    if df is None or df.empty:
        return "EMPTY"
    use = df.copy()
    keep = [c for c in cols if c in use.columns]
    if not keep:
        return "NO_COLS"
    use = use[keep].copy()
    sort_cols = [c for c in ["code", "trade_date"] if c in use.columns]
    if sort_cols:
        use = use.sort_values(sort_cols)
    payload = use.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def fetch_kline_fq(code: str, start_date: str, end_date: str = "") -> pd.DataFrame:
    """拉取前复权日线（tushare daily + adj_factor 拼算，内置重试 + 请求节流）。"""
    return tsc.fetch_kline_qfq(code, start_date=start_date, end_date=end_date)


def fetch_adjust_factor(code: str, start_date: str, end_date: str = "") -> pd.DataFrame:
    """拉取复权因子，用来判断是否需要重算前复权历史（tushare adj_factor，内置重试）。"""
    return tsc.fetch_adj_factor_series(code, start_date=start_date, end_date=end_date)


def get_last_date(existing: pd.DataFrame) -> str | None:
    if existing is None or existing.empty or "date" not in existing.columns:
        return None
    dmax = pd.to_datetime(existing["date"], errors="coerce").max()
    if pd.isna(dmax):
        return None
    return dmax.strftime("%Y-%m-%d")


def get_first_date(existing: pd.DataFrame) -> str | None:
    if existing is None or existing.empty or "date" not in existing.columns:
        return None
    dmin = pd.to_datetime(existing["date"], errors="coerce").min()
    if pd.isna(dmin):
        return None
    return dmin.strftime("%Y-%m-%d")


def next_day(yyyy_mm_dd: str) -> str:
    d = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d")
    return (d + timedelta(days=1)).strftime("%Y-%m-%d")


def prev_day(yyyy_mm_dd: str) -> str:
    d = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d")
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")


def update_one_stock(code: str, factor_check_start: str,
                     factor_check_dates: dict[str, str] | None = None) -> dict:
    """
    更新单只股票（优化：K线优先、因子条件检查）。

    流程：
      1. 若有本地数据 → 先做增量 K 线查询（便宜）
      2. 无新 K 线 + 因子近期检查过 → 跳过因子 API，直接 NOOP
      3. 有新 K 线 / 因子过期 / 无本地数据 → 检查复权因子
      4. 因子变化 → 全量重拉；否则增量/回补
    """
    k_path = os.path.join(DATA_DIR, f"{code}.parquet")
    f_path = os.path.join(FACTOR_DIR, f"{code}.parquet")
    factor_dates = factor_check_dates or {}

    existing = read_parquet_if_exists(k_path)
    first_date = get_first_date(existing)
    last_date = get_last_date(existing)
    factor_checked_this_run = False

    # ======== Phase 1: 有本地数据 → 先做增量 K 线（跳过不必要因子检查）========
    df_inc = pd.DataFrame()
    need_factor_check = True  # 保守默认：需要查因子

    if last_date is not None:
        inc_start = next_day(last_date)
        df_inc = fetch_kline_fq(code, start_date=inc_start, end_date="")

        # 若没有新 K 线数据，且因子近期检查过，可跳过因子 API
        if df_inc.empty and not should_check_factor(code, factor_dates):
            need_factor_check = False

    # ======== Phase 2: 因子检查（条件触发）========
    factor_changed = False
    factor_old = read_parquet_if_exists(f_path)
    old_hash = stable_hash_df(
        factor_old,
        cols=["code", "trade_date", "adj_factor"]
    ) if need_factor_check else "SKIPPED"

    if need_factor_check:
        factor_checked_this_run = True
        factor_new = fetch_adjust_factor(code, start_date=factor_check_start, end_date="")
        if not factor_new.empty:
            factor_all = pd.concat([factor_old, factor_new], ignore_index=True) if not factor_old.empty else factor_new
            factor_all = factor_all.drop_duplicates()
            if "trade_date" in factor_all.columns:
                factor_all = factor_all.sort_values(["trade_date"])
        else:
            factor_all = factor_old

        new_hash = stable_hash_df(
            factor_all,
            cols=["code", "trade_date", "adj_factor"]
        )
        factor_changed = (new_hash != old_hash)

        if factor_all is not None and not factor_all.empty:
            factor_all.to_parquet(f_path, index=False)

    # ======== Phase 3: K 线构建/更新 ========

    # 若因子变化 → 全量重拉
    if factor_changed:
        df_full = fetch_kline_fq(code, start_date=START_DATE, end_date="")
        if df_full.empty:
            return {"code": code, "mode": "FULL_REBUILD_EMPTY", "rows": 0,
                    "last_date": last_date, "factor_checked": factor_checked_this_run}
        df_full.to_parquet(k_path, index=False)
        return {
            "code": code,
            "mode": "FULL_REBUILD",
            "rows": len(df_full),
            "first_date": df_full["date"].min().strftime("%Y-%m-%d"),
            "last_date": df_full["date"].max().strftime("%Y-%m-%d"),
            "factor_checked": factor_checked_this_run,
        }

    # 无本地数据 → 初始化
    if last_date is None:
        df_init = fetch_kline_fq(code, start_date=START_DATE, end_date="")
        if df_init.empty:
            return {"code": code, "mode": "INIT_EMPTY", "rows": 0,
                    "first_date": None, "last_date": None, "factor_checked": factor_checked_this_run}
        df_init.to_parquet(k_path, index=False)
        return {
            "code": code,
            "mode": "INIT",
            "rows": len(df_init),
            "first_date": df_init["date"].min().strftime("%Y-%m-%d"),
            "last_date": df_init["date"].max().strftime("%Y-%m-%d"),
            "factor_checked": factor_checked_this_run,
        }

    # 向前回补缺口
    did_backfill = False
    if first_date is not None and START_DATE < first_date:
        backfill_start = START_DATE
        backfill_end = prev_day(first_date)
        if backfill_end >= backfill_start:
            df_back = fetch_kline_fq(code, start_date=backfill_start, end_date=backfill_end)
            if not df_back.empty:
                merged = pd.concat([df_back, existing], ignore_index=True)
                merged["date"] = pd.to_datetime(merged["date"], errors="coerce")
                merged = merged.dropna(subset=["date"])
                merged = merged.sort_values(["date"]).drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
                merged.to_parquet(k_path, index=False)

                existing = merged
                first_date = get_first_date(existing)
                last_date = get_last_date(existing)
                did_backfill = True

    # 若 df_inc 为空（Phase 1 已查过）且未回补 → NOOP
    if df_inc.empty:
        mode = "INCREMENTAL_NOOP"
        if did_backfill:
            mode = "BACKFILL+NOOP"
        return {"code": code, "mode": mode, "rows": 0,
                "first_date": first_date, "last_date": last_date, "factor_checked": factor_checked_this_run}

    # 合并增量
    merged = pd.concat([existing, df_inc], ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce")
    merged = merged.dropna(subset=["date"])
    merged = merged.sort_values(["date"]).drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    merged.to_parquet(k_path, index=False)

    mode = "INCREMENTAL"
    if did_backfill:
        mode = "BACKFILL+INCREMENTAL"

    return {
        "code": code,
        "mode": mode,
        "rows": len(df_inc),
        "first_date": merged["date"].min().strftime("%Y-%m-%d"),
        "last_date": merged["date"].max().strftime("%Y-%m-%d"),
        "factor_checked": factor_checked_this_run,
    }


_active_pool: "Pool | None" = None


def _sigterm_handler(signum, frame):
    """收到 SIGTERM（GitHub Actions 手动取消）时立即终止所有 worker，避免孤儿进程。"""
    if _active_pool is not None:
        _active_pool.terminate()
    sys.exit(128 + signum)


def _pool_init(rate_lock, rate_next_allowed):
    """每个 worker 进程启动时，把跨进程共享的限流锁/状态注入 tushare_client，
    让所有 worker 共用同一个全局调用速率。"""
    tsc.configure_rate_limiter(rate_lock, rate_next_allowed)


def _update_task(args):
    """进程池任务：更新单只股票，异常转成 ERROR 结果而非抛出（避免拖垮整个池）。

    永久性错误（token失效/权限不足/积分不够）单独标记为 FATAL，供主进程识别
    并立即熔断整个 run。
    """
    code, factor_check_start, factor_check_dates = args
    try:
        return update_one_stock(code, factor_check_start=factor_check_start,
                                factor_check_dates=factor_check_dates)
    except tsc.TushareFatalError as e:
        return {"code": code, "mode": "FATAL", "error": str(e)}
    except Exception as e:
        return {"code": code, "mode": "ERROR", "error": str(e)}


def _trip_fatal_breaker(state: dict, error_msg: str):
    """检测到 tushare 永久性错误：记录冷却期截止时间，供后续 run 快速跳过。"""
    cooldown_until = datetime.now() + timedelta(hours=FATAL_COOLDOWN_HOURS)
    state["fatal_blocked_until"] = cooldown_until.isoformat()
    save_state(state)
    print(f"[Fatal] tushare 返回永久性错误，立即停止本次运行：{error_msg}")
    print(f"[Fatal] 冷却至 {cooldown_until.isoformat()} 前，期间的 run 将自动跳过。")


def _get_latest_local_date(codes: list[str], sample_size: int = 50) -> str | None:
    """随机抽样检查本地 kline 文件的最晚日期，取最小值。

    上次运行若被中途取消，只有按 code 顺序排在前面的一批股票会被更新，
    若固定抽前缀 + 取 max，会被这批"恰好更新过"的股票误判为全量已最新。
    随机抽样 + 取 min 可以避免命中这种不均匀更新的局部样本。
    """
    sample = random.sample(codes, min(sample_size, len(codes)))
    all_dates = []
    for code in sample:
        k_path = os.path.join(DATA_DIR, f"{code}.parquet")
        existing = read_parquet_if_exists(k_path)
        d = get_last_date(existing)
        if d:
            all_dates.append(d)
    if not all_dates:
        return None
    return min(all_dates)


def _quick_check_latest_market_date() -> str | None:
    """直接查交易日历，确认最新可用的交易日（比抽样查股票K线更直接、更省请求）。"""
    dates = tsc.fetch_trade_dates(
        start_date=(datetime.today() - timedelta(days=10)).strftime("%Y-%m-%d"),
        end_date=datetime.today().strftime("%Y-%m-%d"),
    )
    if not dates:
        return None
    return max(dates).strftime("%Y-%m-%d")


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
    except tsc.TushareFatalError as e:
        _trip_fatal_breaker(state, str(e))
        sys.exit(1)
    codes = stock_df["code"].tolist()
    # 顺便刷新代码->名称映射，供看板显示公司名称（随 HF 同步）
    save_name_map(stock_df)

    factor_check_start = (datetime.today() - timedelta(days=FACTOR_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    if factor_check_start < START_DATE:
        factor_check_start = START_DATE

    # 仅供本地冒烟测试：STOCK_LIMIT=N 只处理前 N 只，默认 0=不限
    limit = int(os.environ.get("STOCK_LIMIT", "0"))
    if limit > 0:
        codes = codes[:limit]

    # -------- 开盘日快速跳过：周末/假日无新数据时直接退出 --------
    local_latest = _get_latest_local_date(codes)
    if local_latest and limit == 0:  # 只在非冒烟模式生效
        try:
            market_latest = _quick_check_latest_market_date()
            if market_latest and local_latest >= market_latest:
                print(f"[Skip] 数据已是最新（本地 {local_latest} >= 市场 {market_latest}），无需更新。")
                state["last_run"] = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
                save_state(state)
                return
        except tsc.TushareFatalError as e:
            _trip_fatal_breaker(state, str(e))
            sys.exit(1)

    # -------- 加载因子检查日期追踪 --------
    factor_check_dates = load_factor_check_dates()
    today_str = datetime.today().strftime("%Y-%m-%d")

    stats = {
        "FULL_REBUILD": 0,
        "INIT": 0,
        "BACKFILL": 0,
        "INCREMENTAL": 0,
        "NOOP": 0,
        "ERROR": 0
    }
    errors = []
    factor_checked_count = 0

    # -------- 多进程并行更新每只股票（tushare 是纯 HTTP 调用，worker 无需登录）--------
    # 注册 SIGTERM 处理器：GitHub Actions 手动取消时发 SIGTERM，立即 terminate() pool，
    # 避免 worker 进程成为孤儿。
    signal.signal(signal.SIGTERM, _sigterm_handler)

    # 跨进程共享的限流锁/状态：所有 worker 共用同一个全局速率，不会因为开多个
    # worker 就把账号每分钟调用上限叠加超标。
    rate_lock = Lock()
    rate_next_allowed = Value("d", 0.0)
    tsc.configure_rate_limiter(rate_lock, rate_next_allowed)

    global _active_pool
    tasks = [(code, factor_check_start, factor_check_dates) for code in codes]
    with Pool(processes=WORKERS, initializer=_pool_init, initargs=(rate_lock, rate_next_allowed)) as pool:
        _active_pool = pool
        for r in tqdm(
            pool.imap(_update_task, tasks, chunksize=CHUNKSIZE),
            total=len(tasks),
            desc="Updating HS Mainboard (front-adjusted)",
        ):
            mode = r["mode"]
            if mode == "FATAL":
                pool.terminate()
                if r.get("factor_checked"):
                    factor_check_dates[r["code"]] = today_str
                save_factor_check_dates(factor_check_dates)
                _trip_fatal_breaker(state, r.get("error"))
                _active_pool = None
                sys.exit(1)
            elif mode == "ERROR":
                stats["ERROR"] += 1
                errors.append((r.get("code"), r.get("error")))
            elif mode == "FULL_REBUILD":
                stats["FULL_REBUILD"] += 1
            elif mode == "INIT":
                stats["INIT"] += 1
            elif mode.startswith("BACKFILL"):
                stats["BACKFILL"] += 1
                if mode.endswith("INCREMENTAL"):
                    stats["INCREMENTAL"] += 1
                else:
                    stats["NOOP"] += 1
            elif mode == "INCREMENTAL":
                stats["INCREMENTAL"] += 1
            elif mode == "INCREMENTAL_NOOP":
                stats["NOOP"] += 1

            # 更新因子检查日期追踪
            if r.get("factor_checked"):
                factor_check_dates[r["code"]] = today_str
                factor_checked_count += 1
    _active_pool = None

    # -------- 持久化 --------
    save_factor_check_dates(factor_check_dates)
    state["last_run"] = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)

    print("\n=== DONE ===")
    print("Saved to:", os.path.abspath(BASE_DIR))
    print("Total stocks:", len(codes))
    print(f"Workers: {WORKERS}")
    print(f"Chunksize: {CHUNKSIZE}")
    print(f"Factors checked this run: {factor_checked_count}/{len(codes)}")
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
