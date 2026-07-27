"""模拟盘测试夹具：tmp_path 双库（market + paper）+ 确定性合成行情。

行情设计（全部 close=10 走平，便于精确断言）：
- sh.600000 浦发银行：主板；第 5 个交易日涨停（close 11.0, pctChg 10.0），之后走平
- sz.000001 平安银行：主板；第 5 个交易日跌停（close 9.0, pctChg -10.0），之后走平
- sh.600123 ST测试：主板 ST（±5%）；第 5 日涨停（close 10.5, pctChg 5.0），之后走平
- sz.300750 宁德时代：创业板（±20%）；第 6 日涨停（close 12.0, pctChg 20.0），之后走平
- sz.002594 停牌股：第 8 个交易日无行情行，其余走平
"""
import os
import sys

import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src import cache, db  # noqa: E402
from src.paper_trading import store  # noqa: E402

TRADE_DATES = pd.bdate_range("2025-01-02", periods=20)
LIMIT_DAY = str(TRADE_DATES[5].date())       # 涨停/跌停日
CHINEXT_LIMIT_DAY = str(TRADE_DATES[6].date())
SUSPEND_DAY = str(TRADE_DATES[8].date())     # 停牌日
DAY1 = str(TRADE_DATES[0].date())
DAY2 = str(TRADE_DATES[1].date())


def _rows(code: str, limit_day_close: float | None = None, limit_day_idx: int | None = None,
          skip_idx: int | None = None) -> pd.DataFrame:
    rows = []
    prev = 10.0
    for i, d in enumerate(TRADE_DATES):
        if skip_idx is not None and i == skip_idx:
            continue
        close = float(limit_day_close) if i == limit_day_idx else prev
        pct = round((close / prev - 1) * 100, 4) if i else 0.0
        rows.append({
            "code": code, "date": d, "open": close, "high": close, "low": close,
            "close": close, "volume": 1e5, "amount": 1e6,
            "pctChg": pct, "turn": 1.0, "adjustflag": "2",
        })
        prev = close
    return pd.DataFrame(rows)


def _synthetic_kline() -> pd.DataFrame:
    return pd.concat([
        _rows("sh.600000", limit_day_close=11.0, limit_day_idx=5),
        _rows("sz.000001", limit_day_close=9.0, limit_day_idx=5),
        _rows("sh.600123", limit_day_close=10.5, limit_day_idx=5),
        _rows("sz.300750", limit_day_close=12.0, limit_day_idx=6),
        _rows("sz.002594", skip_idx=8),
        _rows("sh.600999").head(5),   # 长期停牌：仅前 5 个交易日有行情
    ], ignore_index=True)


def _synthetic_meta() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["sh.600000", "sz.000001", "sh.600123", "sz.300750", "sz.002594", "sh.600999"],
        "code_name": ["浦发银行", "平安银行", "ST测试", "宁德时代", "停牌股份", "长期停牌"],
    })


@pytest.fixture
def paper_env(tmp_path, monkeypatch):
    """构建临时 market + paper 双库；禁用 Redis。返回 (market_path, paper_path)。"""
    market_path = str(tmp_path / "market.duckdb")
    with db.connect(read_only=False, path=market_path) as conn:
        db.init_schema(conn)
        db.upsert_kline(_synthetic_kline(), conn)
        db.upsert_meta(_synthetic_meta(), conn)

    paper_path = str(tmp_path / "paper.duckdb")
    with store.connect(path=paper_path) as conn:
        store.init_schema(conn)

    monkeypatch.setattr(db, "DUCKDB_PATH", market_path)
    monkeypatch.setenv("PAPER_DUCKDB_PATH", paper_path)
    monkeypatch.setattr(cache, "REDIS_ENABLED", False)
    monkeypatch.setattr(cache, "_redis_client", None)
    monkeypatch.setattr(cache, "_redis_available", False)
    return market_path, paper_path


def backdate_orders(paper_path: str, trade_date: str) -> None:
    """把全部委托/账户的 created_at 回拨到撮合日上午（模拟盘中提交/盘前建户）。"""
    with store.connect(path=paper_path) as conn:
        conn.execute(
            "UPDATE orders SET created_at = CAST(? AS TIMESTAMP)",
            [f"{trade_date} 10:00:00"],
        )
        conn.execute(
            "UPDATE accounts SET created_at = CAST(? AS TIMESTAMP)",
            [f"{trade_date} 09:00:00"],
        )
