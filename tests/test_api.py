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


def test_limit_up_down(duck):
    r = client.get("/api/market/limit-up-down")
    assert r.status_code == 200
    assert sum(p["limit_up"] for p in r.json()) == 1


def test_search(duck):
    r = client.get("/api/stocks/search", params={"q": "银行"})
    assert r.status_code == 200
    assert {row["code"] for row in r.json()} == {"sh.600000", "sz.000001"}


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
