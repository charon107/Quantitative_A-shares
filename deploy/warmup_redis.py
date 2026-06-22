"""Redis 缓存预热：手动计算并存入 Redis。服务器上执行一次即可。"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"

from src.visualization.redis_cache import is_available, save
from src.visualization.metrics import load_all_latest_day, equal_weighted_index, limit_up_down_series
from src.analysis.ma5_above_ma10_duration import compute_duration_samples
import pandas as pd
from pathlib import Path

DATA_DIR = "股价数据_parquet_fq"

print(f"Redis 可用: {is_available()}")

# 1. load_latest_day
t0 = time.time()
df = load_all_latest_day(DATA_DIR)
save("load_latest_day", df, ttl=86400)
print(f"[1/5] load_latest_day: {len(df)} 条, {time.time()-t0:.1f}s")

# 2. load_equal_weighted_index
t0 = time.time()
s = equal_weighted_index(DATA_DIR, start_date="2025-01-01")
save("load_equal_weighted_index", s, relevant_params={"start_date": "2025-01-01"}, ttl=86400)
print(f"[2/5] equal_weighted_index(2025-01-01): {len(s)} rows, {time.time()-t0:.1f}s")

# 3. load_limit_up_down
t0 = time.time()
lud = limit_up_down_series(DATA_DIR)
save("load_limit_up_down", lud, ttl=86400)
print(f"[3/5] limit_up_down: {len(lud)} rows, {time.time()-t0:.1f}s")

# 4. load_ma_duration_samples
t0 = time.time()
ma = compute_duration_samples(DATA_DIR)
save("load_ma_duration_samples", ma, ttl=86400 * 7)
print(f"[4/5] ma_duration_samples: {len(ma)} rows, {time.time()-t0:.1f}s")

# 5. load_name_map
t0 = time.time()
path = Path(DATA_DIR) / "code_name_map.parquet"
if path.exists():
    df_map = pd.read_parquet(path)
    nm = dict(zip(df_map["code"], df_map["code_name"]))
    save("load_name_map", nm, ttl=86400)
    print(f"[5/5] name_map: {len(nm)} 条, {time.time()-t0:.1f}s")
else:
    print("[5/5] name_map: 文件不存在，跳过")

print("\n缓存预热完成！")
