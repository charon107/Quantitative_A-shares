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


def _valuation_chunk(code: str):
    dates = pd.bdate_range("2025-01-02", periods=5)
    return pd.DataFrame({
        "code": code, "date": dates,
        "pe": [15.0] * 5, "pe_ttm": [14.0] * 5, "pb": [1.5] * 5,
        "ps": [2.0] * 5, "ps_ttm": [1.9] * 5,
        "dv_ratio": [2.5] * 5, "dv_ttm": [2.4] * 5,
        "total_mv": [5e6] * 5, "circ_mv": [4e6] * 5,
    })


def test_persist_writes_valuation(tmp_path, monkeypatch):
    """persist 顺带写入估值日频（与换手率同一 daily_basic 响应）。"""
    monkeypatch.setattr(db, "DUCKDB_PATH", str(tmp_path / "market.duckdb"))
    stock_df = pd.DataFrame({"code": ["sh.600000"], "code_name": ["浦发银行"]})
    sp.persist(
        stock_df,
        {"sh.600000": [_raw_chunk("sh.600000")]},
        {"sh.600000": [_adj_chunk("sh.600000")]},
        valuation_rows_by_code={"sh.600000": [_valuation_chunk("sh.600000")]},
    )
    val = db.query_df("SELECT * FROM stock_valuation_daily ORDER BY date")
    assert len(val) == 5
    assert val["pe_ttm"].iloc[0] == pytest.approx(14.0)


def test_upsert_quarterly_idempotent_and_fills_missing_cols(tmp_path):
    """季度基本面 upsert：幂等（主键覆盖）+ 缺失列补 NULL。"""
    path = str(tmp_path / "m.duckdb")
    df = pd.DataFrame({
        "code": ["sh.600000"] * 2,
        "end_date": ["2023-03-31", "2023-06-30"],
        "year": [2023, 2023], "quarter": [1, 2],
        "roe": [0.10, 0.20], "q_net_profit": [100.0, 150.0],
    })
    with db.connect(read_only=False, path=path) as conn:
        db.init_schema(conn)
        assert db.upsert_fundamental_quarterly(df, conn) == 2
        assert db.upsert_fundamental_quarterly(df, conn) == 2  # 幂等
        rows = conn.execute(
            "SELECT COUNT(*), MAX(roe) FROM stock_fundamental_quarterly"
        ).fetchone()
        assert rows[0] == 2 and rows[1] == pytest.approx(0.20)
        # 未提供的列补 NULL
        assert conn.execute(
            "SELECT eps FROM stock_fundamental_quarterly WHERE quarter=1"
        ).fetchone()[0] is None


def test_delete_codes_covers_new_tables(tmp_path):
    """退市清理覆盖季度/估值/分红/预告/快报五张新表。"""
    path = str(tmp_path / "m.duckdb")
    code = "sh.600000"
    with db.connect(read_only=False, path=path) as conn:
        db.init_schema(conn)
        db.upsert_fundamental_quarterly(pd.DataFrame({
            "code": [code], "end_date": ["2023-03-31"], "year": [2023], "quarter": [1],
        }), conn)
        db.upsert_valuation_daily(_valuation_chunk(code), conn)
        db.upsert_dividend(pd.DataFrame({
            "code": [code], "end_date": ["2023-12-31"], "cash_div": [0.5],
        }), conn)
        db.upsert_forecast(pd.DataFrame({
            "code": [code], "end_date": ["2023-12-31"], "ann_date": ["2024-01-20"],
            "type": ["预增"],
        }), conn)
        db.upsert_express(pd.DataFrame({
            "code": [code], "end_date": ["2023-12-31"], "n_income": [1e8],
        }), conn)
        db.delete_codes([code], conn)
        for tbl in ("stock_fundamental_quarterly", "stock_valuation_daily",
                    "stock_dividend", "stock_forecast", "stock_express"):
            assert conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0] == 0
