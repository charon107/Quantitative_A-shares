"""load_all_parquet：qfq 增量追加 vs 除权全量重算。不触网。"""
import importlib
import os

import pandas as pd
import pytest

from src import db

load_all = importlib.import_module("scripts.load_all_parquet")


def _raw_df(code: str, dates, closes):
    return pd.DataFrame({
        "code": code, "date": pd.to_datetime(dates),
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1e6] * len(dates), "amount": [1e7] * len(dates),
        "pctChg": [0.0] * len(dates), "turn": [1.5] * len(dates),
    })


def _adj_df(code: str, dates, factors):
    return pd.DataFrame({"code": code, "trade_date": pd.to_datetime(dates), "adj_factor": factors})


def _write_ingest(dirpath, raw: pd.DataFrame, adj: pd.DataFrame) -> str:
    os.makedirs(dirpath, exist_ok=True)
    raw.to_parquet(os.path.join(dirpath, "raw_recent.parquet"))
    adj.to_parquet(os.path.join(dirpath, "adj_recent.parquet"))
    return str(dirpath)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DUCKDB_PATH", str(tmp_path / "market.duckdb"))
    monkeypatch.setattr(load_all, "STATE_PATH", str(tmp_path / "ingest_state.json"))
    return tmp_path


def _seed(env, capsys):
    """首灌 5 天，factor=1.0，qfq == raw close。"""
    dates = [f"2025-01-0{d}" for d in (2, 3, 6, 7, 8)]
    closes = [10.0, 11.0, 10.5, 12.0, 13.0]
    d = _write_ingest(env / "seed", _raw_df("sh.600000", dates, closes), _adj_df("sh.600000", dates, [1.0] * 5))
    load_all.main(d)
    capsys.readouterr()
    return dates, closes


def test_factor_unchanged_appends_incrementally(env, capsys):
    _seed(env, capsys)

    # 新增 1 天，因子不变 → 走增量追加，历史 qfq 不动
    d = _write_ingest(env / "day2", _raw_df("sh.600000", ["2025-01-09"], [14.0]), _adj_df("sh.600000", ["2025-01-09"], [1.0]))
    load_all.main(d)

    out = capsys.readouterr().out
    assert "全量重算 0 只 + 增量追加 1 只" in out

    kl = db.query_df("SELECT date, close FROM kline WHERE code='sh.600000' ORDER BY date")
    assert len(kl) == 6
    assert kl["close"].tolist() == pytest.approx([10.0, 11.0, 10.5, 12.0, 13.0, 14.0])


def test_factor_changed_recomputes_full_history(env, capsys):
    _seed(env, capsys)

    # 新增 1 天，因子 1.0 -> 2.0（除权）→ 全量重算，历史 qfq 减半
    d = _write_ingest(env / "day2", _raw_df("sh.600000", ["2025-01-09"], [7.0]), _adj_df("sh.600000", ["2025-01-09"], [2.0]))
    load_all.main(d)

    out = capsys.readouterr().out
    assert "全量重算 1 只 + 增量追加 0 只" in out

    kl = db.query_df("SELECT date, close FROM kline WHERE code='sh.600000' ORDER BY date")
    assert len(kl) == 6
    assert kl["close"].tolist() == pytest.approx([5.0, 5.5, 5.25, 6.0, 6.5, 7.0])


def test_new_listing_gets_full_compute(env, capsys):
    _seed(env, capsys)

    # 全新上市股票（kline/adj 均无历史）→ 全量路径
    d = _write_ingest(env / "day2", _raw_df("sz.301999", ["2025-01-09"], [20.0]), _adj_df("sz.301999", ["2025-01-09"], [1.0]))
    load_all.main(d)

    out = capsys.readouterr().out
    assert "全量重算 1 只 + 增量追加 0 只" in out
    kl = db.query_df("SELECT close FROM kline WHERE code='sz.301999'")
    assert kl["close"].tolist() == pytest.approx([20.0])
