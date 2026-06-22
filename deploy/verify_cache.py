"""验证 Redis 缓存读取"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"

from src.visualization.redis_cache import try_load, is_available

print(f"Redis: {is_available()}")

functions = [
    "load_latest_day",
    "load_equal_weighted_index",
    "load_limit_up_down",
    "load_ma_duration_samples",
    "load_name_map",
]

for fn in functions:
    t0 = time.time()
    kwargs = {}
    if fn == "load_equal_weighted_index":
        kwargs["relevant_params"] = {"start_date": "2025-01-01"}
    result, hit = try_load(fn, fallback_fn=lambda: None, **kwargs)
    elapsed = time.time() - t0
    size = ""
    if hasattr(result, '__len__'):
        size = f", {len(result)} rows"
    print(f"  {fn}: hit={hit}{size}, {elapsed*1000:.1f}ms")
