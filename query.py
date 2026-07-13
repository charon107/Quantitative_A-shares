"""远程查询服务器 DuckDB 的命令行入口（只读 SQL 网关客户端，接口文档见 API.md）。

前置：仓库根 .env 配置 SQL_API_TOKEN（与服务器一致）。

用法：
    uv run python query.py "SELECT COUNT(*) AS n FROM kline"
    uv run python query.py "SELECT * FROM stock_fundamental_quarterly WHERE code='sh.600660'" --csv fuyao.csv
    uv run python query.py "SELECT * FROM stock_valuation_daily WHERE code='sh.600519'" --parquet maotai_val.parquet

在 Python / Jupyter 里用：
    from query import query_df
    df = query_df("SELECT * FROM stock_dividend WHERE code='sh.600660'")
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.remote_query import query_df  # noqa: E402,F401  re-export 供 import 使用


def main() -> None:
    ap = argparse.ArgumentParser(description="远程查询服务器 DuckDB（只读）")
    ap.add_argument("sql", help='SQL 语句，如 "SELECT * FROM stock_fundamental WHERE year=2025 LIMIT 10"')
    ap.add_argument("--csv", metavar="FILE", help="结果另存为 CSV（UTF-8 带 BOM，Excel 可直接打开）")
    ap.add_argument("--parquet", metavar="FILE", help="结果另存为 Parquet")
    ap.add_argument("--url", default=None, help="服务地址（默认取 WECHATNUM_API_URL 或生产服务器）")
    args = ap.parse_args()

    df = query_df(args.sql, url=args.url)

    if args.csv:
        df.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"已保存 {len(df)} 行 -> {args.csv}", file=sys.stderr)
    if args.parquet:
        df.to_parquet(args.parquet, index=False)
        print(f"已保存 {len(df)} 行 -> {args.parquet}", file=sys.stderr)
    if not args.csv and not args.parquet:
        with pd.option_context("display.max_columns", None, "display.width", 200, "display.max_rows", 100):
            print(df)
        print(f"[{len(df)} 行 × {len(df.columns)} 列]", file=sys.stderr)


if __name__ == "__main__":
    main()
