"""Redis 缓存预热：手动计算并存入 Redis。服务器上数据刷新后执行一次。

缓存 key/参数与 src/api/services.py 一致（start_date 用 config.START_DATE），
确保 API 首屏直接命中。
用法：uv run python deploy/warmup_redis.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import cache, config, metrics  # noqa: E402

START = config.START_DATE

print(f"Redis 可用: {cache.is_available()}")
print(f"DuckDB: {metrics.db.DUCKDB_PATH}  start_date: {START}")

_step = 0
TOTAL = 9


def _log(name: str, size: int, t0: float) -> None:
    global _step
    _step += 1
    print(f"[{_step}/{TOTAL}] {name}: {size} 条, {time.time() - t0:.1f}s")


# 1. 全市场最新一日快照
t0 = time.time()
df = metrics.load_all_latest_day()
cache.save("load_latest_day", df, ttl=86400)
_log("load_latest_day", len(df), t0)

# 2. 全市场等权指数
t0 = time.time()
s = metrics.equal_weighted_index(START)
cache.save("load_equal_weighted_index", s, relevant_params={"start_date": START}, ttl=86400)
_log("equal_weighted_index", len(s), t0)

# 3. 上证主板等权指数
t0 = time.time()
sh = metrics.shanghai_equal_weighted_index(START)
cache.save("load_shanghai_equal_weighted_index", sh, relevant_params={"start_date": START}, ttl=86400)
_log("shanghai_equal_weighted_index", len(sh), t0)

# 4. 每日涨跌家数（市场宽度）
t0 = time.time()
bs = metrics.breadth_series()
cache.save("load_breadth_series", bs, ttl=86400)
_log("breadth_series", len(bs), t0)

# 5. 涨停/跌停家数序列（大盘概览，全表聚合）
t0 = time.time()
lud = metrics.limit_up_down_series()
cache.save("load_limit_up_down", lud, ttl=86400)
_log("limit_up_down", len(lud), t0)

# 6. MA 多头时长样本（最耗时，TTL 7 天）
t0 = time.time()
ma = metrics.ma_duration_samples()
cache.save("load_ma_duration_samples", ma, ttl=86400 * 7)
_log("ma_duration_samples", len(ma), t0)

# 7. 代码->名称映射
t0 = time.time()
nm = metrics.name_map()
cache.save("load_name_map", nm, ttl=86400)
_log("name_map", len(nm), t0)

# 8. 同花顺人气榜
t0 = time.time()
hot = metrics.hot_stocks(12)
cache.save("load_hot_stocks", hot, ttl=86400)
_log("hot_stocks", len(hot), t0)

# 9. 基本面选股：最新一年的选股结果（年份列表是 list，cache 层不支持，查询本身极轻）
t0 = time.time()
years = metrics.selection_years()
n_sel = 0
if years:
    latest = years[-1]
    sel = metrics.selected_stocks_by_year(latest)
    cache.save("load_selected_by_year", sel, relevant_params={"year": latest}, ttl=86400)
    n_sel = len(sel)
_log(f"selected_by_year({years[-1] if years else '-'})", n_sel, t0)

print("\n缓存预热完成！")
