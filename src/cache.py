"""Redis L2 持久化缓存 — 为 FastAPI 看板的重计算结果提供跨重启持久层。

设计：
  - 计算层（src/metrics.py）产出 DataFrame/Series/dict
  - 本模块把结果以 parquet bytes / JSON 序列化进 Redis，TTL 控制
  - Redis 不可用时优雅降级：所有操作静默跳过，回退到实时计算

用法示例：
    from src import cache, metrics

    def load_latest_day():
        def compute():
            return metrics.load_all_latest_day()
        result, _hit = cache.try_load("load_latest_day", fallback_fn=compute, ttl=86400)
        return result

缓存失效（数据刷新后）：
    python -m src.cache clear
"""
from __future__ import annotations

import os
import json
import hashlib
import logging
from datetime import datetime
from typing import Any, Callable

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# ========== 配置 ==========
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
REDIS_ENABLED = os.environ.get("REDIS_ENABLED", "true").lower() not in ("false", "0", "no")
CACHE_VERSION = "v1"          # 递增可强制全局失效
DEFAULT_TTL = 86400 * 7       # 7 天（安全网；实际依赖显式失效）


# ========== 延迟连接 ==========
_redis_client = None       # None = 尚未尝试连接
_redis_available = False   # True = 已通过 ping 验证


def _get_redis():
    """延迟连接 Redis。返回客户端或 None（不可用时）。"""
    global _redis_client, _redis_available
    if _redis_client is not None:
        return _redis_client if _redis_available else None
    if not REDIS_ENABLED:
        return None
    try:
        import redis
        _redis_client = redis.Redis.from_url(
            REDIS_URL,
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=False,  # 二进制数据
        )
        _redis_client.ping()
        _redis_available = True
        logger.info("Redis L2 缓存已连接: %s", REDIS_URL)
    except Exception as exc:
        _redis_available = False
        _redis_client = None
        logger.warning("Redis 不可用 (%s)，回退到实时计算", exc)
    return _redis_client if _redis_available else None


def is_available() -> bool:
    """检查 Redis L2 缓存是否可用。用于数据状态接口展示。"""
    return _get_redis() is not None


def get_redis():
    """暴露 Redis 客户端给其他模块（如最近查看持久化）。返回 None 表示不可用。"""
    return _get_redis()


# ========== Key 生成 ==========
def _make_key(func_name: str, params: dict[str, object] | None = None) -> str:
    """构建确定性的 Redis key。仅 func_name + 指定 params 参与哈希。"""
    payload = {"func": func_name}
    if params:
        payload["params"] = {k: str(v) for k, v in sorted(params.items())}
    canonical = json.dumps(payload, sort_keys=True)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return f"{CACHE_VERSION}:{func_name}:{digest}"


# ========== 序列化 ==========
def _serialize_df(df: pd.DataFrame) -> bytes:
    table = pa.Table.from_pandas(df)
    buf = pa.BufferOutputStream()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue().to_pybytes()


def _deserialize_df(data: bytes) -> pd.DataFrame:
    reader = pa.BufferReader(data)
    table = pq.read_table(reader)
    return table.to_pandas()


def _serialize_series(s: pd.Series) -> bytes:
    return _serialize_df(s.to_frame("value"))


def _deserialize_series(data: bytes) -> pd.Series:
    df = _deserialize_df(data)
    return df["value"]


def _serialize_dict(d: dict) -> bytes:
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


def _deserialize_dict(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


def _serialize(value: Any) -> tuple[bytes, str] | tuple[None, None]:
    if isinstance(value, pd.DataFrame):
        return _serialize_df(value), "DataFrame"
    if isinstance(value, pd.Series):
        return _serialize_series(value), "Series"
    if isinstance(value, dict):
        return _serialize_dict(value), "dict"
    return None, None


def _deserialize(data: bytes, typ: str) -> Any:
    if typ == "DataFrame":
        return _deserialize_df(data)
    if typ == "Series":
        return _deserialize_series(data)
    if typ == "dict":
        return _deserialize_dict(data)
    return None


# ========== 存储 / 读取 ==========
def _store(key: str, value: Any, ttl: int) -> bool:
    r = _get_redis()
    if r is None:
        return False
    data, typ = _serialize(value)
    if data is None:
        return False
    meta = json.dumps({"type": typ, "created": datetime.now().isoformat(), "ttl": ttl})
    try:
        pipe = r.pipeline()
        pipe.set(key, data, ex=ttl)
        pipe.set(f"{key}:meta", meta, ex=ttl)
        pipe.execute()
        return True
    except Exception as exc:
        logger.debug("Redis 写入失败 (%s): %s", key, exc)
        return False


def _retrieve(key: str) -> tuple[Any, dict | None]:
    r = _get_redis()
    if r is None:
        return None, None
    try:
        meta_raw = r.get(f"{key}:meta")
        if meta_raw is None:
            return None, None
        meta = json.loads(meta_raw.decode("utf-8"))
        data = r.get(key)
        if data is None:
            return None, None
        return _deserialize(data, meta["type"]), meta
    except Exception as exc:
        logger.debug("Redis 读取失败 (%s): %s", key, exc)
        return None, None


# ========== 公共 API ==========
def try_load(
    func_name: str,
    *,
    relevant_params: dict[str, object] | None = None,
    fallback_fn: Callable[[], Any] | None = None,
    ttl: int = DEFAULT_TTL,
) -> tuple[Any, bool]:
    """读穿透：Redis 命中返回缓存值；miss 调用 fallback_fn 计算并存 Redis。

    返回 (value, hit)，hit=True 表示来自 Redis 缓存。
    """
    key = _make_key(func_name, relevant_params)
    value, _meta = _retrieve(key)
    if value is not None:
        return value, True
    if fallback_fn is not None:
        computed = fallback_fn()
        if computed is not None and (not isinstance(computed, pd.DataFrame) or not computed.empty):
            _store(key, computed, ttl)
        return computed, False
    return None, False


def save(
    func_name: str,
    value: Any,
    *,
    relevant_params: dict[str, object] | None = None,
    ttl: int = DEFAULT_TTL,
) -> bool:
    """显式将值写入 Redis 缓存。"""
    key = _make_key(func_name, relevant_params)
    return _store(key, value, ttl)


def invalidate_all() -> int:
    """清空当前版本的所有 Redis 缓存 key（SCAN + 批量删除）。返回删除的 key 数量。"""
    r = _get_redis()
    if r is None:
        return 0
    pattern = f"{CACHE_VERSION}:*"
    deleted = 0
    try:
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=100)
            if keys:
                meta_keys = [f"{k}:meta" for k in keys]
                r.delete(*(list(keys) + meta_keys))
                deleted += len(keys)
            if cursor == 0:
                break
        logger.info("Redis 缓存已清空: %d 个 key", deleted)
    except Exception as exc:
        logger.warning("Redis 清空失败: %s", exc)
    return deleted


def invalidate_func(func_name: str) -> int:
    """清空指定函数的所有缓存 key。返回删除数。"""
    r = _get_redis()
    if r is None:
        return 0
    pattern = f"{CACHE_VERSION}:{func_name}:*"
    deleted = 0
    try:
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=100)
            if keys:
                meta_keys = [f"{k}:meta" for k in keys]
                r.delete(*(list(keys) + meta_keys))
                deleted += len(keys)
            if cursor == 0:
                break
    except Exception as exc:
        logger.warning("Redis 清空 %s 失败: %s", func_name, exc)
    return deleted


# ========== 直接执行：健康检查 / 清缓存 ==========
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "clear":
        n = invalidate_all()
        print(f"已清空 {n} 个缓存 key")
    else:
        print(f"Redis URL: {REDIS_URL}")
        print(f"Redis 启用: {REDIS_ENABLED}")
        if is_available():
            print("Redis 连接正常")
            print(f"   缓存版本: {CACHE_VERSION}")
            print(f"   默认 TTL: {DEFAULT_TTL}s ({DEFAULT_TTL // 86400}d)")
        else:
            print("Redis 不可用（将回退到实时计算）")
