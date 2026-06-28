"""测试夹具：构建临时 DuckDB（合成数据），并把 src.db.DUCKDB_PATH 指向它、禁用 Redis。"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src import cache, db  # noqa: E402


def _synthetic_kline() -> pd.DataFrame:
    """3 只股票各 40 个交易日；sh.600000 第 10 日制造一个涨停。"""
    rng = np.random.default_rng(42)
    frames = []
    specs = [("sh.600000", 10.0, 10), ("sz.000001", 20.0, None), ("sh.600519", 50.0, None)]
    for code, base_px, jump in specs:
        n = 40
        dates = pd.bdate_range("2025-01-02", periods=n)
        px = [base_px]
        for i in range(1, n):
            step = rng.normal(0, 0.2)
            if jump and i == jump:
                step = base_px * 0.105  # +10.5% 涨停
            px.append(max(1.0, px[-1] + step))
        px = np.array(px)
        pct = np.concatenate([[0.0], (px[1:] / px[:-1] - 1) * 100])
        frames.append(pd.DataFrame({
            "date": dates, "code": code,
            "open": px, "high": px * 1.01, "low": px * 0.99, "close": px,
            "volume": rng.uniform(1e5, 1e6, n), "amount": rng.uniform(1e6, 1e7, n),
            "pctChg": pct, "turn": rng.uniform(0.5, 3, n), "adjustflag": "2",
        }))
    return pd.concat(frames, ignore_index=True)


def _synthetic_meta() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["sh.600000", "sz.000001", "sh.600519"],
        "code_name": ["浦发银行", "平安银行", "贵州茅台"],
    })


@pytest.fixture
def duck(tmp_path, monkeypatch):
    """构建临时 DuckDB 并指向它；禁用 Redis（走实时计算）。返回库路径。"""
    path = str(tmp_path / "market.duckdb")
    with db.connect(read_only=False, path=path) as conn:
        db.init_schema(conn)
        db.upsert_kline(_synthetic_kline(), conn)
        db.upsert_meta(_synthetic_meta(), conn)

    monkeypatch.setattr(db, "DUCKDB_PATH", path)
    monkeypatch.setattr(cache, "REDIS_ENABLED", False)
    monkeypatch.setattr(cache, "_redis_client", None)
    monkeypatch.setattr(cache, "_redis_available", False)
    return path
