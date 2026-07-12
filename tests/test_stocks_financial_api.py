"""个股财务扩展接口端到端测试：季度基本面 / 估值日频 / 分红 / 业绩动态。

无数据时统一 200 + 空列表（前端整卡静默隐藏），不产生 404 噪音。
"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src import db
from src.api.main import app

client = TestClient(app)

CODE = "sh.600519"


@pytest.fixture
def duck_financial(duck):
    """在合成 duck 库上补：sh.600519 两年 8 个季度基本面 + 估值 + 分红 + 预告/快报。"""
    periods = [(y, q) for y in (2023, 2024) for q in (1, 2, 3, 4)]
    mmdd = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
    quarterly = pd.DataFrame({
        "code": CODE,
        "end_date": [f"{y}-{mmdd[q]}" for y, q in periods],
        "year": [y for y, _ in periods],
        "quarter": [q for _, q in periods],
        "ann_date": [f"{y}-{mmdd[q]}" for y, q in periods],
        "roe": [0.05 * q for _, q in periods],
        "net_profit": [1e8 * q for _, q in periods],
        "q_net_profit": [1e8] * 8,
        "cfo": [8e7 * q for _, q in periods],
        "total_liab": [3e10] * 8,
        "total_debt": [1.1e10] * 8,
        # q_roe 留空：验证 NULL 透传为 None
    })
    valuation = pd.DataFrame({
        "code": CODE,
        "date": pd.bdate_range("2025-01-02", periods=5),
        "pe_ttm": [30.0, 31.0, 32.0, 33.0, 34.0],
        "pb": [8.0] * 5,
        "total_mv": [2e8] * 5,
    })
    dividend = pd.DataFrame({
        "code": CODE, "end_date": ["2023-12-31", "2024-12-31"],
        "ann_date": ["2024-06-01", "2025-06-01"],
        "cash_div_tax": [25.91, 30.0], "ex_date": ["2024-06-18", "2025-06-18"],
    })
    forecast = pd.DataFrame({
        "code": CODE, "end_date": ["2024-12-31"], "ann_date": ["2025-01-20"],
        "type": ["预增"], "p_change_min": [0.10], "p_change_max": [0.20],
        "net_profit_min": [8e10], "net_profit_max": [9e10],
    })
    express = pd.DataFrame({
        "code": CODE, "end_date": ["2024-12-31"], "ann_date": ["2025-02-28"],
        "revenue": [1.7e11], "n_income": [8.5e10], "diluted_roe": [0.32],
        "yoy_dedu_np": [0.15],
    })
    with db.connect(read_only=False, path=db.DUCKDB_PATH) as conn:
        db.upsert_fundamental_quarterly(quarterly, conn)
        db.upsert_valuation_daily(valuation, conn)
        db.upsert_dividend(dividend, conn)
        db.upsert_forecast(forecast, conn)
        db.upsert_express(express, conn)
    return db.DUCKDB_PATH


def test_quarterly_fundamental(duck_financial):
    r = client.get(f"/api/stocks/{CODE}/fundamental/quarterly")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == CODE
    assert body["code_name"] == "贵州茅台"
    pts = body["points"]
    assert len(pts) == 8
    # 按 end_date 升序
    assert pts[0]["end_date"] == "2023-03-31"
    assert pts[-1]["end_date"] == "2024-12-31"
    assert pts[0]["year"] == 2023 and pts[0]["quarter"] == 1
    assert pts[3]["roe"] == pytest.approx(0.20)
    assert pts[0]["total_liab"] == pytest.approx(3e10)
    assert pts[0]["total_debt"] == pytest.approx(1.1e10)  # 有息口径（短借+长借+应付债券）
    # NULL 透传为 None（缺失指标不造数）
    assert pts[0]["q_roe"] is None
    assert pts[0]["eps"] is None


def test_quarterly_fundamental_empty(duck_financial):
    """无数据股票返回 200 + 空 points（前端隐藏面板）。"""
    r = client.get("/api/stocks/sh.600000/fundamental/quarterly")
    assert r.status_code == 200
    assert r.json()["points"] == []


def test_valuation(duck_financial):
    r = client.get(f"/api/stocks/{CODE}/valuation")
    assert r.status_code == 200
    pts = r.json()["points"]
    assert len(pts) == 5
    assert pts[0]["date"] == "2025-01-02"
    assert pts[0]["pe_ttm"] == pytest.approx(30.0)
    assert pts[0]["pe"] is None  # 未提供的列 NULL 透传


def test_dividend(duck_financial):
    r = client.get(f"/api/stocks/{CODE}/dividend")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert rows[0]["end_date"] == "2023-12-31"
    assert rows[0]["cash_div_tax"] == pytest.approx(25.91)
    assert rows[0]["ex_date"] == "2024-06-18"


def test_earnings(duck_financial):
    r = client.get(f"/api/stocks/{CODE}/earnings")
    assert r.status_code == 200
    body = r.json()
    assert len(body["forecasts"]) == 1
    assert body["forecasts"][0]["type"] == "预增"
    assert body["forecasts"][0]["p_change_max"] == pytest.approx(0.20)
    assert len(body["express"]) == 1
    assert body["express"][0]["diluted_roe"] == pytest.approx(0.32)


def test_earnings_empty(duck_financial):
    r = client.get("/api/stocks/sh.600000/earnings")
    assert r.status_code == 200
    body = r.json()
    assert body["forecasts"] == [] and body["express"] == []
