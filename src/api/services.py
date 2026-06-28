"""服务层：在纯计算（src.metrics）外包一层 Redis 缓存（src.cache）。

缓存 key 与 deploy/warmup_redis.py 保持一致，便于预热命中。
重活（等权指数、MA 时长）TTL 更长。
"""
from __future__ import annotations

import pandas as pd

from src import cache, metrics

DEFAULT_START = "2025-01-01"


def latest_day() -> pd.DataFrame:
    res, _ = cache.try_load("load_latest_day", fallback_fn=metrics.load_all_latest_day, ttl=86400)
    return res if res is not None else pd.DataFrame()


def equal_weight_index(start_date: str = DEFAULT_START) -> pd.Series:
    res, _ = cache.try_load(
        "load_equal_weighted_index",
        relevant_params={"start_date": start_date},
        fallback_fn=lambda: metrics.equal_weighted_index(start_date),
        ttl=86400,
    )
    return res if res is not None else pd.Series(dtype=float)


def limit_up_down() -> pd.DataFrame:
    res, _ = cache.try_load("load_limit_up_down", fallback_fn=metrics.limit_up_down_series, ttl=86400)
    return res if res is not None else pd.DataFrame()


def ma_duration() -> pd.DataFrame:
    res, _ = cache.try_load("load_ma_duration_samples", fallback_fn=metrics.ma_duration_samples, ttl=86400 * 7)
    return res if res is not None else pd.DataFrame()


def name_map() -> dict:
    res, _ = cache.try_load("load_name_map", fallback_fn=metrics.name_map, ttl=86400)
    return res if res is not None else {}
