import os
import time
import pandas as pd
import baostock as bs
from datetime import datetime, timedelta

# =========================
# 配置区
# =========================
OUT_DIR = "parquet_A股_2007-2024_Q4"  # 你的 parquet 输出目录
SELECTED_CSV = os.path.join(OUT_DIR, "selected_stocks_2013-2025.csv")

# profit 表里有 pubDate（年报披露日）
PROFIT_DIR = os.path.join(OUT_DIR, "profit")

# 输出文件
OUT_DETAIL_CSV = os.path.join(OUT_DIR, "max_possible_return_detail.csv")
OUT_YEAR_SUMMARY_CSV = os.path.join(OUT_DIR, "max_possible_return_year_summary.csv")

# baostock 日线字段：必须含 date/close/high
K_FIELDS = "date,close,high"

# 非交易日对齐方式：
#   "next" = 下一个交易日（推荐，不穿越）
#   "prev" = 上一个交易日
ALIGN_NON_TRADING = "next"

# 为了找到最近交易日，最多向前/向后找多少天
ALIGN_SEARCH_DAYS = 30

# baostock 查询频率控制（视情况调整）
SLEEP_SECONDS_PER_QUERY = 0.0


# =========================
# 工具函数
# =========================
def read_profit_pubdates() -> pd.DataFrame:
    """
    读取 profit 目录，拿到 code + reqYear 对应的 pubDate（年报披露日）
    """
    if not os.path.exists(PROFIT_DIR):
        raise FileNotFoundError(f"找不到 profit 目录：{PROFIT_DIR}")

    df = pd.read_parquet(PROFIT_DIR, columns=["code", "reqYear", "pubDate"])
    df["reqYear"] = pd.to_numeric(df["reqYear"], errors="coerce").astype("Int64")
    df["pubDate"] = pd.to_datetime(df["pubDate"], errors="coerce")

    # 同一 code+reqYear 可能有多行（不同版本/修订），取最后一条（pubDate 最大）
    df = df.dropna(subset=["code", "reqYear", "pubDate"])
    df = df.sort_values(["code", "reqYear", "pubDate"]).drop_duplicates(["code", "reqYear"], keep="last")
    return df


def bs_rs_to_df(rs) -> pd.DataFrame:
    if rs.error_code != "0":
        raise RuntimeError(f"baostock query failed: {rs.error_code} {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields)


def query_kdata(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    查询日线：date, close, high
    start_date/end_date: 'YYYY-MM-DD'
    """
    rs = bs.query_history_k_data_plus(
        code,
        K_FIELDS,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag="2"  # 2=后复权（更适合长期比较）；如果你想不复权可改 3 或 1
    )
    df = bs_rs_to_df(rs)
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["high"] = pd.to_numeric(df["high"], errors="coerce")
    df = df.dropna(subset=["date", "close", "high"]).sort_values("date").reset_index(drop=True)
    return df


def align_to_trading_day(code: str, target_dt: pd.Timestamp, mode: str) -> pd.Timestamp | None:
    """
    如果 target_dt 非交易日，按 mode 找最近交易日（next 或 prev）
    返回对齐后的交易日日期（Timestamp, date=交易日）
    """
    if pd.isna(target_dt):
        return None

    target_dt = pd.Timestamp(target_dt).normalize()

    if mode not in ("next", "prev"):
        raise ValueError("mode must be 'next' or 'prev'")

    if mode == "next":
        start = target_dt.strftime("%Y-%m-%d")
        end = (target_dt + pd.Timedelta(days=ALIGN_SEARCH_DAYS)).strftime("%Y-%m-%d")
        df = query_kdata(code, start, end)
        time.sleep(SLEEP_SECONDS_PER_QUERY)
        if df.empty:
            return None
        # 找 date >= target_dt 的第一天
        cand = df[df["date"] >= target_dt]
        if cand.empty:
            return None
        return cand.iloc[0]["date"].normalize()

    else:  # prev
        start = (target_dt - pd.Timedelta(days=ALIGN_SEARCH_DAYS)).strftime("%Y-%m-%d")
        end = target_dt.strftime("%Y-%m-%d")
        df = query_kdata(code, start, end)
        time.sleep(SLEEP_SECONDS_PER_QUERY)
        if df.empty:
            return None
        cand = df[df["date"] <= target_dt]
        if cand.empty:
            return None
        return cand.iloc[-1]["date"].normalize()


def compute_max_possible_return(code: str, start_pub: pd.Timestamp, end_pub: pd.Timestamp | None) -> dict:
    """
    从 start_pub 对齐后的交易日起（买入 close），到 end_pub 对齐后的交易日止，
    计算区间内 max(high)/start_close - 1
    """
    start_td = align_to_trading_day(code, start_pub, ALIGN_NON_TRADING)
    if start_td is None:
        return {"error": "start_trading_day_not_found"}

    # end_pub 可能缺失（比如最后一年没有下一份年报）
    if end_pub is not None and not pd.isna(end_pub):
        end_td = align_to_trading_day(code, end_pub, ALIGN_NON_TRADING)
        if end_td is None:
            return {"error": "end_trading_day_not_found"}
    else:
        end_td = None

    # 拉取区间数据
    start_str = start_td.strftime("%Y-%m-%d")

    if end_td is None:
        # 没有结束披露日：就拉到今天（你也可以改成拉到 2025-12-31 等）
        end_td = pd.Timestamp.today().normalize()
    end_str = end_td.strftime("%Y-%m-%d")

    if end_td < start_td:
        return {"error": "end_before_start"}

    df = query_kdata(code, start_str, end_str)
    time.sleep(SLEEP_SECONDS_PER_QUERY)
    if df.empty:
        return {"error": "no_kdata_in_range"}

    # 买入价 = start_td 当天 close
    first = df[df["date"] == start_td]
    if first.empty:
        # 理论上不该发生，因为 start_td 就是用 kdata 找出来的
        return {"error": "start_close_missing"}

    start_close = float(first.iloc[0]["close"])

    # 区间最高价
    idx = df["high"].idxmax()
    max_high = float(df.loc[idx, "high"])
    max_high_date = df.loc[idx, "date"].strftime("%Y-%m-%d")

    max_ret = max_high / start_close - 1.0

    return {
        "start_trading_day": start_td.strftime("%Y-%m-%d"),
        "start_close": start_close,
        "end_trading_day": end_td.strftime("%Y-%m-%d"),
        "max_high": max_high,
        "max_high_date": max_high_date,
        "max_return": max_ret,
        "days": int((end_td - start_td).days) + 1
    }


# =========================
# 主流程
# =========================
def main():
    if not os.path.exists(SELECTED_CSV):
        raise FileNotFoundError(f"找不到选股结果：{SELECTED_CSV}")

    selected = pd.read_csv(SELECTED_CSV, dtype={"year": int, "code": str, "name": str})
    selected["year"] = pd.to_numeric(selected["year"], errors="coerce").astype(int)

    pub = read_profit_pubdates()  # code, reqYear, pubDate

    # 构造字典：pubDate_map[(code, reqYear)] = pubDate
    pub_map = {(r["code"], int(r["reqYear"])): r["pubDate"] for _, r in pub.iterrows()}

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_code} {lg.error_msg}")

    try:
        rows = []

        for i, r in selected.iterrows():
            select_year = int(r["year"])
            code = r["code"]
            name = r.get("name", "")

            fy_end = select_year - 1
            start_pub = pub_map.get((code, fy_end), None)
            end_pub = pub_map.get((code, fy_end + 1), None)  # 下一份年报披露日

            out = {
                "select_year": select_year,
                "fy_end": fy_end,
                "code": code,
                "name": name,
                "start_pubDate": "" if start_pub is None else pd.Timestamp(start_pub).strftime("%Y-%m-%d"),
                "end_pubDate": "" if end_pub is None else pd.Timestamp(end_pub).strftime("%Y-%m-%d"),
            }

            if start_pub is None or pd.isna(start_pub):
                out["error"] = "start_pubDate_missing"
                rows.append(out)
                continue

            res = compute_max_possible_return(code, start_pub, end_pub)
            out.update(res)
            rows.append(out)

            if (i + 1) % 50 == 0:
                print(f"Processed {i+1}/{len(selected)}")

        detail = pd.DataFrame(rows)

        # 输出明细
        detail.to_csv(OUT_DETAIL_CSV, index=False, encoding="utf-8-sig")
        print(f"[OK] 明细输出：{OUT_DETAIL_CSV}")

        # 年度汇总（只统计无 error 的行）
        ok = detail[detail.get("error").isna()].copy() if "error" in detail.columns else detail.copy()
        if not ok.empty:
            ok["max_return"] = pd.to_numeric(ok["max_return"], errors="coerce")
            summary = ok.groupby("select_year").agg(
                n=("code", "count"),
                mean_max_return=("max_return", "mean"),
                median_max_return=("max_return", "median"),
                p90_max_return=("max_return", lambda s: s.quantile(0.9)),
                max_of_max_return=("max_return", "max"),
            ).reset_index()

            summary.to_csv(OUT_YEAR_SUMMARY_CSV, index=False, encoding="utf-8-sig")
            print(f"[OK] 年度汇总输出：{OUT_YEAR_SUMMARY_CSV}")
        else:
            print("[WARN] 没有可用于汇总的有效记录（全部 error）")

    finally:
        bs.logout()


if __name__ == "__main__":
    main()
