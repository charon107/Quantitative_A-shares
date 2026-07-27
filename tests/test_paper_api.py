"""模拟盘 API 测试：TestClient 覆盖全路由 + request_id 幂等 + 撤单/重置。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


@pytest.fixture
def account(tmp_path, monkeypatch, duck):
    """duck fixture（根 conftest）建好临时行情库后，再指向临时 paper 库并建账户。"""
    monkeypatch.setenv("PAPER_DUCKDB_PATH", str(tmp_path / "paper.duckdb"))
    resp = client.post("/api/paper/accounts", json={"name": "API测试", "init_cash": 1_000_000})
    assert resp.status_code == 201, resp.text
    return resp.json()["account_id"]


def test_create_account_and_overview(account):
    ov = client.get(f"/api/paper/accounts/{account}/overview")
    assert ov.status_code == 200
    body = ov.json()
    assert body["cash"] == 1_000_000.0 and body["total_asset"] == 1_000_000.0
    assert body["position_count"] == 0


def test_overview_unknown_account_404(account):
    assert client.get("/api/paper/accounts/nope/overview").status_code == 404


def test_place_order_and_idempotency(account):
    rid = uuid.uuid4().hex
    payload = {"request_id": rid, "code": "sh.600000", "side": "buy",
               "price_type": "market", "qty": 1000}
    r1 = client.post(f"/api/paper/accounts/{account}/orders", json=payload)
    assert r1.status_code == 201, r1.text
    r2 = client.post(f"/api/paper/accounts/{account}/orders", json=payload)
    assert r2.status_code == 201
    assert r1.json()["order_id"] == r2.json()["order_id"]

    orders = client.get(f"/api/paper/accounts/{account}/orders").json()
    assert orders["total"] == 1
    assert orders["items"][0]["status"] == "pending"
    assert orders["items"][0]["code_name"] == "浦发银行"

    pending = client.get(f"/api/paper/accounts/{account}/orders?status=pending").json()
    assert pending["total"] == 1


def test_place_order_validation_errors(account):
    base = f"/api/paper/accounts/{account}/orders"
    bad_qty = {"request_id": uuid.uuid4().hex, "code": "sh.600000", "side": "buy",
               "price_type": "market", "qty": 150}
    assert client.post(base, json=bad_qty).status_code == 400
    no_limit = {"request_id": uuid.uuid4().hex, "code": "sh.600000", "side": "buy",
                "price_type": "limit", "qty": 100}
    assert client.post(base, json=no_limit).status_code == 400
    bad_code = {"request_id": uuid.uuid4().hex, "code": "bj.830799", "side": "buy",
                "price_type": "market", "qty": 100}
    assert client.post(base, json=bad_code).status_code == 400
    oversell = {"request_id": uuid.uuid4().hex, "code": "sh.600000", "side": "sell",
                "price_type": "market", "qty": 100}
    assert client.post(base, json=oversell).status_code == 400


def test_cancel_order(account):
    payload = {"request_id": uuid.uuid4().hex, "code": "sh.600000", "side": "buy",
               "price_type": "market", "qty": 1000}
    order_id = client.post(
        f"/api/paper/accounts/{account}/orders", json=payload).json()["order_id"]
    assert client.delete(f"/api/paper/accounts/{account}/orders/{order_id}").status_code == 200
    # 重复撤单 → 400
    assert client.delete(
        f"/api/paper/accounts/{account}/orders/{order_id}").status_code == 400
    ov = client.get(f"/api/paper/accounts/{account}/overview").json()
    assert ov["cash"] == 1_000_000.0 and ov["frozen"] == 0.0


def test_reset(account):
    assert client.post(
        f"/api/paper/accounts/{account}/reset", json={"confirm": False}).status_code == 400
    resp = client.post(f"/api/paper/accounts/{account}/reset", json={"confirm": True})
    assert resp.status_code == 200 and resp.json()["ok"]
    ov = client.get(f"/api/paper/accounts/{account}/overview").json()
    assert ov["total_asset"] == 1_000_000.0


def test_list_endpoints(account):
    assert client.get(f"/api/paper/accounts/{account}/positions").json() == {"items": []}
    assert client.get(f"/api/paper/accounts/{account}/fills").json()["total"] == 0
    assert client.get(f"/api/paper/accounts/{account}/cash-flows").status_code == 200


def test_update_cost_price_endpoint(account):
    # 无持仓 → 404；非法成本 → 422（pydantic gt=0）
    resp = client.patch(f"/api/paper/accounts/{account}/positions/sh.600000",
                        json={"cost_price": 10.0})
    assert resp.status_code == 404
    resp = client.patch(f"/api/paper/accounts/{account}/positions/sh.600000",
                        json={"cost_price": -1})
    assert resp.status_code == 422


def test_equity_curve_and_metrics(account):
    curve = client.get(f"/api/paper/accounts/{account}/equity-curve")
    assert curve.status_code == 200
    body = curve.json()
    assert len(body["curve"]) >= 1
    m = client.get(f"/api/paper/accounts/{account}/metrics")
    assert m.status_code == 200
    assert set(m.json()) == {"total_return_pct", "annualized_return_pct",
                             "max_drawdown_pct", "win_rate"}
