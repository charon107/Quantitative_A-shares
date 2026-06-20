import os
import json
import atexit
import hashlib
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor

import baostock as bs
import pandas as pd
from tqdm import tqdm

# pandas>=2.0 移除了 DataFrame.append，而 baostock 0.9.2 的 resultset.get_data()
# 仍在调用它（CI 用 `uv sync --frozen` 会重装原版 baostock，故必须在项目侧兜底）。
# 这里补一个等价垫片，把 append 转成 concat，保证拉取股价/股票列表不报 AttributeError。
if not hasattr(pd.DataFrame, "append"):
    def _df_append(self, other, ignore_index=False, verify_integrity=False, sort=False):
        others = other if isinstance(other, (list, tuple)) else [other]
        frames = [self] + [
            o if isinstance(o, pd.DataFrame) else pd.DataFrame(o) for o in others
        ]
        return pd.concat(frames, ignore_index=ignore_index)

    pd.DataFrame.append = _df_append

# =========================
# 配置区
# =========================
START_DATE = "2025-01-01"              # 拉取起始日期
BASE_DIR = "股价数据_parquet_fq"      # 数据保存根目录
ADJUSTFLAG = "2"                       # 1后复权 2前复权 3不复权

# 日线字段（baostock）
K_FIELDS = "date,code,open,high,low,close,volume,amount,turn,pctChg"

# 检查复权因子是否变化：每次运行回看最近 N 天（越大越保险，越慢一点）
FACTOR_LOOKBACK_DAYS = 30

# 并行抓取的进程数（baostock 连接是模块级全局、非线程安全，只能多进程；
# 每进程独立登录。限流报错时调小此值即可）。
WORKERS = int(os.environ.get("BAOSTOCK_WORKERS", "8"))

# =========================
# 路径区
# =========================
DATA_DIR = os.path.join(BASE_DIR, "kline_fq")        # 前复权日线 parquet
FACTOR_DIR = os.path.join(BASE_DIR, "adj_factor")    # 复权因子 parquet
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
    sort_cols = [c for c in ["code", "dividOperateDate"] if c in use.columns]
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
    """
    获取沪深主板股票列表（约3000+）
    优先 query_stock_basic，失败则兜底 query_all_stock
    """
    rs = bs.query_stock_basic()
    if rs.error_code == "0":
        df = rs.get_data()
        if df is not None and not df.empty:
            return filter_mainboard(df)

    rs2 = bs.query_all_stock()
    if rs2.error_code != "0":
        raise RuntimeError(f"Stock list query failed: {rs2.error_msg}")

    df2 = rs2.get_data()
    if df2 is None or df2.empty:
        raise RuntimeError("query_all_stock returned empty DataFrame.")

    return filter_mainboard(df2)


def save_name_map(stock_df: pd.DataFrame):
    """
    保存代码->公司名称映射到 parquet，供可视化看板显示公司名称。

    数据随 股价数据_parquet_fq/ 目录一起同步到 Hugging Face，
    看板只读本地文件，无需自己联网调 baostock。

    query_stock_basic 返回 code_name 列；query_all_stock 兜底返回 code_name。
    若两者都没有名称列，则跳过（看板回退到只显示代码）。
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
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        stock_df = get_stock_list()
        save_name_map(stock_df)
    finally:
        bs.logout()


def fetch_kline_fq(code: str, start_date: str, end_date: str = "") -> pd.DataFrame:
    """
    拉取前复权日线
    """
    rs = bs.query_history_k_data_plus(
        code,
        K_FIELDS,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag=ADJUSTFLAG
    )
    if rs.error_code != "0":
        raise RuntimeError(f"query_history_k_data_plus failed for {code}: {rs.error_msg}")

    df = rs.get_data()
    if df is None or df.empty:
        return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    num_cols = ["open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date"])
    df = df.sort_values(["date"]).drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return df


def fetch_adjust_factor(code: str, start_date: str, end_date: str = "") -> pd.DataFrame:
    """
    拉取复权因子，用来判断是否需要重算前复权历史
    """
    rs = bs.query_adjust_factor(code=code, start_date=start_date, end_date=end_date)
    if rs.error_code != "0":
        raise RuntimeError(f"query_adjust_factor failed for {code}: {rs.error_msg}")

    df = rs.get_data()
    if df is None or df.empty:
        return pd.DataFrame()

    if "dividOperateDate" in df.columns:
        df["dividOperateDate"] = pd.to_datetime(df["dividOperateDate"], errors="coerce")
    return df


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


def update_one_stock(code: str, factor_check_start: str) -> dict:
    """
    更新单只股票：
    - 复权因子变化 => 全量重拉前复权并覆盖
    - 否则：若 START_DATE 早于本地最早日期 => 向前回补缺口
            再做增量追加
    """
    k_path = os.path.join(DATA_DIR, f"{code}.parquet")
    f_path = os.path.join(FACTOR_DIR, f"{code}.parquet")

    # -------- 复权因子检查（决定是否全量重拉）--------
    factor_old = read_parquet_if_exists(f_path)
    old_hash = stable_hash_df(
        factor_old,
        cols=["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"]
    )

    factor_new = fetch_adjust_factor(code, start_date=factor_check_start, end_date="")
    if not factor_new.empty:
        factor_all = pd.concat([factor_old, factor_new], ignore_index=True) if not factor_old.empty else factor_new
        factor_all = factor_all.drop_duplicates()
        if "dividOperateDate" in factor_all.columns:
            factor_all = factor_all.sort_values(["dividOperateDate"])
    else:
        factor_all = factor_old

    new_hash = stable_hash_df(
        factor_all,
        cols=["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"]
    )
    factor_changed = (new_hash != old_hash)

    if factor_all is not None and not factor_all.empty:
        factor_all.to_parquet(f_path, index=False)

    existing = read_parquet_if_exists(k_path)
    first_date = get_first_date(existing)
    last_date = get_last_date(existing)

    # -------- 若复权因子变化：全量重拉并覆盖（从 START_DATE 开始）--------
    if factor_changed:
        df_full = fetch_kline_fq(code, start_date=START_DATE, end_date="")
        if df_full.empty:
            return {"code": code, "mode": "FULL_REBUILD_EMPTY", "rows": 0, "last_date": last_date}
        df_full.to_parquet(k_path, index=False)
        return {
            "code": code,
            "mode": "FULL_REBUILD",
            "rows": len(df_full),
            "first_date": df_full["date"].min().strftime("%Y-%m-%d"),
            "last_date": df_full["date"].max().strftime("%Y-%m-%d"),
        }

    # -------- 若本地无数据：初始化（从 START_DATE 开始）--------
    if last_date is None:
        df_init = fetch_kline_fq(code, start_date=START_DATE, end_date="")
        if df_init.empty:
            return {"code": code, "mode": "INIT_EMPTY", "rows": 0, "first_date": None, "last_date": None}
        df_init.to_parquet(k_path, index=False)
        return {
            "code": code,
            "mode": "INIT",
            "rows": len(df_init),
            "first_date": df_init["date"].min().strftime("%Y-%m-%d"),
            "last_date": df_init["date"].max().strftime("%Y-%m-%d"),
        }

    # -------- 新增：向前回补缺口（关键改动）--------
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

    # -------- 再做向后增量更新 --------
    inc_start = next_day(last_date)
    df_inc = fetch_kline_fq(code, start_date=inc_start, end_date="")
    if df_inc.empty:
        mode = "INCREMENTAL_NOOP"
        if did_backfill:
            mode = "BACKFILL+NOOP"
        return {"code": code, "mode": mode, "rows": 0, "first_date": first_date, "last_date": last_date}

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
    }


def _pool_init():
    """每个 worker 进程启动时登录一次 baostock，进程退出时登出。"""
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed in worker: {lg.error_msg}")
    atexit.register(bs.logout)


def _update_task(args):
    """进程池任务：更新单只股票，异常转成 ERROR 结果而非抛出（避免拖垮整个池）。"""
    code, factor_check_start = args
    try:
        return update_one_stock(code, factor_check_start=factor_check_start)
    except Exception as e:
        return {"code": code, "mode": "ERROR", "error": str(e)}


def main():
    ensure_dirs()
    state = load_state()

    # -------- 主进程登录一次：仅用于拉股票列表 + 刷新名称映射 --------
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        stock_df = get_stock_list()
        codes = stock_df["code"].tolist()
        # 顺便刷新代码->名称映射，供看板显示公司名称（随 HF 同步）
        save_name_map(stock_df)
    finally:
        bs.logout()

    factor_check_start = (datetime.today() - timedelta(days=FACTOR_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    if factor_check_start < START_DATE:
        factor_check_start = START_DATE

    # 仅供本地冒烟测试：STOCK_LIMIT=N 只处理前 N 只，默认 0=不限
    limit = int(os.environ.get("STOCK_LIMIT", "0"))
    if limit > 0:
        codes = codes[:limit]

    stats = {
        "FULL_REBUILD": 0,
        "INIT": 0,
        "BACKFILL": 0,
        "INCREMENTAL": 0,
        "NOOP": 0,
        "ERROR": 0
    }
    errors = []

    # -------- 多进程并行更新每只股票（每进程独立 baostock 登录）--------
    tasks = [(code, factor_check_start) for code in codes]
    with ProcessPoolExecutor(max_workers=WORKERS, initializer=_pool_init) as ex:
        for r in tqdm(
            ex.map(_update_task, tasks, chunksize=16),
            total=len(tasks),
            desc="Updating HS Mainboard (front-adjusted)",
        ):
            mode = r["mode"]
            if mode == "ERROR":
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

    state["last_run"] = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)

    print("\n=== DONE ===")
    print("Saved to:", os.path.abspath(BASE_DIR))
    print("Total stocks:", len(codes))
    print(f"Workers: {WORKERS}")
    print("Stats:", stats)
    if errors:
        print("\n--- Errors (showing up to 30) ---")
        for code, msg in errors[:30]:
            print(code, "=>", msg)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "name-map":
        # 轻量模式：只生成代码->名称映射，不拉取K线
        build_name_map_only()
    else:
        main()
