"""每日模拟盘撮合：在行情入库（load_all_parquet.py）完成后执行。

用法：
    uv run python scripts/paper_match_daily.py [YYYY-MM-DD]

- 不指定日期时取行情库最近交易日；
- 对全部「待成交」委托按收盘价批量撮合并生成当日净值快照；
- 结束后全量清 Redis 缓存（沿用入库后的既有惯例）；
- 幂等：同一交易日重跑只处理仍为 pending 的委托，不会重复成交；
- paper.duckdb 不存在时自动建库。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.paper_trading.matcher import run_daily_match  # noqa: E402


def main() -> None:
    trade_date = sys.argv[1] if len(sys.argv) > 1 else None
    result = run_daily_match(trade_date)
    print(
        f"[paper_match] {result.trade_date} "
        f"成交 {result.filled} 笔，过期 {result.expired} 笔，"
        f"拒绝 {result.rejected} 笔，顺延 {result.skipped} 笔"
    )


if __name__ == "__main__":
    main()
