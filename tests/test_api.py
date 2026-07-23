"""FastAPI 端点测试（TestClient + 临时 DuckDB）。"""
import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_breadth(duck):
    r = client.get("/api/market/breadth")
    assert r.status_code == 200
    body = r.json()
    assert body["up"] + body["down"] + body["flat"] == 3
    assert body["latest_date"] is not None


def test_equal_weight_index(duck):
    r = client.get("/api/market/equal-weight-index")
    assert r.status_code == 200
    pts = r.json()
    assert isinstance(pts, list) and len(pts) > 0
    assert {"date", "value"} == set(pts[0].keys())


def test_search(duck):
    r = client.get("/api/stocks/search", params={"q": "银行"})
    assert r.status_code == 200
    assert {row["code"] for row in r.json()} == {"sh.600000", "sz.000001"}


def test_quotes(duck):
    r = client.get("/api/stocks/quotes", params={"codes": "sh.600000,sh.600519,sh.999999"})
    assert r.status_code == 200
    rows = r.json()
    assert {row["code"] for row in rows} == {"sh.600000", "sh.600519"}
    row = next(x for x in rows if x["code"] == "sh.600000")
    assert row["code_name"] == "浦发银行"
    assert row["close"] is not None and row["pctChg"] is not None and row["date"] is not None


def test_quotes_requires_codes(duck):
    assert client.get("/api/stocks/quotes").status_code == 422


def test_kline_with_ma(duck):
    r = client.get("/api/stocks/sh.600000/kline")
    assert r.status_code == 200
    body = r.json()
    assert body["code_name"] == "浦发银行"
    assert len(body["points"]) == 40
    assert "MA5" in body["points"][-1]


def test_kline_404(duck):
    assert client.get("/api/stocks/sh.999999/kline").status_code == 404


def test_volatility(duck):
    r = client.get("/api/stocks/sh.600000/volatility")
    assert r.status_code == 200
    assert len(r.json()) == 40


def test_rankings(duck):
    r = client.get("/api/rankings", params={"metric": "pctChg", "n": 2})
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_rankings_bad_metric(duck):
    assert client.get("/api/rankings", params={"metric": "bogus"}).status_code == 400


def test_ma_duration(duck):
    r = client.get("/api/ma-duration")
    assert r.status_code == 200
    assert "summary" in r.json() and "samples" in r.json()


def test_status(duck):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["n_codes"] == 3
    assert body["redis_available"] is False
