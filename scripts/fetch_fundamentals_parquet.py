"""在 runner（境外，直连 tushare 代理稳定）抓取基本面财务 + 指数日线，存为 parquet。

随后由 workflow scp 到服务器，服务器用 load_fundamentals.py 只做 DB 活（入库 + 跑选股）。
财务数据按年报口径逐股抓取（fina_indicator 一次拿全部报告期），变动频率低，
默认一次性全历史回填；财报季用 --years 增量刷新最近若干年即可。

产出（--outdir 下）：
  fundamental.parquet  全市场年报财务（code/year/ann_date/roe/netprofit_yoy/
                       debt_to_assets/net_profit/cfo）
  index.parquet        指数日线收盘（默认上证综指 sh.000001；code/date/close）

用法（runner）：
  uv run --no-project --with tushare --with pandas --with pyarrow --with numpy \
    python scripts/fetch_fundamentals_parquet.py --outdir artifacts_fund --full
需环境变量 TUSHARE_TOKEN / TUSHARE_API_URL。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.data_collection import tushare_client as tsc  # noqa: E402

_MB = re.compile(r"^(sh\.60|sz\.00)\d{4}$")
INDEX_CODE = "sh.000001"


def _mainboard_codes() -> list[str]:
    basic = tsc.fetch_stock_basic()
    codes = basic["code"].astype(str)
    return codes[codes.str.match(_MB, na=False)].tolist()


def _fetch_one(code: str) -> pd.DataFrame:
    """拼装单只股票的年报财务宽表（fina_indicator + income + cashflow 按 year 合并）。"""
    fina = tsc.fetch_fina_indicator(code)
    if fina.empty:
        return pd.DataFrame()
    inc = tsc.fetch_income(code)
    cf = tsc.fetch_cashflow(code)
    df = fina.merge(inc, on=["code", "year"], how="left").merge(cf, on=["code", "year"], how="left")
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="artifacts_fund")
    ap.add_argument("--full", action="store_true", help="全历史回填（默认）")
    ap.add_argument("--years", type=int, default=0, help="只保留最近 N 年（增量刷新用；0=全部）")
    ap.add_argument("--limit", type=int, default=0, help="调试：只抓前 N 只股票（0=全部）")
    args = ap.parse_args()
    od = args.outdir
    os.makedirs(od, exist_ok=True)

    codes = _mainboard_codes()
    if args.limit:
        codes = codes[: args.limit]
    total = len(codes)
    print(f"[fetch_fund] 主板股票 {total} 只")

    frames: list[pd.DataFrame] = []
    for i, code in enumerate(codes, 1):
        try:
            df = _fetch_one(code)
            if not df.empty:
                frames.append(df)
        except Exception as e:  # 单只失败不致命，跳过继续
            print(f"[fetch_fund] {code} 失败：{e}")
        if i % 200 == 0:
            print(f"[fetch_fund] 进度 {i}/{total}")

    fund = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["code", "year", "ann_date", "roe", "netprofit_yoy", "debt_to_assets", "net_profit", "cfo"]
    )
    if args.years and not fund.empty:
        max_year = int(pd.to_numeric(fund["year"], errors="coerce").max())
        fund = fund[fund["year"] >= max_year - args.years + 1]
    fund.to_parquet(f"{od}/fundamental.parquet", index=False)

    # 指数日线（全历史）
    try:
        idx = tsc.fetch_index_daily(INDEX_CODE)
    except Exception as e:
        print(f"[fetch_fund] index 失败：{e}")
        idx = pd.DataFrame(columns=["code", "date", "close"])
    idx.to_parquet(f"{od}/index.parquet", index=False)

    print(f"[fetch_fund] fundamental={len(fund)} index={len(idx)} -> {os.path.abspath(od)}")


if __name__ == "__main__":
    main()
