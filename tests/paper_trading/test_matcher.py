"""撮合引擎单元测试：每个交易规则独立用例。"""
import uuid

import pytest

from src import db
from src.paper_trading import config as cfg
from src.paper_trading import service, store
from src.paper_trading.matcher import match_day

from .conftest import (CHINEXT_LIMIT_DAY, DAY1, DAY2, LIMIT_DAY, SUSPEND_DAY,
                       backdate_orders)


def run_match(market_path, paper_path, trade_date):
    with db.connect(read_only=True, path=market_path) as mc, \
            store.connect(path=paper_path) as pc:
        return match_day(mc, pc, trade_date)


def make_account(cash=1_000_000.0):
    return service.create_account("测试账户", cash)["account_id"]


def place(aid, code, side, qty, price_type="market", limit_price=None):
    return service.place_order(aid, uuid.uuid4().hex, code, side, price_type, limit_price, qty)


def account_state(paper_path, aid):
    with store.connect(path=paper_path) as pc:
        cash, frozen = pc.execute(
            "SELECT cash, frozen FROM accounts WHERE account_id = ?", [aid]).fetchone()
    return cash, frozen


def order_status(paper_path, order_id):
    with store.connect(path=paper_path) as pc:
        return pc.execute(
            "SELECT status, reject_reason FROM orders WHERE order_id = ?", [order_id]
        ).fetchone()


# ---------- 板块/涨跌停规则 ----------

def test_board_limit_pct():
    assert cfg.board_limit_pct("sh.600000", "浦发银行") == 10.0
    assert cfg.board_limit_pct("sz.000001", "平安银行") == 10.0
    assert cfg.board_limit_pct("sh.600123", "ST测试") == 5.0
    assert cfg.board_limit_pct("sh.600123", "*ST某某") == 5.0
    assert cfg.board_limit_pct("sz.300750", "宁德时代") == 20.0
    assert cfg.board_limit_pct("sh.688981", "中芯国际") == 20.0
    assert cfg.board_limit_pct("sh.688123", "ST科创") == 20.0  # 科创板 ST 仍 ±20%
    assert cfg.board_limit_pct("bj.830799", "北证") == 30.0
    assert cfg.is_limit_up(10.0, 10.0) and not cfg.is_limit_up(9.5, 10.0)
    assert cfg.is_limit_down(-5.0, 5.0) and not cfg.is_limit_down(-4.5, 5.0)


def test_fees_min_commission_and_stamp():
    commission, stamp, fee = cfg.compute_fees("buy", 1000.0)
    assert commission == 5.0 and stamp == 0.0 and fee == 5.0      # 最低佣金 5 元
    commission, stamp, fee = cfg.compute_fees("sell", 100000.0)
    assert commission == 25.0 and stamp == 50.0 and fee == 75.0   # 万2.5 + 印花税 0.05%


# ---------- 市价单 ----------

def test_market_buy_fills_at_close(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    order = place(aid, "sz.002594", "buy", 1000)
    assert order["frozen_amount"] == 10005.0   # 1000*10 + 最低佣金 5
    assert account_state(paper_path, aid) == (1_000_000.0 - 10005.0, 10005.0)

    backdate_orders(paper_path, DAY1)
    result = run_match(market_path, paper_path, DAY1)
    assert result.filled == 1

    cash, frozen = account_state(paper_path, aid)
    assert cash == 989_995.0 and frozen == 0.0
    assert order_status(paper_path, order["order_id"])[0] == "filled"
    with store.connect(path=paper_path) as pc:
        fill = pc.execute(
            "SELECT price, qty, amount, commission, stamp_tax FROM fills WHERE order_id = ?",
            [order["order_id"]]).fetchone()
        assert fill == (10.0, 1000, 10000.0, 5.0, 0.0)
        pos = pc.execute(
            "SELECT qty, cost_price FROM positions WHERE account_id = ? AND code = 'sz.002594'",
            [aid]).fetchone()
        assert pos[0] == 1000
        assert pos[1] == pytest.approx(10.005, abs=1e-4)  # 摊薄成本含买入费用


def test_market_buy_skipped_on_limit_up(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    order = place(aid, "sh.600000", "buy", 1000)
    backdate_orders(paper_path, LIMIT_DAY)
    result = run_match(market_path, paper_path, LIMIT_DAY)
    assert result.skipped == 1 and result.filled == 0
    assert order_status(paper_path, order["order_id"])[0] == "pending"
    # 冻结资金不释放
    _, frozen = account_state(paper_path, aid)
    assert frozen == order["frozen_amount"]


def test_market_sell_skipped_on_limit_down(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    place(aid, "sz.000001", "buy", 1000)
    backdate_orders(paper_path, DAY1)
    run_match(market_path, paper_path, DAY1)

    sell = place(aid, "sz.000001", "sell", 1000)
    backdate_orders(paper_path, LIMIT_DAY)
    result = run_match(market_path, paper_path, LIMIT_DAY)
    assert result.skipped == 1
    assert order_status(paper_path, sell["order_id"])[0] == "pending"


def test_market_skipped_when_suspended(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    order = place(aid, "sz.002594", "buy", 1000)
    backdate_orders(paper_path, SUSPEND_DAY)
    result = run_match(market_path, paper_path, SUSPEND_DAY)
    assert result.skipped == 1
    assert order_status(paper_path, order["order_id"])[0] == "pending"


# ---------- 限价单 ----------

def test_limit_buy_fills_at_close_when_reachable(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    order = place(aid, "sz.002594", "buy", 1000, "limit", 10.5)
    backdate_orders(paper_path, DAY1)
    result = run_match(market_path, paper_path, DAY1)
    assert result.filled == 1
    with store.connect(path=paper_path) as pc:
        price = pc.execute(
            "SELECT price FROM fills WHERE order_id = ?", [order["order_id"]]).fetchone()[0]
    assert price == 10.0   # 按收盘价成交而非限价


def test_limit_buy_expires_when_unreachable(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    order = place(aid, "sz.002594", "buy", 1000, "limit", 9.5)
    backdate_orders(paper_path, DAY1)
    result = run_match(market_path, paper_path, DAY1)
    assert result.expired == 1
    assert order_status(paper_path, order["order_id"])[0] == "expired"
    assert account_state(paper_path, aid) == (1_000_000.0, 0.0)  # 解冻


def test_limit_buy_expires_on_limit_up(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    order = place(aid, "sh.600000", "buy", 1000, "limit", 11.0)
    backdate_orders(paper_path, LIMIT_DAY)
    result = run_match(market_path, paper_path, LIMIT_DAY)
    assert result.expired == 1


def test_limit_sell_fills_when_close_above_limit(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    place(aid, "sz.002594", "buy", 1000)
    backdate_orders(paper_path, DAY1)
    run_match(market_path, paper_path, DAY1)

    sell = place(aid, "sz.002594", "sell", 1000, "limit", 9.5)
    backdate_orders(paper_path, DAY2)
    result = run_match(market_path, paper_path, DAY2)
    assert result.filled == 1
    with store.connect(path=paper_path) as pc:
        fill = pc.execute(
            "SELECT price, stamp_tax FROM fills WHERE order_id = ?",
            [sell["order_id"]]).fetchone()
    assert fill == (10.0, 5.0)   # 卖出收印花税 0.05%


def test_limit_expires_when_suspended(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    order = place(aid, "sz.002594", "buy", 1000, "limit", 10.5)
    backdate_orders(paper_path, SUSPEND_DAY)
    result = run_match(market_path, paper_path, SUSPEND_DAY)
    assert result.expired == 1


# ---------- ST / 创业板 ----------

def test_st_stock_limit_up_at_5pct(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    order = place(aid, "sh.600123", "buy", 1000)
    backdate_orders(paper_path, LIMIT_DAY)   # ST 当日 +5.0% 涨停
    result = run_match(market_path, paper_path, LIMIT_DAY)
    assert result.skipped == 1
    assert order_status(paper_path, order["order_id"])[0] == "pending"


def test_chinext_limit_up_at_20pct(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    order = place(aid, "sz.300750", "buy", 100)
    backdate_orders(paper_path, CHINEXT_LIMIT_DAY)   # +20.0% 涨停
    result = run_match(market_path, paper_path, CHINEXT_LIMIT_DAY)
    assert result.skipped == 1


# ---------- T+1 / 资金 / 幂等 ----------

def test_t1_buy_then_sell_next_day(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    place(aid, "sz.002594", "buy", 1000)
    backdate_orders(paper_path, DAY1)
    run_match(market_path, paper_path, DAY1)

    # 当日买入不可卖：可卖 = 持仓 - pending 卖单。成交后持仓 1000 可卖 1000
    # （T+1 由撮合时机保证：卖单最早于下一交易日收盘成交）
    sell = place(aid, "sz.002594", "sell", 1000)
    backdate_orders(paper_path, DAY2)
    result = run_match(market_path, paper_path, DAY2)
    assert result.filled == 1
    cash, _ = account_state(paper_path, aid)
    assert cash == 1_000_000.0 - 10005.0 + 9990.0   # 买入 10005，卖出到账 10000-5-5
    with store.connect(path=paper_path) as pc:
        assert pc.execute(
            "SELECT COUNT(*) FROM positions WHERE account_id = ?", [aid]).fetchone()[0] == 0


def test_sell_without_position_rejected_at_placement(paper_env):
    aid = make_account()
    with pytest.raises(ValueError, match="可卖数量不足"):
        place(aid, "sz.002594", "sell", 1000)


def test_insufficient_funds_at_match(paper_env):
    market_path, paper_path = paper_env
    aid = make_account(cash=10005.0)
    order = place(aid, "sz.002594", "buy", 1000)   # 恰好够冻结
    with store.connect(path=paper_path) as pc:     # 模拟撮合前资金被占用减少
        pc.execute("UPDATE accounts SET cash = cash - 10 WHERE account_id = ?", [aid])
    backdate_orders(paper_path, DAY1)
    result = run_match(market_path, paper_path, DAY1)
    assert result.rejected == 1
    assert order_status(paper_path, order["order_id"]) == ("rejected", "insufficient_funds")
    cash, frozen = account_state(paper_path, aid)
    assert frozen == 0.0 and cash == 9995.0        # 解冻后可用资金保持


def test_rerun_same_day_is_idempotent(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    place(aid, "sz.002594", "buy", 1000)
    backdate_orders(paper_path, DAY1)
    first = run_match(market_path, paper_path, DAY1)
    second = run_match(market_path, paper_path, DAY1)
    assert first.filled == 1 and second.filled == 0
    with store.connect(path=paper_path) as pc:
        assert pc.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
        assert pc.execute(
            "SELECT COUNT(*) FROM equity_snapshots WHERE account_id = ? AND trade_date = ?",
            [aid, DAY1]).fetchone()[0] == 1


def test_equity_snapshot_values(paper_env):
    market_path, paper_path = paper_env
    aid = make_account()
    place(aid, "sz.002594", "buy", 1000)
    backdate_orders(paper_path, DAY1)
    run_match(market_path, paper_path, DAY1)
    with store.connect(path=paper_path) as pc:
        snap = pc.execute(
            "SELECT cash, frozen, market_value, total_asset FROM equity_snapshots"
            " WHERE account_id = ? AND trade_date = ?", [aid, DAY1]).fetchone()
    assert snap[0] == 989_995.0 and snap[1] == 0.0
    assert snap[2] == 10000.0                        # 1000 股 × 收盘 10
    assert snap[3] == 999_995.0


def test_cash_flow_reconciliation(paper_env):
    """对账：任意时点 sum(cash_flows.amount) == cash - init_cash，且 frozen 与 pending 买单一致。"""
    market_path, paper_path = paper_env
    aid = make_account()
    place(aid, "sz.002594", "buy", 1000)                    # 成交
    place(aid, "sz.002594", "buy", 1000, "limit", 9.5)      # 过期
    place(aid, "sz.002594", "buy", 500)                     # 撤销
    with store.connect(path=paper_path) as pc:
        cancel_id = pc.execute(
            "SELECT order_id FROM orders WHERE account_id = ? AND qty = 500", [aid]
        ).fetchone()[0]
    service.cancel_order(aid, cancel_id)
    backdate_orders(paper_path, DAY1)
    run_match(market_path, paper_path, DAY1)

    with store.connect(path=paper_path) as pc:
        cash, frozen, init = pc.execute(
            "SELECT cash, frozen, init_cash FROM accounts WHERE account_id = ?", [aid]
        ).fetchone()
        flow_sum = pc.execute(
            "SELECT SUM(amount) FROM cash_flows WHERE account_id = ?", [aid]).fetchone()[0]
        pending_frozen = pc.execute(
            "SELECT COALESCE(SUM(frozen_amount), 0) FROM orders"
            " WHERE account_id = ? AND status = 'pending' AND side = 'buy'", [aid]
        ).fetchone()[0]
    assert flow_sum == pytest.approx(cash - init, abs=1e-6)
    assert frozen == pytest.approx(pending_frozen, abs=1e-6)
