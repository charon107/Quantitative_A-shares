"""入库持久化（DuckDB）测试：raw/adj upsert + qfq 重算 + 原子替换。不触网。"""
import pandas as pd
import pytest

from src import db
from src.data_collection import stock_price as sp


def _raw_chunk(code: str):
    dates = pd.bdate_range("2025-01-02", periods=5)
    close = [10.0, 11.0, 10.5, 12.0, 13.0]
    return pd.DataFrame({
        "code": code, "date": dates,
        "open": close, "high": [c * 1.01 for c in close],
        "low": [c * 0.99 for c in close], "close": close,
        "volume": [1e6] * 5, "amount": [1e7] * 5,
        "pctChg": [0.0, 10.0, -4.5, 14.3, 8.3], "turn": [1.5] * 5,
    })


def _adj_chunk(code: str):
    dates = pd.bdate_range("2025-01-02", periods=5)
    return pd.DataFrame({"code": code, "trade_date": dates, "adj_factor": [1.0] * 5})


def test_persist_builds_kline(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DUCKDB_PATH", str(tmp_path / "market.duckdb"))

    stock_df = pd.DataFrame({"code": ["sh.600000", "sz.000001"], "code_name": ["浦发银行", "平安银行"]})
    raw_by = {"sh.600000": [_raw_chunk("sh.600000")], "sz.000001": [_raw_chunk("sz.000001")]}
    adj_by = {"sh.600000": [_adj_chunk("sh.600000")], "sz.000001": [_adj_chunk("sz.000001")]}

    stats = sp.persist(stock_df, raw_by, adj_by)
    assert stats["UPDATED"] == 2
    assert stats["EMPTY"] == 0

    # kline 表已建好，adj_factor=1 时 qfq close == 原始 close
    kl = db.query_df("SELECT code, date, close FROM kline ORDER BY code, date")
    assert len(kl) == 10
    assert kl["close"].iloc[-1] == pytest.approx(13.0)

    meta = db.query_df("SELECT * FROM stock_meta ORDER BY code")
    assert meta["code_name"].tolist() == ["浦发银行", "平安银行"]


def test_incremental_upsert_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DUCKDB_PATH", str(tmp_path / "market.duckdb"))
    stock_df = pd.DataFrame({"code": ["sh.600000"], "code_name": ["浦发银行"]})

    sp.persist(stock_df, {"sh.600000": [_raw_chunk("sh.600000")]}, {"sh.600000": [_adj_chunk("sh.600000")]})

    # 追加新的一天
    new_raw = pd.DataFrame({
        "code": "sh.600000", "date": pd.bdate_range("2025-01-09", periods=1),
        "open": [14.0], "high": [14.5], "low": [13.5], "close": [14.0],
        "volume": [1e6], "amount": [1e7], "pctChg": [7.7], "turn": [1.2],
    })
    new_adj = pd.DataFrame({"code": "sh.600000", "trade_date": pd.bdate_range("2025-01-09", periods=1), "adj_factor": [1.0]})
    sp.persist(stock_df, {"sh.600000": [new_raw]}, {"sh.600000": [new_adj]})

    kl = db.query_df("SELECT date, close FROM kline WHERE code='sh.600000' ORDER BY date")
    assert len(kl) == 6  # 5 + 1
    assert kl["close"].iloc[-1] == pytest.approx(14.0)


def test_existing_raw_codes_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DUCKDB_PATH", str(tmp_path / "market.duckdb"))
    assert sp.existing_raw_codes() == set()  # 库不存在
    sp.persist(
        pd.DataFrame({"code": ["sh.600000"], "code_name": ["浦发银行"]}),
        {"sh.600000": [_raw_chunk("sh.600000")]},
        {"sh.600000": [_adj_chunk("sh.600000")]},
    )
    assert sp.existing_raw_codes() == {"sh.600000"}
