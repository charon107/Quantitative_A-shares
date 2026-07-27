"""日频撮合引擎（纯函数风格，不依赖 FastAPI）。

输入：行情库只读连接 + paper 库读写连接 + 交易日；输出：成交/作废/跳过统计。
撮合规则见 docs/paper-trading-design.md §3：
- 市价单按当日收盘价成交（涨停不买/跌停不卖，跳过保留至下一交易日）；
- 限价买单 close ≤ 限价成交，限价卖单 close ≥ 限价成交，当日未成交即过期；
- 停牌（当日无行情）：市价单跳过保留，限价单过期；
- 撮合时资金不足 → rejected 并解冻；
- 同一交易日重跑幂等（只处理 status='pending' 的委托）。
支持历史回放：传入任意历史交易日与干净的测试库即可重算。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import duckdb

from src import cache, db
from src.paper_trading import config as cfg
from src.paper_trading import store


@dataclass
class MatchResult:
    trade_date: str
    filled: int = 0
    expired: int = 0
    rejected: int = 0
    skipped: int = 0            # 停牌/涨跌停，保留 pending 顺延
    fill_ids: list[str] = field(default_factory=list)


def _now() -> str:
    return "current_timestamp"


def _flow(paper_con, account_id: str, flow_type: str, amount: float, ref_id: str | None) -> None:
    """记一笔资金流水（amount 为有符号可用资金变动），balance_after 取变动后可用资金。"""
    paper_con.execute(
        "UPDATE accounts SET cash = round(cash + ?, 2) WHERE account_id = ?",
        [amount, account_id],
    )
    balance = paper_con.execute(
        "SELECT cash FROM accounts WHERE account_id = ?", [account_id]
    ).fetchone()[0]
    paper_con.execute(
        "INSERT INTO cash_flows (flow_id, account_id, type, amount, balance_after, ref_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [uuid.uuid4().hex, account_id, flow_type, round(amount, 2), balance, ref_id],
    )


def _unfreeze(paper_con, account_id: str, amount: float, ref_id: str) -> None:
    """解冻资金：frozen 减少、可用资金回流。"""
    if amount <= 0:
        return
    paper_con.execute(
        "UPDATE accounts SET frozen = round(frozen - ?, 2) WHERE account_id = ?",
        [amount, account_id],
    )
    _flow(paper_con, account_id, "unfreeze", amount, ref_id)


def _close_order(paper_con, order_id: str, status: str, reject_reason: str | None = None) -> None:
    paper_con.execute(
        f"UPDATE orders SET status = ?, reject_reason = ?, updated_at = {_now()}"
        " WHERE order_id = ?",
        [status, reject_reason, order_id],
    )


def _fill(paper_con, order: dict, close: float, trade_date: str) -> str | None:
    """执行成交。返回 fill_id；资金/持仓不足时记 rejected 返回 None。"""
    amount = round(close * order["qty"], 2)
    commission, stamp, fee = cfg.compute_fees(order["side"], amount)
    account_id, order_id = order["account_id"], order["order_id"]

    # 先解冻该单冻结资金（多退少补：解冻后按实际成交额+费用扣款）
    _unfreeze(paper_con, account_id, order["frozen_amount"], order_id)
    cash = paper_con.execute(
        "SELECT cash FROM accounts WHERE account_id = ?", [account_id]
    ).fetchone()[0]

    if order["side"] == "buy":
        total_cost = round(amount + fee, 2)
        if cash < total_cost - 1e-9:
            _close_order(paper_con, order_id, "rejected", "insufficient_funds")
            return None
        _flow(paper_con, account_id, "buy", -total_cost, order_id)
        pos = paper_con.execute(
            "SELECT qty, cost_price FROM positions WHERE account_id = ? AND code = ?",
            [account_id, order["code"]],
        ).fetchone()
        if pos:
            new_qty = pos[0] + order["qty"]
            new_cost = round((pos[0] * pos[1] + total_cost) / new_qty, 4)
            paper_con.execute(
                f"UPDATE positions SET qty = ?, cost_price = ?, updated_at = {_now()}"
                " WHERE account_id = ? AND code = ?",
                [new_qty, new_cost, account_id, order["code"]],
            )
        else:
            paper_con.execute(
                "INSERT INTO positions (account_id, code, qty, cost_price) VALUES (?, ?, ?, ?)",
                [account_id, order["code"], order["qty"], round(total_cost / order["qty"], 4)],
            )
    else:  # sell
        pos = paper_con.execute(
            "SELECT qty FROM positions WHERE account_id = ? AND code = ?",
            [account_id, order["code"]],
        ).fetchone()
        if not pos or pos[0] < order["qty"]:
            _close_order(paper_con, order_id, "rejected", "insufficient_position")
            return None
        proceeds = round(amount - fee, 2)
        _flow(paper_con, account_id, "sell", proceeds, order_id)
        new_qty = pos[0] - order["qty"]
        if new_qty == 0:
            paper_con.execute(
                "DELETE FROM positions WHERE account_id = ? AND code = ?",
                [account_id, order["code"]],
            )
        else:
            paper_con.execute(
                f"UPDATE positions SET qty = ?, updated_at = {_now()}"
                " WHERE account_id = ? AND code = ?",
                [new_qty, account_id, order["code"]],
            )

    fill_id = uuid.uuid4().hex
    paper_con.execute(
        "INSERT INTO fills (fill_id, order_id, account_id, code, side, price, qty,"
        " amount, commission, stamp_tax, fee, trade_date)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fill_id, order_id, account_id, order["code"], order["side"], close,
         order["qty"], amount, commission, stamp, fee, trade_date],
    )
    _close_order(paper_con, order_id, "filled")
    return fill_id


def _expire(paper_con, order: dict) -> None:
    if order["side"] == "buy":
        _unfreeze(paper_con, order["account_id"], order["frozen_amount"], order["order_id"])
    _close_order(paper_con, order["order_id"], "expired")


def _snapshot_all(paper_con, market_con, trade_date: str) -> None:
    """为全部活跃账户生成当日净值快照（持仓按 trade_date 前最后已知收盘价估值）。"""
    accounts = paper_con.execute(
        "SELECT account_id, cash, frozen FROM accounts"
        " WHERE status = 'active' AND CAST(created_at AS DATE) <= ?",
        [trade_date],
    ).fetchall()
    if not accounts:
        return
    positions = paper_con.execute(
        "SELECT account_id, code, qty FROM positions WHERE qty > 0"
    ).fetchall()
    codes = sorted({p[1] for p in positions})
    last_close: dict[str, float] = {}
    if codes:
        ph = ", ".join(["?"] * len(codes))
        rows = market_con.execute(
            f"SELECT code, close FROM kline WHERE date <= ? AND code IN ({ph})"
            " QUALIFY row_number() OVER (PARTITION BY code ORDER BY date DESC) = 1",
            [trade_date, *codes],
        ).fetchall()
        last_close = {r[0]: r[1] for r in rows}
    mv_by_account: dict[str, float] = {}
    for account_id, code, qty in positions:
        px = last_close.get(code, 0.0)
        mv_by_account[account_id] = round(mv_by_account.get(account_id, 0.0) + qty * px, 2)
    for account_id, cash, frozen in accounts:
        mv = mv_by_account.get(account_id, 0.0)
        paper_con.execute(
            "INSERT OR REPLACE INTO equity_snapshots"
            " (account_id, trade_date, cash, frozen, market_value, total_asset)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [account_id, trade_date, cash, frozen, mv, round(cash + frozen + mv, 2)],
        )


def match_day(market_con, paper_con, trade_date: str) -> MatchResult:
    """对全部 pending 委托按 trade_date 收盘价批量撮合（事务内原子提交）。"""
    trade_date = str(trade_date)
    result = MatchResult(trade_date=trade_date)
    rows = paper_con.execute(
        "SELECT order_id, account_id, code, side, price_type, limit_price, qty, frozen_amount"
        " FROM orders WHERE status = 'pending' AND CAST(created_at AS DATE) <= ?"
        " ORDER BY created_at, order_id",
        [trade_date],
    ).fetchall()
    orders = [
        dict(zip(["order_id", "account_id", "code", "side", "price_type",
                  "limit_price", "qty", "frozen_amount"], r))
        for r in rows
    ]
    codes = sorted({o["code"] for o in orders})
    quotes: dict[str, tuple[float, float | None]] = {}
    names: dict[str, str] = {}
    if codes:
        ph = ", ".join(["?"] * len(codes))
        for code, close, pct in market_con.execute(
            f"SELECT code, close, pctChg FROM kline WHERE date = ? AND code IN ({ph})",
            [trade_date, *codes],
        ).fetchall():
            quotes[code] = (close, pct)
        names = dict(market_con.execute(
            f"SELECT code, code_name FROM stock_meta WHERE code IN ({ph})", codes
        ).fetchall())

    paper_con.execute("BEGIN TRANSACTION")
    try:
        for order in orders:
            quote = quotes.get(order["code"])
            if quote is None:  # 停牌：市价顺延，限价当日作废
                if order["price_type"] == "limit":
                    _expire(paper_con, order)
                    result.expired += 1
                else:
                    result.skipped += 1
                continue
            close, pct_chg = quote
            limit_pct = cfg.board_limit_pct(order["code"], names.get(order["code"], ""))
            is_buy = order["side"] == "buy"
            blocked = cfg.is_limit_up(pct_chg, limit_pct) if is_buy \
                else cfg.is_limit_down(pct_chg, limit_pct)
            if blocked:  # 涨停买不进 / 跌停卖不出
                if order["price_type"] == "limit":
                    _expire(paper_con, order)
                    result.expired += 1
                else:
                    result.skipped += 1
                continue
            if order["price_type"] == "limit":
                reachable = close <= order["limit_price"] + 1e-9 if is_buy \
                    else close >= order["limit_price"] - 1e-9
                if not reachable:
                    _expire(paper_con, order)
                    result.expired += 1
                    continue
            fill_id = _fill(paper_con, order, close, trade_date)
            if fill_id:
                result.filled += 1
                result.fill_ids.append(fill_id)
            else:
                result.rejected += 1
        _snapshot_all(paper_con, market_con, trade_date)
        paper_con.execute("COMMIT")
    except Exception:
        paper_con.execute("ROLLBACK")
        raise
    return result


def latest_trade_date(market_path: str | None = None) -> str | None:
    """行情库最近交易日（YYYY-MM-DD）。"""
    df = db.query_df("SELECT MAX(date) AS d FROM kline", path=market_path)
    if df.empty or df["d"].iloc[0] is None:
        return None
    return str(df["d"].iloc[0])[:10]


def run_daily_match(
    trade_date: str | None = None,
    market_path: str | None = None,
    paper_path: str | None = None,
) -> MatchResult:
    """每日撮合入口：默认取行情库最近交易日；结束后全量清 Redis 缓存。"""
    if trade_date is None:
        trade_date = latest_trade_date(market_path)
        if trade_date is None:
            raise RuntimeError("行情库为空，无法确定撮合日")
    with db.connect(read_only=True, path=market_path) as market_con, \
            store.connect(path=paper_path) as paper_con:
        result = match_day(market_con, paper_con, trade_date)
    cache.invalidate_all()
    return result
