"""基本面选股接口端到端测试（在 duck 夹具基础上补 fundamental/selected/index）。"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src import db
from src.analysis import fundamental_screen as fs
from src.api.main import app

client = TestClient(app)


@pytest.fixture
def duck_fund(duck):
    """在合成 duck 库上补：sh.600519 近5年达标财务 + 选股池 + 上证指数（2025 年）。"""
    years = list(range(2020, 2025))  # 选股年 2025 的 5 年窗口 [2020..2024]
    fund = pd.DataFrame({
        "code": "sh.600519",
        "year": years,
        "ann_date": [f"{y + 1}-04-15" for y in years],
        "roe": [0.30] * 5,
        "netprofit_yoy": [0.20] * 5,
        "debt_ratio": [0.30] * 5,
        "net_profit": [100, 120, 150, 180, 210],
        "cfo": [90, 110, 140, 170, 200],
    })
    # 上证指数：复用 kline 日期区间（2025 年交易日）
    idx = pd.DataFrame({
        "code": "sh.000001",
        "date": pd.bdate_range("2025-01-02", periods=40),
        "close": [3000 + i for i in range(40)],
    })
    with db.connect(read_only=False, path=db.DUCKDB_PATH) as conn:
        db.upsert_fundamental(fund, conn)
        db.upsert_index_daily(idx, conn)
        panel = fs.panel_from_conn(conn)
        nm = {"sh.600519": "贵州茅台"}
        db.replace_selected_stocks(fs.select_pool(panel, nm, 2025, 2025), conn)
    return db.DUCKDB_PATH


def test_screening_years(duck_fund):
    r = client.get("/api/screening/years")
    assert r.status_code == 200
    assert 2025 in r.json()


def test_screening_by_year(duck_fund):
    r = client.get("/api/screening/2025")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["code"] == "sh.600519"
    assert row["code_name"] == "贵州茅台"
    assert row["roe"] == pytest.approx(0.30)  # 2024 年（窗口末年）指标
    assert row["debt_ratio"] == pytest.approx(0.30)


def test_screening_chart(duck_fund):
    r = client.get("/api/screening/2025/sh.600519/chart")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "sh.600519"
    assert len(body["stock"]) > 0
    assert len(body["index"]) > 0
    assert {"date", "close", "ma"} == set(body["stock"][0].keys())


def test_screening_chart_missing(duck_fund):
    r = client.get("/api/screening/2025/sz.999999/chart")
    assert r.status_code == 404
