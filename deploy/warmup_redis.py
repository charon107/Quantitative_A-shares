"""Redis 缓存预热：手动计算并存入 Redis。服务器上数据刷新后执行一次。

缓存 key 与 src/api/services.py 一致，确保 API 首屏直接命中。
用法：uv run python deploy/warmup_redis.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import cache, metrics  # noqa: E402

print(f"Redis 可用: {cache.is_available()}")
print(f"DuckDB: {metrics.db.DUCKDB_PATH}")

# 1. load_latest_day
t0 = time.time()
df = metrics.load_all_latest_day()
cache.save("load_latest_day", df, ttl=86400)
print(f"[1/5] load_latest_day: {len(df)} 条, {time.time()-t0:.1f}s")

# 2. load_equal_weighted_index
t0 = time.time()
s = metrics.equal_weighted_index("2025-01-01")
cache.save("load_equal_weighted_index", s, relevant_params={"start_date": "2025-01-01"}, ttl=86400)
print(f"[2/6] equal_weighted_index(2025-01-01): {len(s)} rows, {time.time()-t0:.1f}s")

# 3. load_shanghai_equal_weighted_index（上证主板等权）
t0 = time.time()
sh = metrics.shanghai_equal_weighted_index("2025-01-01")
cache.save("load_shanghai_equal_weighted_index", sh, relevant_params={"start_date": "2025-01-01"}, ttl=86400)
print(f"[3/6] shanghai_equal_weighted_index(2025-01-01): {len(sh)} rows, {time.time()-t0:.1f}s")

# 4. load_breadth_series（每日涨跌家数，含涨停/跌停）
t0 = time.time()
bs = metrics.breadth_series()
cache.save("load_breadth_series", bs, ttl=86400)
print(f"[4/6] breadth_series: {len(bs)} rows, {time.time()-t0:.1f}s")

# 5. load_ma_duration_samples（最耗时，TTL 7 天）
t0 = time.time()
ma = metrics.ma_duration_samples()
cache.save("load_ma_duration_samples", ma, ttl=86400 * 7)
print(f"[5/6] ma_duration_samples: {len(ma)} rows, {time.time()-t0:.1f}s")

# 6. load_name_map
t0 = time.time()
nm = metrics.name_map()
cache.save("load_name_map", nm, ttl=86400)
print(f"[6/6] name_map: {len(nm)} 条, {time.time()-t0:.1f}s")

print("\n缓存预热完成！")
