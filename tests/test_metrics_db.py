"""src/metrics.py（DuckDB 版）单元测试。"""
import numpy as np
import pandas as pd
import pytest

from src import metrics


def test_load_all_latest_day_one_row_per_code(duck):
    df = metrics.load_all_latest_day()
    assert len(df) == 3
    assert set(df["code"]) == {"sh.600000", "sz.000001", "sh.600519"}
    # 每只只保留最新一日
    assert df["code"].is_unique


def test_market_breadth_counts(duck):
    df = metrics.load_all_latest_day()
    b = metrics.market_breadth(df)
    assert b["up"] + b["down"] + b["flat"] == 3


def test_market_breadth_empty():
    b = metrics.market_breadth(pd.DataFrame())
    assert b == {"up": 0, "down": 0, "flat": 0, "ratio": pytest.approx(np.nan, nan_ok=True)} or np.isnan(b["ratio"])


def test_equal_weighted_index_cumulative(duck):
    s = metrics.equal_weighted_index("2025-01-01")
    assert isinstance(s, pd.Series)
    assert len(s) > 0
    # 累计收益是单调累乘结果，首值应接近首日等权收益
    assert s.notna().all()


def test_limit_up_down_detects_injected_limit(duck):
    df = metrics.limit_up_down_series()
    assert "limit_up" in df.columns and "limit_down" in df.columns
    # 合成数据里注入了恰好 1 个涨停
    assert int(df["limit_up"].sum()) == 1


def test_load_stock_kline_sorted(duck):
    df = metrics.load_stock_kline("sh.600000")
    assert len(df) == 40
    assert df["date"].is_monotonic_increasing


def test_load_stock_kline_missing_raises(duck):
    with pytest.raises(LookupError):
        metrics.load_stock_kline("sh.999999")


def test_add_moving_averages(duck):
    df = metrics.add_moving_averages(metrics.load_stock_kline("sh.600000"))
    assert {"MA5", "MA10", "MA20", "MA60"}.issubset(df.columns)
    assert df["MA60"].isna().all()  # 数据不足 60 日
    assert df["MA5"].iloc[-1] == pytest.approx(df["close"].iloc[-5:].mean())


def test_volatility_frame_has_dates(duck):
    f = metrics.volatility_frame("sh.600000", window=20)
    assert list(f.columns) == ["date", "value"]
    assert len(f) == 40


def test_top_movers(duck):
    df = metrics.load_all_latest_day()
    top = metrics.top_movers(df, n=2, metric="pctChg")
    assert len(top) == 2
    assert top["pctChg"].iloc[0] >= top["pctChg"].iloc[1]


def test_name_map_and_search(duck):
    nm = metrics.name_map()
    assert nm["sh.600000"] == "浦发银行"
    by_name = metrics.search_stocks("银行")
    assert set(by_name["code"]) == {"sh.600000", "sz.000001"}
    by_code = metrics.search_stocks("sh.600")
    assert set(by_code["code"]) == {"sh.600000", "sh.600519"}


def test_ma_duration_samples_shape(duck):
    df = metrics.ma_duration_samples("2025-01-01")
    assert set(df.columns) == {"code", "start_date", "end_date", "duration", "ongoing"}


def test_data_status(duck):
    st = metrics.data_status()
    assert st["n_codes"] == 3
    assert st["n_rows"] == 120
    assert st["latest_date"] is not None
