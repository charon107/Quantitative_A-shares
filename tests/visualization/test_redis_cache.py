"""
单元测试：redis_cache.py — Redis L2 缓存模块

覆盖：序列化往返、key 生成、Redis 不可用时回退。
"""
import os
import pytest
import pandas as pd
import numpy as np

# 强制禁用 Redis，避免测试环境依赖外部服务
os.environ["REDIS_ENABLED"] = "false"

from src.visualization.redis_cache import (
    _make_key,
    _serialize_df,
    _deserialize_df,
    _serialize_series,
    _deserialize_series,
    _serialize_dict,
    _deserialize_dict,
    _serialize,
    _deserialize,
    try_load,
    save,
    invalidate_all,
    invalidate_func,
    is_available,
    CACHE_VERSION,
)


class TestKeyGeneration:
    """key 生成：确定性与隔离性"""

    def test_same_params_same_key(self):
        k1 = _make_key("load_latest_day")
        k2 = _make_key("load_latest_day")
        assert k1 == k2

    def test_different_func_different_key(self):
        k1 = _make_key("load_latest_day")
        k2 = _make_key("load_limit_up_down")
        assert k1 != k2

    def test_params_affect_key(self):
        k1 = _make_key("load_equal_weighted_index",
                        {"start_date": "2025-01-01"})
        k2 = _make_key("load_equal_weighted_index",
                        {"start_date": "2024-01-01"})
        assert k1 != k2

    def test_key_has_version_prefix(self):
        key = _make_key("load_latest_day")
        assert key.startswith(f"{CACHE_VERSION}:")
        # 格式：v1:func_name:16hex
        parts = key.split(":")
        assert len(parts) == 3
        assert len(parts[2]) == 16


class TestDataFrameSerialization:
    """DataFrame ↔ parquet bytes 往返"""

    def test_round_trip_ohlcv(self):
        df = pd.DataFrame({
            "date": ["2025-06-19", "2025-06-20"],
            "code": ["sh.601988"] * 2,
            "open": [10.4, 10.6],
            "close": [10.6, 10.9],
            "volume": [1_400_000, 1_600_000],
            "pctChg": [0.9, 1.2],
        })
        data = _serialize_df(df)
        result = _deserialize_df(data)
        pd.testing.assert_frame_equal(result, df)

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        data = _serialize_df(df)
        result = _deserialize_df(data)
        assert result.empty

    def test_dataframe_with_nan(self):
        df = pd.DataFrame({
            "a": [1.0, np.nan, 3.0],
            "b": ["x", None, "z"],
        })
        data = _serialize_df(df)
        result = _deserialize_df(data)
        pd.testing.assert_frame_equal(result, df)


class TestSeriesSerialization:
    """Series ↔ parquet bytes 往返"""

    def test_round_trip(self):
        s = pd.Series([1.0, 2.0, 3.0], name="value",
                       index=pd.DatetimeIndex(["2025-01-01", "2025-01-02", "2025-01-03"]))
        data = _serialize_series(s)
        result = _deserialize_series(data)
        pd.testing.assert_series_equal(result, s)

    def test_empty_series(self):
        s = pd.Series(dtype=float)
        data = _serialize_series(s)
        result = _deserialize_series(data)
        assert result.empty


class TestDictSerialization:
    """dict ↔ JSON 往返"""

    def test_round_trip(self):
        d = {"sh.601988": "中国银行", "sz.000001": "平安银行"}
        data = _serialize_dict(d)
        result = _deserialize_dict(data)
        assert result == d

    def test_empty_dict(self):
        data = _serialize_dict({})
        result = _deserialize_dict(data)
        assert result == {}

    def test_unicode(self):
        d = {"sh.600519": "贵州茅台", "sz.000858": "五粮液"}
        data = _serialize_dict(d)
        result = _deserialize_dict(data)
        assert result == d


class TestTypeDispatch:
    """_serialize / _deserialize 类型分发"""

    def test_serialize_dataframe(self):
        df = pd.DataFrame({"x": [1, 2]})
        data, typ = _serialize(df)
        assert typ == "DataFrame"
        assert _deserialize(data, typ).equals(df)

    def test_serialize_series(self):
        s = pd.Series([1, 2])
        data, typ = _serialize(s)
        assert typ == "Series"
        # 往返后 name 变为 "value"（序列化时 to_frame("value")），值等价即可
        result = _deserialize(data, typ)
        pd.testing.assert_series_equal(result, s, check_names=False)

    def test_serialize_dict(self):
        d = {"a": 1}
        data, typ = _serialize(d)
        assert typ == "dict"
        assert _deserialize(data, typ) == d

    def test_unsupported_type(self):
        data, typ = _serialize([1, 2, 3])  # list not supported
        assert data is None
        assert typ is None


class TestRedisDisabledFallback:
    """Redis 禁用时：所有公共 API 应安全回退，不抛异常"""

    def test_is_available_false(self):
        assert not is_available()

    def test_try_load_falls_back(self):
        called = []
        def fallback():
            called.append(1)
            return pd.DataFrame({"x": [1]})
        result, hit = try_load("test_func", fallback_fn=fallback)
        assert not hit
        assert len(called) == 1
        assert result.equals(pd.DataFrame({"x": [1]}))

    def test_try_load_returns_none_without_fallback(self):
        result, hit = try_load("nonexistent")
        assert not hit
        assert result is None

    def test_save_returns_false(self):
        df = pd.DataFrame({"x": [1]})
        assert not save("test_func", df)

    def test_invalidate_all_returns_zero(self):
        assert invalidate_all() == 0

    def test_invalidate_func_returns_zero(self):
        assert invalidate_func("test_func") == 0

    def test_empty_dataframe_not_cached(self):
        """空 DataFrame 不应写入 Redis（即使 Redis 可用也不写）"""
        called = []
        def fallback():
            called.append(1)
            return pd.DataFrame()
        result, hit = try_load("test_func", fallback_fn=fallback)
        assert not hit
        assert len(called) == 1
        assert result.empty
