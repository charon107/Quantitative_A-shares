"""在 GitHub Actions runner（境外，直连 tushare 代理更稳）抓取同花顺人气榜，存为 parquet。

随后由 workflow scp 到服务器，服务器用 load_ths_hot_parquet.py 加载进 DuckDB，
绕开「服务器 -> 网关」对 ths_hot 的偶发读超时。

用法：uv run --with tushare --with pandas --with pyarrow \
        python scripts/fetch_ths_hot_parquet.py --out ths_hot.parquet
需环境变量 TUSHARE_TOKEN / TUSHARE_API_URL。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.data_collection import tushare_client as tsc  # noqa: E402


def latest_trade_date() -> str:
    today = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=20)).strftime("%Y-%m-%d")
    days = tsc.fetch_trade_dates(start, today)
    return days[-1].strftime("%Y-%m-%d") if days else today


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="ths_hot.parquet")
    ap.add_argument("--date", default="", help="交易日 YYYY-MM-DD，缺省取最近交易日")
    args = ap.parse_args()

    d = args.date or latest_trade_date()
    df = tsc.fetch_ths_hot(d)
    # 即使为空也写出（让加载端决定跳过），保证 workflow 后续步骤有文件可传
    df.to_parquet(args.out, index=False)
    print(f"[fetch_ths_hot] {d} 主板人气榜 {len(df)} 行 -> {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
