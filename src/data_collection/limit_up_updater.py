import os
import pandas as pd
import akshare as ak
from datetime import datetime

out_csv = "202526涨停.csv"   # 目标CSV文件名（已有就追加，没有就新建）

# （可选）只要沪深主板
ONLY_MAIN_BOARD = False

def read_last_date(csv_path: str):
    """读取CSV中最后一个date（YYYY-MM-DD），没有则返回None"""
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return None
    try:
        # 只读date列即可，省内存
        df0 = pd.read_csv(csv_path, usecols=["date"], encoding="utf-8-sig")
        if df0.empty:
            return None
        d = pd.to_datetime(df0["date"], errors="coerce").dropna()
        return d.max().date() if not d.empty else None
    except Exception:
        # 兼容用户可能用过utf-8写入
        df0 = pd.read_csv(csv_path, usecols=["date"], encoding="utf-8")
        if df0.empty:
            return None
        d = pd.to_datetime(df0["date"], errors="coerce").dropna()
        return d.max().date() if not d.empty else None

def get_trade_days(start_date: str, end_date: str):
    """
    获取A股交易日列表（YYYY-MM-DD字符串列表），闭区间。
    ak.tool_trade_date_hist_sina() 返回的是交易日序列。
    """
    cal = ak.tool_trade_date_hist_sina()
    cal = pd.to_datetime(cal["trade_date"]).dt.date
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()
    days = [d.strftime("%Y%m%d") for d in cal if start <= d <= end]
    return days

def fetch_zt_for_day(yyyymmdd: str):
    """拉取某交易日涨停池并整理成 date,code,name 三列"""
    df = ak.stock_zt_pool_em(date=yyyymmdd)
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "code", "name"])

    date_fmt = pd.to_datetime(yyyymmdd, format="%Y%m%d").strftime("%Y-%m-%d")
    to_add = pd.DataFrame({
        "date": [date_fmt] * len(df),
        "code": df["代码"].astype(str).str.zfill(6),
        "name": df["名称"].astype(str),
    })

    if ONLY_MAIN_BOARD:
        to_add = to_add[to_add["code"].str.startswith(("000","001","600","601","603","605"))].copy()

    # 去重：同一天同代码只留一条
    to_add = to_add.drop_duplicates(subset=["date", "code"], keep="first")
    return to_add

def main():
    today = datetime.now().strftime("%Y-%m-%d")

    last_date = read_last_date(out_csv)
    if last_date is None:
        # 文件不存在或为空：从今天开始（你也可以改成更早的起始日期）
        start_date = today
        print(f"未发现历史数据，将从 {start_date} 开始更新。")
    else:
        start_date = (pd.to_datetime(last_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"现有文件已更新到：{last_date}，将从 {start_date} 开始补齐。")

    # 如果 start_date > today，说明已经最新
    if pd.to_datetime(start_date) > pd.to_datetime(today):
        print(f"已是最新，无需更新（最后日期 {last_date}）。")
        return

    trade_days = get_trade_days(start_date, today)
    if not trade_days:
        print(f"{start_date} ~ {today} 之间没有交易日需要更新。")
        return

    file_exists = os.path.exists(out_csv)
    total_added = 0

    for d in trade_days:
        to_add = fetch_zt_for_day(d)
        if to_add.empty:
            print(f"{d} 无涨停数据（可能未更新/接口返回空）。")
            continue

        # 追加写入
        to_add.to_csv(
            out_csv,
            mode="a",
            header=not file_exists,
            index=False,
            encoding="utf-8-sig"
        )
        file_exists = True
        total_added += len(to_add)
        print(f"{d} 追加 {len(to_add)} 条")

    print(f"完成：共追加 {total_added} 条到 {out_csv}")

if __name__ == "__main__":
    main()
