"""在 GitHub Actions runner（境外，直连 tushare 代理稳定）抓取全部所需数据，存为 parquet。

随后由 workflow scp 到服务器，服务器用 load_all_parquet.py 只做 DB 活（重算前复权、入库），
彻底绕开「服务器 -> 网关」的网络不通问题。

产出（--outdir 下）：
  raw_recent.parquet  最近 N 个交易日全市场原始日线（含换手率，沪深主板）
  adj_recent.parquet  最近 N 个交易日复权因子
  meta.parquet        代码->名称
  company.parquet     公司信息（stock_basic + stock_company）
  ths_hot.parquet     同花顺人气榜（最近有数据的交易日）

用法（runner）：
  uv run --no-project --with tushare --with pandas --with pyarrow --with numpy \
    python scripts/fetch_all_parquet.py --outdir artifacts --days 3
需环境变量 TUSHARE_TOKEN / TUSHARE_API_URL。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.data_collection import tushare_client as tsc  # noqa: E402

_MB = re.compile(r"^(sh\.60|sz\.00)\d{4}$")


def _mainboard(df: pd.DataFrame, col: str = "code") -> pd.DataFrame:
    if df is None or df.empty or col not in df.columns:
        return df if df is not None else pd.DataFrame()
    return df[df[col].astype(str).str.match(_MB, na=False)]


def _recent_days(n: int) -> list[str]:
    today = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=n * 3 + 20)).strftime("%Y-%m-%d")
    days = [d.strftime("%Y-%m-%d") for d in tsc.fetch_trade_dates(start, today)]
    return days[-n:] if days else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="artifacts")
    ap.add_argument("--days", type=int, default=3, help="抓取最近 N 个交易日的日线")
    args = ap.parse_args()
    od = args.outdir
    os.makedirs(od, exist_ok=True)

    days = _recent_days(args.days)
    print(f"[fetch_all] 最近交易日：{days}")

    raw_all, adj_all = [], []
    for d in days:
        raw = _mainboard(tsc.fetch_daily_by_date(d))
        turn = _mainboard(tsc.fetch_turnover_by_date(d))
        fac = _mainboard(tsc.fetch_adj_factor_by_date(d))
        if not raw.empty:
            if not turn.empty:
                raw = raw.merge(turn[["code", "turn"]], on="code", how="left")
            else:
                raw = raw.assign(turn=pd.NA)
            raw_all.append(raw)
        if not fac.empty:
            adj_all.append(fac)

    raw_df = pd.concat(raw_all, ignore_index=True) if raw_all else pd.DataFrame()
    adj_df = pd.concat(adj_all, ignore_index=True) if adj_all else pd.DataFrame()
    raw_df.to_parquet(f"{od}/raw_recent.parquet", index=False)
    adj_df.to_parquet(f"{od}/adj_recent.parquet", index=False)

    # 公司信息 + 名称
    try:
        comp = tsc.fetch_company_info()
    except Exception as e:
        print(f"[fetch_all] company 失败：{e}")
        comp = pd.DataFrame()
    comp.to_parquet(f"{od}/company.parquet", index=False)
    meta = (
        comp[["code", "code_name"]].dropna(subset=["code"]).drop_duplicates("code")
        if not comp.empty and "code_name" in comp.columns
        else pd.DataFrame(columns=["code", "code_name"])
    )
    meta.to_parquet(f"{od}/meta.parquet", index=False)

    # 人气榜（从最近交易日往前取第一个有数据的）
    hot = pd.DataFrame()
    for d in reversed(_recent_days(6)):
        try:
            hot = tsc.fetch_ths_hot(d)
        except Exception:
            hot = pd.DataFrame()
        if not hot.empty:
            break
    hot.to_parquet(f"{od}/ths_hot.parquet", index=False)

    print(
        f"[fetch_all] raw={len(raw_df)} adj={len(adj_df)} "
        f"company={len(comp)} meta={len(meta)} hot={len(hot)} -> {os.path.abspath(od)}"
    )


if __name__ == "__main__":
    main()
