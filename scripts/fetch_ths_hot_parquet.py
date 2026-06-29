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


def recent_trade_dates(n: int = 6) -> list[str]:
    """最近 n 个交易日，从新到旧。"""
    today = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=25)).strftime("%Y-%m-%d")
    days = [d.strftime("%Y-%m-%d") for d in tsc.fetch_trade_dates(start, today)]
    return list(reversed(days))[:n] if days else [today]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="ths_hot.parquet")
    ap.add_argument("--date", default="", help="交易日 YYYY-MM-DD，缺省取最近有数据的交易日")
    args = ap.parse_args()

    candidates = [args.date] if args.date else recent_trade_dates()
    # 从新到旧，取第一个有数据的交易日（今日热榜未发布时回退到上一交易日）
    import pandas as pd
    df = pd.DataFrame()
    used = candidates[0] if candidates else ""
    for d in candidates:
        df = tsc.fetch_ths_hot(d)
        if not df.empty:
            used = d
            break

    # 即使最终为空也写出（加载端会跳过、保留旧数据）
    df.to_parquet(args.out, index=False)
    print(f"[fetch_ths_hot] {used} 主板人气榜 {len(df)} 行 -> {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
