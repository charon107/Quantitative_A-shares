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


def test_kline_pub_dates_from_annual_fundamental(duck_fund):
    """个股查询 K 线接口回传财报公布日（年报+季报并集，升序去重）。"""
    r = client.get("/api/stocks/sh.600519/kline")
    assert r.status_code == 200
    assert r.json()["pub_dates"] == [
        "2021-04-15", "2022-04-15", "2023-04-15", "2024-04-15", "2025-04-15",
    ]


def test_kline_pub_dates_includes_quarterly(duck_fund):
    """季报公布日也计入（stock_fundamental_quarterly 的 ann_date）。"""
    import pandas as pd

    q = pd.DataFrame({
        "code": "sh.600519",
        "end_date": ["2024-09-30", "2024-06-30"],
        "year": [2024, 2024],
        "quarter": [3, 2],
        "ann_date": ["2024-10-25", "2024-08-20"],
    })
    with db.connect(read_only=False, path=db.DUCKDB_PATH) as conn:
        db.upsert_fundamental_quarterly(q, conn)
    r = client.get("/api/stocks/sh.600519/kline")
    assert r.status_code == 200
    pub = r.json()["pub_dates"]
    # 年报日期 + 两份季报日期，升序去重
    assert "2024-08-20" in pub and "2024-10-25" in pub
    assert pub == sorted(set(pub))
