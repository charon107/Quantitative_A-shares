import os
import json
import hashlib
from datetime import datetime, timedelta

import baostock as bs
import pandas as pd
from tqdm import tqdm

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

# =========================
# 路径区
# =========================
DATA_DIR = os.path.join(BASE_DIR, "kline_fq")        # 前复权日线 parquet
FACTOR_DIR = os.path.join(BASE_DIR, "adj_factor")    # 复权因子 parquet
STATE_PATH = os.path.join(BASE_DIR, "state.json")


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


def main():
    ensure_dirs()
    state = load_state()

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")

    try:
        stock_df = get_stock_list()
        codes = stock_df["code"].tolist()

        factor_check_start = (datetime.today() - timedelta(days=FACTOR_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        if factor_check_start < START_DATE:
            factor_check_start = START_DATE

        stats = {
            "FULL_REBUILD": 0,
            "INIT": 0,
            "BACKFILL": 0,
            "INCREMENTAL": 0,
            "NOOP": 0,
            "ERROR": 0
        }
        errors = []

        for code in tqdm(codes, desc="Updating HS Mainboard (front-adjusted)"):
            try:
                r = update_one_stock(code, factor_check_start=factor_check_start)
                mode = r["mode"]

                if mode == "FULL_REBUILD":
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

            except Exception as e:
                stats["ERROR"] += 1
                errors.append((code, str(e)))

        state["last_run"] = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)

        print("\n=== DONE ===")
        print("Saved to:", os.path.abspath(BASE_DIR))
        print("Total stocks:", len(codes))
        print("Stats:", stats)
        if errors:
            print("\n--- Errors (showing up to 30) ---")
            for code, msg in errors[:30]:
                print(code, "=>", msg)

    finally:
        bs.logout()


if __name__ == "__main__":
    main()
