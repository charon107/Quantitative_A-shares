import os
import glob
import pandas as pd

from src.data_collection import tushare_client as tsc


# ================== 参数 ==================
DATA_DIR = "沪深主板微信指数"

# 回测月份：2025年1月~5月
MONTH_START = "2025-01-01"
MONTH_END   = "2025-05-31"

LOOKBACK = 5                  # 微信指数前5日

# 回测参数
TAKE_PROFIT = 0.05            # 未来n日内最高价达到 +5%
FWD_DAYS = 20                 # 未来20个交易日（不含买入日）

# 价格约束参数（只对条件2/3/4生效：只要入池 reason 里出现 C2/C3/C4 就检查）
PRICE_LOOKBACK = 10           # 前10个交易日（不含当日）
PRICE_RANGE_MAX = 0.1         # 不超过10%

# 新增：target_date 当日跌幅不超过 5%（对所有启用条件都生效）
MAX_DAILY_DROP = 0.05

# 新增：target_date 当日涨幅不超过 10%（对所有启用条件都生效）
MAX_DAILY_RISE = 0.1

# 新增：C4 参数 - 破过去30个交易日（不含当日）新高
C4_LOOKBACK_DAYS = 30

# ======== 新增：条件开关（只回测部分条件）========
# 可选：["C1","C2","C3","C4"] 的任意子集
# 例：只回测新条件：ACTIVE_CONDITIONS = ["C4"]
ACTIVE_CONDITIONS = ["C2","C3"]

# 复权口径：2=前复权（统一口径）
ADJUSTFLAG = "2"

# 输出文件
OUT_XLSX = "wechat_index_backtest_2025-1-5_withC4_partial.xlsx"
# =========================================


# --------- 进度条：tqdm（没有就自动降级）---------
try:
    from tqdm.auto import tqdm
except Exception:
    def tqdm(x, **kwargs):
        return x


def load_one_parquet(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if df.empty:
        return df

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["wechat_index"] = pd.to_numeric(df["wechat_index"], errors="coerce")

    df = (
        df.dropna(subset=["date"])
          .sort_values("date")
          .drop_duplicates(subset=["date"], keep="last")
          .reset_index(drop=True)
    )
    return df


def pick_window(df: pd.DataFrame, target_date: pd.Timestamp, lookback: int = 5):
    """
    取：
    - target_date 当天
    - target_date 之前最近 lookback 条数据
    """
    df2 = df[df["date"] <= target_date]
    if df2.empty:
        return None, None

    target_df = df2[df2["date"] == target_date]
    if target_df.empty:
        return None, None

    prev_df = df2[df2["date"] < target_date].tail(lookback)
    if len(prev_df) < lookback:
        return None, None

    return prev_df, target_df.iloc[-1]


def pick_prev_n(df: pd.DataFrame, target_date: pd.Timestamp, n: int):
    """
    取 target_date 之前（不含当日）最近 n 条数据
    """
    df2 = df[df["date"] < target_date].sort_values("date")
    if df2.empty:
        return None
    prev_df = df2.tail(n)
    if len(prev_df) < n:
        return None
    return prev_df


def check_conditions(
    df_full: pd.DataFrame,
    prev_df: pd.DataFrame,
    target_row: pd.Series,
    active_conditions: list[str],
    c4_lookback_days: int = C4_LOOKBACK_DAYS,
):
    """
    返回 (passed, reasons)
    reasons 里只包含 active_conditions 中启用的条件。
    注意：C4 依赖 C2 的“连续上涨”作为前置，但即便未启用 C2，也会内部计算一次用于 C4 判定。
    """
    prev_vals = prev_df["wechat_index"].tolist()
    t = target_row["wechat_index"]

    if pd.isna(t) or any(pd.isna(x) for x in prev_vals):
        return False, []

    reasons = []

    # C1
    if "C1" in active_conditions:
        if t > 20000 and all(x < 5000 for x in prev_vals):
            reasons.append("C1: t>20000 & prev5<5000")

    # C2（严格单调上涨）
    c2_ok = False
    if ("C2" in active_conditions) or ("C4" in active_conditions):
        seq = prev_vals + [t]
        c2_ok = all(seq[i] < seq[i + 1] for i in range(len(seq) - 1))
        if ("C2" in active_conditions) and c2_ok:
            reasons.append("C2: 连续上涨")

    # C3
    if "C3" in active_conditions:
        prev_mean = sum(prev_vals) / len(prev_vals)
        if prev_mean > 0 and t >= 15 * prev_mean:
            reasons.append("C3: t>=15*mean(prev5)")

    # C4：在满足C2基础上，且 target_date 的微信指数 > 过去30个交易日（不含当日）新高
    if "C4" in active_conditions:
        if c2_ok:
            td = pd.to_datetime(target_row["date"])
            prev30 = pick_prev_n(df_full, td, c4_lookback_days)
            if prev30 is not None:
                prev30_max = float(prev30["wechat_index"].max())
                if pd.notna(prev30_max) and t > prev30_max:
                    reasons.append("C4: C2 & t>max(prev30)")

    return len(reasons) > 0, reasons


def to_baostock_code(code6: str) -> str:
    c = str(code6).zfill(6)
    if c.startswith("6"):
        return f"sh.{c}"
    if c.startswith(("0", "3")):
        return f"sz.{c}"
    if c.startswith("8"):
        return f"bj.{c}"
    return f"sz.{c}"


def daily_pct_change_ok_with_baostock(
    code6: str,
    target_date: pd.Timestamp,
    max_drop: float = MAX_DAILY_DROP,   # 最大允许跌幅（正数，比如 0.05）
    max_rise: float = MAX_DAILY_RISE,   # 最大允许涨幅（正数，比如 0.10）
    adjustflag: str = ADJUSTFLAG,       # 2=前复权（统一口径）
) -> bool:
    """
    计算 target_date 当日涨跌幅（close / prev_close - 1）
    要求：-max_drop <= pct_change <= max_rise
    数据不足（拿不到前一交易日或当日收盘）则返回 False
    """
    bs_code = to_baostock_code(code6)

    start = (target_date - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    end = (target_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    hist = tsc.fetch_kline_qfq(bs_code, start_date=start, end_date=end, fields=["date", "close"])
    if hist.empty:
        return False
    hist = hist.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)

    w = hist[hist["date"] <= target_date].tail(2).reset_index(drop=True)
    if len(w) < 2:
        return False

    prev_close = float(w.loc[0, "close"])
    today_close = float(w.loc[1, "close"])
    if prev_close <= 0:
        return False

    pct_change = (today_close / prev_close) - 1.0
    return (-max_drop) <= pct_change <= max_rise


def prevN_price_range_ok_with_baostock(
    code6: str,
    target_date: pd.Timestamp,
    n_days: int = 7,
    threshold: float = 0.10,
    adjustflag: str = ADJUSTFLAG,   # 2=前复权（统一口径）
) -> bool:
    """
    取 target_date 之前（不含当日）最近 n_days 个交易日：
    - 用这 n_days 内的最高价 high_max 与最低价 low_min
    - 若 high_max 出现在 low_min 之前： (high_max - low_min) / high_max <= threshold
      否则：                         (high_max - low_min) / low_min  <= threshold
    不足 n_days 个交易日则返回 False
    """
    bs_code = to_baostock_code(code6)

    start = (target_date - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    end = (target_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    hist = tsc.fetch_kline_qfq(bs_code, start_date=start, end_date=end, fields=["date", "high", "low"])
    if hist.empty:
        return False
    hist = hist.dropna(subset=["date", "high", "low"]).sort_values("date").reset_index(drop=True)

    w = hist[hist["date"] < target_date].tail(n_days).reset_index(drop=True)
    if len(w) < n_days:
        return False

    idx_max = int(w["high"].idxmax())
    idx_min = int(w["low"].idxmin())
    high_max = float(w.loc[idx_max, "high"])
    low_min = float(w.loc[idx_min, "low"])

    if high_max <= 0 or low_min <= 0:
        return False

    if idx_max < idx_min:
        ratio = (high_max - low_min) / high_max
    else:
        ratio = (high_max - low_min) / low_min

    return ratio <= threshold


def calc_forward_hit_rate_with_baostock(
    result_df: pd.DataFrame,
    target_date: pd.Timestamp,
    fwd_days: int = 10,
    take_profit: float = 0.05,
    show_pbar: bool = True,
    adjustflag: str = ADJUSTFLAG,   # 2=前复权（统一口径）
):
    """
    对 result_df 中入池股票（该 target_date 当天的池）做回测：
    - buy_date: target_date 之后的第一个交易日
    - buy_close: buy_date 的收盘价
    - future_window: buy_date 之后（不含 buy_date）连续 fwd_days 个交易日
    - max_high: future_window 区间最高价最大值
    - hit: max_high >= buy_close * (1 + take_profit)

    输出：
    - min_low_next10d: future_window 最低价
    - min_drawdown_next10d: (min_low_next10d / buy_close) - 1
    - days_to_hit: 首次命中发生在买入后的第几个交易日(1~fwd_days)，不命中则 NaN
    - hit_date: 首次命中日期（不命中为空）
    """
    if result_df.empty:
        return pd.DataFrame(), 0.0

    start = (target_date - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    end = (target_date + pd.Timedelta(days=120)).strftime("%Y-%m-%d")

    details = []

    it = result_df.itertuples(index=False)
    if show_pbar:
        it = tqdm(list(it), total=len(result_df), desc=f"Backtest {target_date.date()}", leave=False)

    for row in it:
        code6 = str(getattr(row, "code")).zfill(6)
        bs_code = to_baostock_code(code6)

        hist = tsc.fetch_kline_qfq(
            bs_code, start_date=start, end_date=end,
            fields=["date", "code", "close", "high", "low"],
        )
        if hist.empty:
            continue
        hist = hist.dropna(subset=["date", "close", "high", "low"]).sort_values("date").reset_index(drop=True)

        after = hist[hist["date"] > target_date]
        if after.empty:
            continue

        buy_row = after.iloc[0]
        buy_date = buy_row["date"]
        buy_close = float(buy_row["close"])

        future = after.iloc[1:1 + fwd_days]
        if len(future) < fwd_days:
            continue

        take_profit_px = buy_close * (1.0 + take_profit)

        max_high = float(future["high"].max())
        min_low = float(future["low"].min())
        min_drawdown = (min_low / buy_close) - 1.0

        hit_mask = future["high"] >= take_profit_px
        if hit_mask.any():
            first_hit_pos = int(hit_mask.idxmax())
            first_hit_iloc = int(future.index.get_loc(first_hit_pos))  # 0-based
            days_to_hit = first_hit_iloc + 1
            hit_date = pd.to_datetime(future.iloc[first_hit_iloc]["date"]).date()
            hit = True
        else:
            days_to_hit = float("nan")
            hit_date = pd.NaT
            hit = False

        max_return = (max_high / buy_close) - 1.0

        details.append({
            "target_date": target_date.date(),
            "code": code6,
            "name": getattr(row, "name", ""),
            "target_index": getattr(row, "target_index", None),

            "buy_date": buy_date.date(),
            "buy_close": buy_close,

            "max_high_next10d": max_high,
            "max_return_next10d": max_return,
            "hit_take_profit": hit,

            "min_low_next10d": min_low,
            "min_drawdown_next10d": min_drawdown,
            "days_to_hit": days_to_hit,
            "hit_date": hit_date if pd.notna(hit_date) else None,
        })

    detail_df = pd.DataFrame(details)
    if detail_df.empty:
        return detail_df, 0.0

    hit_rate = float(detail_df["hit_take_profit"].mean())
    return detail_df, hit_rate


def add_condition_flags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    r = df["reason"].fillna("").astype(str)
    df["is_C1"] = r.str.contains(r"\bC1:", regex=True)
    df["is_C2"] = r.str.contains(r"\bC2:", regex=True)
    df["is_C3"] = r.str.contains(r"\bC3:", regex=True)
    df["is_C4"] = r.str.contains(r"\bC4:", regex=True)
    return df


def summarize_by_condition(df: pd.DataFrame, cond_col: str) -> dict:
    sub = df[df[cond_col]].copy()
    sub = sub.dropna(subset=["max_return_next10d", "hit_take_profit"])
    if sub.empty:
        return {"n": 0, "hit_rate": float("nan"), "avg_pnl": float("nan")}

    return {
        "n": int(len(sub)),
        "hit_rate": float(sub["hit_take_profit"].mean()),
        "avg_pnl": float(sub["max_return_next10d"].mean()),
    }


def get_trading_days_from_baostock(start_date: str, end_date: str) -> list[pd.Timestamp]:
    return [d.normalize() for d in tsc.fetch_trade_dates(start_date, end_date)]


def main():
    # 1) 获取交易日
    trading_days = get_trading_days_from_baostock(MONTH_START, MONTH_END)
    if not trading_days:
        raise RuntimeError("未获取到交易日，请检查 MONTH_START/MONTH_END 或 tushare 接口返回。")

    # 2) 预加载所有 parquet（避免每个交易日重复读盘）
    paths = sorted(glob.glob(os.path.join(DATA_DIR, "*.parquet")))
    if not paths:
        raise RuntimeError("目录下未找到 parquet 文件")

    cache = {}  # code -> df
    for p in tqdm(paths, desc="Loading parquet", leave=False):
        try:
            df = load_one_parquet(p)
            if df.empty:
                continue
            if "code" not in df.columns:
                continue
            code6 = str(df["code"].iloc[-1]).zfill(6)
            cache[code6] = df
        except Exception:
            continue

    if not cache:
        raise RuntimeError("未能从 parquet 加载到任何有效数据（缺少 code/date/wechat_index？）")

    # 3) 逐交易日跑入池 + 回测 + 统计
    all_pools = []
    all_details = []
    daily_summaries = []

    for target_date in tqdm(trading_days, desc="Processing trading days"):
        target_date = pd.to_datetime(target_date)

        pool = []
        items = list(cache.items())
        for code6, df in tqdm(items, total=len(items), desc=f"{target_date.date()} scan", leave=False):
            try:
                prev_df, target_row = pick_window(df, target_date, LOOKBACK)
                if target_row is None or prev_df is None:
                    continue

                passed, reasons = check_conditions(
                    df_full=df,
                    prev_df=prev_df,
                    target_row=target_row,
                    active_conditions=ACTIVE_CONDITIONS,
                    c4_lookback_days=C4_LOOKBACK_DAYS,
                )
                if not passed:
                    continue

                # 对所有启用条件都生效 —— target_date 当日涨跌幅区间约束
                pct_ok = daily_pct_change_ok_with_baostock(
                    code6=code6,
                    target_date=target_date,
                    max_drop=MAX_DAILY_DROP,
                    max_rise=MAX_DAILY_RISE,
                    adjustflag=ADJUSTFLAG,
                )
                if not pct_ok:
                    continue

                # 价格约束：只要 reason 里有 C2/C3/C4，就检查；不通过就移除这些 reason
                need_price_filter = any(
                    r.startswith(("C2:", "C3:", "C4:")) for r in reasons
                )
                if need_price_filter:
                    price_ok = prevN_price_range_ok_with_baostock(
                        code6=code6,
                        target_date=target_date,
                        n_days=PRICE_LOOKBACK,
                        threshold=PRICE_RANGE_MAX,
                        adjustflag=ADJUSTFLAG,
                    )
                    if not price_ok:
                        reasons = [r for r in reasons if not r.startswith(("C2:", "C3:", "C4:"))]

                if not reasons:
                    continue

                pool.append({
                    "target_date": target_date.date(),
                    "code": str(target_row["code"]).zfill(6),
                    "name": target_row.get("name", ""),
                    "target_index": target_row.get("wechat_index"),
                    "prev5_mean": prev_df["wechat_index"].mean(),
                    "prev5_min": prev_df["wechat_index"].min(),
                    "prev5_max": prev_df["wechat_index"].max(),
                    "reason": "; ".join(reasons),
                })
            except Exception:
                continue

        result_df = pd.DataFrame(pool)
        if not result_df.empty:
            result_df = result_df.sort_values(
                ["target_index", "code"],
                ascending=[False, True]
            ).reset_index(drop=True)

        # 回测（按当天池子）
        detail_df, hit_rate = calc_forward_hit_rate_with_baostock(
            result_df=result_df,
            target_date=target_date,
            fwd_days=FWD_DAYS,
            take_profit=TAKE_PROFIT,
            show_pbar=True,
            adjustflag=ADJUSTFLAG,
        )

        # 合并回测字段
        if not result_df.empty and not detail_df.empty:
            result_df = result_df.merge(
                detail_df[[
                    "target_date", "code",
                    "buy_date", "buy_close",
                    "max_high_next10d", "max_return_next10d", "hit_take_profit",
                    "min_low_next10d", "min_drawdown_next10d",
                    "days_to_hit", "hit_date"
                ]],
                on=["target_date", "code"],
                how="left"
            )

        # 分条件统计（仅对有回测结果的）
        if not result_df.empty:
            result_df = add_condition_flags(result_df)

            overall = result_df.dropna(subset=["max_return_next10d"])
            bt_sample_n = int(overall.shape[0])
            bt_hit_rate = float(overall["hit_take_profit"].mean()) if bt_sample_n > 0 else float("nan")
            bt_avg_pnl = float(overall["max_return_next10d"].mean()) if bt_sample_n > 0 else float("nan")

            bt_avg_drawdown = float(overall["min_drawdown_next10d"].mean()) if bt_sample_n > 0 else float("nan")
            hit_only = overall[overall["hit_take_profit"] == True]
            bt_avg_days_to_hit = float(hit_only["days_to_hit"].mean()) if not hit_only.empty else float("nan")

            stats_C1 = summarize_by_condition(result_df, "is_C1")
            stats_C2 = summarize_by_condition(result_df, "is_C2")
            stats_C3 = summarize_by_condition(result_df, "is_C3")
            stats_C4 = summarize_by_condition(result_df, "is_C4")
        else:
            bt_sample_n = 0
            bt_hit_rate = float("nan")
            bt_avg_pnl = float("nan")
            bt_avg_drawdown = float("nan")
            bt_avg_days_to_hit = float("nan")
            stats_C1 = {"n": 0, "hit_rate": float("nan"), "avg_pnl": float("nan")}
            stats_C2 = {"n": 0, "hit_rate": float("nan"), "avg_pnl": float("nan")}
            stats_C3 = {"n": 0, "hit_rate": float("nan"), "avg_pnl": float("nan")}
            stats_C4 = {"n": 0, "hit_rate": float("nan"), "avg_pnl": float("nan")}

        daily_summaries.append({
            "target_date": target_date.date(),
            "pool_size": int(len(result_df)),
            "backtest_sample_n": bt_sample_n,
            "backtest_hit_rate": bt_hit_rate,
            "backtest_avg_pnl": bt_avg_pnl,

            "backtest_avg_min_drawdown": bt_avg_drawdown,
            "backtest_avg_days_to_hit": bt_avg_days_to_hit,

            "C1_n": stats_C1["n"], "C1_hit_rate": stats_C1["hit_rate"], "C1_avg_pnl": stats_C1["avg_pnl"],
            "C2_n": stats_C2["n"], "C2_hit_rate": stats_C2["hit_rate"], "C2_avg_pnl": stats_C2["avg_pnl"],
            "C3_n": stats_C3["n"], "C3_hit_rate": stats_C3["hit_rate"], "C3_avg_pnl": stats_C3["avg_pnl"],
            "C4_n": stats_C4["n"], "C4_hit_rate": stats_C4["hit_rate"], "C4_avg_pnl": stats_C4["avg_pnl"],
        })

        if not result_df.empty:
            all_pools.append(result_df)
        if not detail_df.empty:
            all_details.append(detail_df)

    pools_all = pd.concat(all_pools, ignore_index=True) if all_pools else pd.DataFrame()
    details_all = pd.concat(all_details, ignore_index=True) if all_details else pd.DataFrame()
    summary_df = pd.DataFrame(daily_summaries).sort_values("target_date").reset_index(drop=True)

    # 4) 写 Excel
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="daily_summary", index=False)
        pools_all.to_excel(writer, sheet_name="pools_all", index=False)
        details_all.to_excel(writer, sheet_name="backtest_detail", index=False)

    print(f"\n已完成：{MONTH_START} ~ {MONTH_END} 回测结果已保存到：{OUT_XLSX}")
    print(f"交易日数量：{len(trading_days)} | 总入池行数：{len(pools_all)} | 总回测明细行数：{len(details_all)}")
    print(f"本次启用条件：{ACTIVE_CONDITIONS}")



if __name__ == "__main__":
    main()
