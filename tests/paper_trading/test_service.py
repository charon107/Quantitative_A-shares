"""服务层测试：账户/下单校验/撤单/重置/查询/收益。"""
import uuid

import pytest

from src import db
from src.paper_trading import service, store
from src.paper_trading.matcher import match_day

from .conftest import DAY1, backdate_orders


def make_account(cash=1_000_000.0, name="测试账户"):
    return service.create_account(name, cash)["account_id"]


def run_match(market_path, paper_path, trade_date):
    with db.connect(read_only=True, path=market_path) as mc, \
            store.connect(path=paper_path) as pc:
        return match_day(mc, pc, trade_date)


# ---------- 账户 ----------

def test_create_account_validation(paper_env):
    with pytest.raises(ValueError, match="名称"):
        service.create_account("  ", 1_000_000)
    with pytest.raises(ValueError, match="初始资金"):
        service.create_account("x", 0)
    acc = service.create_account("x", 500_000)
    assert acc["cash"] == 500_000.0 and acc["frozen"] == 0.0


def test_overview(paper_env):
    aid = make_account()
    ov = service.get_overview(aid)
    assert ov["total_asset"] == 1_000_000.0
    assert ov["total_pnl"] == 0.0 and ov["total_return_pct"] == 0.0
    assert ov["position_count"] == 0 and ov["asof_date"]
    with pytest.raises(LookupError):
        service.get_overview("no-such-account")


def test_reset_keeps_snapshot_and_restores(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "market", None, 1000)
    backdate_orders(paper_path, DAY1)
    run_match(market_path, paper_path, DAY1)

    reset_id = service.reset_account(aid)
    assert reset_id
    ov = service.get_overview(aid)
    assert ov["cash"] == 1_000_000.0 and ov["market_value"] == 0.0
    assert ov["position_count"] == 0
    with store.connect(path=paper_path) as pc:
        assert pc.execute(
            "SELECT COUNT(*) FROM account_resets WHERE account_id = ?", [aid]
        ).fetchone()[0] == 1
        for table in ("orders", "fills", "positions"):
            assert pc.execute(
                f"SELECT COUNT(*) FROM {table} WHERE account_id = ?", [aid]
            ).fetchone()[0] == 0
        # 重置资金流水保留（对账可回溯）
        assert pc.execute(
            "SELECT COUNT(*) FROM cash_flows WHERE account_id = ? AND type = 'reset'",
            [aid]).fetchone()[0] == 1


# ---------- 下单校验 ----------

def test_place_order_code_validation(paper_env):
    aid = make_account()
    with pytest.raises(ValueError, match="沪深"):
        service.place_order(aid, uuid.uuid4().hex, "bj.830799", "buy", "market", None, 100)
    with pytest.raises(ValueError, match="不存在"):
        service.place_order(aid, uuid.uuid4().hex, "sh.999999", "buy", "market", None, 100)


def test_place_order_qty_validation(paper_env):
    aid = make_account()
    with pytest.raises(ValueError, match="整数倍"):
        service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "market", None, 150)
    with pytest.raises(ValueError, match="整数倍"):
        service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "market", None, 0)
    with pytest.raises(ValueError, match="上限|不能超过"):
        service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "market", None, 2_000_000)


def test_place_order_limit_price_validation(paper_env):
    aid = make_account()
    with pytest.raises(ValueError, match="限价"):
        service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "limit", None, 100)
    # 002594 最新收盘 10.0，主板 ±10% → 合法区间 [9.0, 11.0]
    with pytest.raises(ValueError, match="涨跌停"):
        service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "limit", 12.0, 100)


def test_place_order_suspended_stock_rejected(paper_env):
    aid = make_account()
    with pytest.raises(ValueError, match="停牌"):
        service.place_order(aid, uuid.uuid4().hex, "sh.600999", "buy", "market", None, 100)


def test_place_order_insufficient_funds(paper_env):
    aid = make_account(cash=1000.0)
    with pytest.raises(ValueError, match="可用资金不足"):
        service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "market", None, 1000)


def test_place_order_idempotent_by_request_id(paper_env):
    aid = make_account()
    rid = uuid.uuid4().hex
    first = service.place_order(aid, rid, "sz.002594", "buy", "market", None, 1000)
    second = service.place_order(aid, rid, "sz.002594", "buy", "market", None, 1000)
    assert first["order_id"] == second["order_id"]
    assert service.get_overview(aid)["frozen"] == first["frozen_amount"]  # 只冻结一次


def test_sell_reserves_pending_qty(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "market", None, 1000)
    backdate_orders(paper_path, DAY1)
    run_match(market_path, paper_path, DAY1)
    service.place_order(aid, uuid.uuid4().hex, "sz.002594", "sell", "market", None, 600)
    with pytest.raises(ValueError, match="可卖数量不足"):
        service.place_order(aid, uuid.uuid4().hex, "sz.002594", "sell", "market", None, 500)


# ---------- 撤单 ----------

def test_cancel_order(paper_env):
    aid = make_account()
    order = service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "market", None, 1000)
    service.cancel_order(aid, order["order_id"])
    ov = service.get_overview(aid)
    assert ov["cash"] == 1_000_000.0 and ov["frozen"] == 0.0
    with pytest.raises(ValueError, match="待成交"):
        service.cancel_order(aid, order["order_id"])   # 已撤销不可再撤
    with pytest.raises(LookupError):
        service.cancel_order(aid, "no-such-order")


# ---------- 查询与收益 ----------

def test_positions_and_records(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "market", None, 1000)
    backdate_orders(paper_path, DAY1)
    run_match(market_path, paper_path, DAY1)

    positions = service.list_positions(aid)
    assert len(positions) == 1
    pos = positions[0]
    assert pos["code"] == "sz.002594" and pos["qty"] == 1000
    assert pos["sellable_qty"] == 1000
    assert pos["last_close"] == 10.0 and pos["market_value"] == 10000.0
    assert pos["pnl"] < 0   # 成本含费用，浮亏 = -费用

    orders = service.list_orders(aid, None, 50, 0)
    assert orders["total"] == 1 and orders["items"][0]["status"] == "filled"
    assert orders["items"][0]["code_name"] == "停牌股份"
    filled = service.list_orders(aid, "filled", 50, 0)
    assert filled["total"] == 1
    pending = service.list_orders(aid, "pending", 50, 0)
    assert pending["total"] == 0

    fills = service.list_fills(aid, 50, 0)
    assert fills["total"] == 1 and fills["items"][0]["price"] == 10.0

    flows = service.list_cash_flows(aid, 50, 0)
    types = [f["type"] for f in flows["items"]]
    assert "freeze" in types and "buy" in types and "unfreeze" in types


def test_equity_curve_and_metrics(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    service.place_order(aid, uuid.uuid4().hex, "sz.002594", "buy", "market", None, 1000)
    backdate_orders(paper_path, DAY1)
    run_match(market_path, paper_path, DAY1)

    curve = service.equity_curve(aid)
    assert len(curve["curve"]) >= 1
    assert curve["curve"][0]["total_asset"] > 0
    assert all("date" in p and "value" in p for p in curve["benchmark"])
    day1 = next(p for p in curve["curve"] if p["date"] == DAY1)
    assert day1["return_pct"] < 0   # 费用导致小幅亏损

    m = service.account_metrics(aid)
    assert set(m) == {"total_return_pct", "annualized_return_pct",
                      "max_drawdown_pct", "win_rate"}
    assert 0.0 <= m["win_rate"] <= 1.0
    assert m["max_drawdown_pct"] >= 0.0
