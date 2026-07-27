"""模拟盘服务层：账户/下单/查询，供 API 路由与脚本调用。

约定：
- 校验失败抛 ValueError（→ API 400）；账户/委托不存在抛 LookupError（→ 404）。
- 行情库一律只读（src.db / src.metrics），paper 库经 store.connect 短连接读写。
- 金额保留 2 位小数；收益率对外一律为「百分数」（5.23 表示 5.23%），
  与前端约定一致（win_rate 除外，为 0-1 小数）。
"""
from __future__ import annotations

import json
import uuid

from src import metrics
from src.paper_trading import config as cfg
from src.paper_trading import store
from src.paper_trading.matcher import _flow, latest_trade_date


# ---------- 工具 ----------

def _account_row(paper_con, account_id: str) -> dict:
    row = paper_con.execute(
        "SELECT account_id, name, init_cash, cash, frozen, created_at, status"
        " FROM accounts WHERE account_id = ?",
        [account_id],
    ).fetchone()
    if not row:
        raise LookupError(f"账户不存在：{account_id}")
    return dict(zip(["account_id", "name", "init_cash", "cash", "frozen",
                     "created_at", "status"], row))


def _order_dict(row) -> dict:
    keys = ["order_id", "account_id", "request_id", "code", "side", "price_type",
            "limit_price", "qty", "status", "reject_reason", "ref_price",
            "frozen_amount", "created_at", "updated_at"]
    d = dict(zip(keys, row))
    for k in ("created_at", "updated_at"):
        d[k] = str(d[k])
    return d


def _latest_closes(codes: list[str]) -> dict[str, dict]:
    """code -> {close, date, name}（每股各自最新一行）。"""
    if not codes:
        return {}
    df = metrics.latest_quotes(codes)
    return {
        r["code"]: {"close": r["close"], "date": str(r["date"]),
                    "name": r.get("code_name")}
        for r in df.to_dict("records")
    }


# ---------- 账户 ----------

def create_account(name: str, init_cash: float) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("账户名称不能为空")
    if init_cash <= 0:
        raise ValueError("初始资金必须大于 0")
    account_id = uuid.uuid4().hex
    with store.connect() as paper_con:
        paper_con.execute(
            "INSERT INTO accounts (account_id, name, init_cash, cash) VALUES (?, ?, ?, ?)",
            [account_id, name, round(init_cash, 2), round(init_cash, 2)],
        )
        td = latest_trade_date()
        if td:
            paper_con.execute(
                "INSERT OR REPLACE INTO equity_snapshots"
                " (account_id, trade_date, cash, frozen, market_value, total_asset)"
                " VALUES (?, ?, ?, 0, 0, ?)",
                [account_id, td, round(init_cash, 2), round(init_cash, 2)],
            )
        return _account_row(paper_con, account_id)


def get_overview(account_id: str) -> dict:
    with store.connect() as paper_con:
        acc = _account_row(paper_con, account_id)
        positions = paper_con.execute(
            "SELECT code, qty FROM positions WHERE account_id = ? AND qty > 0",
            [account_id],
        ).fetchall()
    closes = _latest_closes([p[0] for p in positions])
    market_value = round(sum(qty * closes.get(code, {}).get("close", 0.0)
                             for code, qty in positions), 2)
    total_asset = round(acc["cash"] + acc["frozen"] + market_value, 2)
    total_pnl = round(total_asset - acc["init_cash"], 2)
    return {
        "account_id": acc["account_id"],
        "name": acc["name"],
        "init_cash": acc["init_cash"],
        "cash": acc["cash"],
        "frozen": acc["frozen"],
        "market_value": market_value,
        "total_asset": total_asset,
        "total_pnl": total_pnl,
        "total_return_pct": round(total_pnl / acc["init_cash"] * 100, 2),
        "position_count": len(positions),
        "asof_date": latest_trade_date(),
    }


def reset_account(account_id: str) -> str:
    """重置账户：保留重置前快照（account_resets），清空业务数据并恢复初始资金。"""
    snapshot = get_overview(account_id)
    reset_id = uuid.uuid4().hex
    with store.connect() as paper_con:
        acc = _account_row(paper_con, account_id)
        paper_con.execute(
            "INSERT INTO account_resets (reset_id, account_id, snapshot_json)"
            " VALUES (?, ?, ?)",
            [reset_id, account_id, json.dumps(snapshot, ensure_ascii=False)],
        )
        for table in ("orders", "fills", "cash_flows", "positions", "equity_snapshots"):
            paper_con.execute(f"DELETE FROM {table} WHERE account_id = ?", [account_id])
        paper_con.execute(
            "UPDATE accounts SET frozen = 0 WHERE account_id = ?",
            [account_id],
        )
        # _flow 内部执行 cash += amount：差额回填后可用资金即恢复为初始资金
        _flow(paper_con, account_id, "reset", round(acc["init_cash"] - acc["cash"], 2), reset_id)
        td = latest_trade_date()
        if td:
            paper_con.execute(
                "INSERT OR REPLACE INTO equity_snapshots"
                " (account_id, trade_date, cash, frozen, market_value, total_asset)"
                " VALUES (?, ?, ?, 0, 0, ?)",
                [account_id, td, acc["init_cash"], acc["init_cash"]],
            )
    return reset_id


# ---------- 委托 ----------

def place_order(
    account_id: str,
    request_id: str,
    code: str,
    side: str,
    price_type: str,
    limit_price: float | None,
    qty: int,
) -> dict:
    code = (code or "").strip().lower()
    if not cfg.is_tradable_code(code):
        raise ValueError(f"仅支持沪深 A 股：{code}")
    if side not in ("buy", "sell"):
        raise ValueError("方向须为 buy 或 sell")
    if price_type not in ("market", "limit"):
        raise ValueError("价格类型须为 market 或 limit")
    if price_type == "limit" and (limit_price is None or limit_price <= 0):
        raise ValueError("限价单必须填写正数限价")
    if qty <= 0 or qty % cfg.LOT_SIZE != 0:
        raise ValueError(f"数量须为 {cfg.LOT_SIZE} 股的整数倍")
    if qty > cfg.MAX_ORDER_QTY:
        raise ValueError(f"单笔数量不能超过 {cfg.MAX_ORDER_QTY} 股")

    names = metrics.name_map()
    if code not in names:
        raise ValueError(f"股票不存在：{code}")
    latest = _latest_closes([code]).get(code)
    if latest is None:
        raise ValueError(f"无行情数据：{code}")
    market_latest = latest_trade_date()
    if market_latest and latest["date"] < market_latest:
        raise ValueError(f"该股票已停牌（最近行情 {latest['date']}），禁止下单")

    limit_pct = cfg.board_limit_pct(code, names.get(code, ""))
    ref_price = limit_price if price_type == "limit" else latest["close"]
    if price_type == "limit":
        low, high = cfg.price_limit_range(latest["close"], limit_pct)
        if not (low <= limit_price <= high):
            raise ValueError(f"限价超出涨跌停范围 [{low}, {high}]")

    with store.connect() as paper_con:
        acc = _account_row(paper_con, account_id)
        # 幂等：同 request_id 直接返回原委托
        existing = paper_con.execute(
            "SELECT * FROM orders WHERE account_id = ? AND request_id = ?",
            [account_id, request_id],
        ).fetchone()
        if existing:
            return _order_dict(existing)

        frozen_amount = 0.0
        if side == "buy":
            est_amount = round(qty * ref_price, 2)
            _, _, est_fee = cfg.compute_fees("buy", est_amount)
            frozen_amount = round(est_amount + est_fee, 2)
            if acc["cash"] < frozen_amount - 1e-9:
                raise ValueError(
                    f"可用资金不足：预估需 {frozen_amount:.2f} 元，可用 {acc['cash']:.2f} 元")
        else:
            pos = paper_con.execute(
                "SELECT qty FROM positions WHERE account_id = ? AND code = ?",
                [account_id, code],
            ).fetchone()
            held = pos[0] if pos else 0
            pending_sell = paper_con.execute(
                "SELECT COALESCE(SUM(qty), 0) FROM orders"
                " WHERE account_id = ? AND code = ? AND side = 'sell' AND status = 'pending'",
                [account_id, code],
            ).fetchone()[0]
            if qty > held - pending_sell:
                raise ValueError(f"可卖数量不足：持仓 {held} 股，可卖 {held - pending_sell} 股")

        order_id = uuid.uuid4().hex
        paper_con.execute(
            "INSERT INTO orders (order_id, account_id, request_id, code, side, price_type,"
            " limit_price, qty, status, ref_price, frozen_amount)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            [order_id, account_id, request_id, code, side, price_type,
             limit_price, qty, ref_price, frozen_amount],
        )
        if frozen_amount > 0:
            paper_con.execute(
                "UPDATE accounts SET frozen = round(frozen + ?, 2) WHERE account_id = ?",
                [frozen_amount, account_id],
            )
            _flow(paper_con, account_id, "freeze", -frozen_amount, order_id)
        row = paper_con.execute(
            "SELECT * FROM orders WHERE order_id = ?", [order_id]).fetchone()
        return _order_dict(row)


def cancel_order(account_id: str, order_id: str) -> None:
    with store.connect() as paper_con:
        _account_row(paper_con, account_id)
        row = paper_con.execute(
            "SELECT side, status, frozen_amount FROM orders"
            " WHERE order_id = ? AND account_id = ?",
            [order_id, account_id],
        ).fetchone()
        if not row:
            raise LookupError(f"委托不存在：{order_id}")
        if row[1] != "pending":
            raise ValueError(f"仅待成交委托可撤销（当前状态：{row[1]}）")
        paper_con.execute(
            "UPDATE orders SET status = 'cancelled', updated_at = current_timestamp"
            " WHERE order_id = ?",
            [order_id],
        )
        if row[0] == "buy" and row[2] > 0:
            paper_con.execute(
                "UPDATE accounts SET frozen = round(frozen - ?, 2) WHERE account_id = ?",
                [row[2], account_id],
            )
            _flow(paper_con, account_id, "unfreeze", row[2], order_id)


# ---------- 查询 ----------

def list_positions(account_id: str) -> list[dict]:
    with store.connect() as paper_con:
        _account_row(paper_con, account_id)
        rows = paper_con.execute(
            "SELECT code, qty, cost_price FROM positions WHERE account_id = ? AND qty > 0"
            " ORDER BY code",
            [account_id],
        ).fetchall()
        pending = dict(paper_con.execute(
            "SELECT code, SUM(qty) FROM orders"
            " WHERE account_id = ? AND side = 'sell' AND status = 'pending' GROUP BY code",
            [account_id],
        ).fetchall())
    closes = _latest_closes([r[0] for r in rows])
    items = []
    for code, qty, cost in rows:
        q = closes.get(code, {})
        close = q.get("close", 0.0)
        mv = round(qty * close, 2)
        pnl = round((close - cost) * qty, 2)
        items.append({
            "code": code,
            "name": q.get("name"),
            "qty": qty,
            "sellable_qty": qty - int(pending.get(code, 0)),
            "cost_price": cost,
            "last_close": close,
            "market_value": mv,
            "pnl": pnl,
            "pnl_pct": round((close / cost - 1) * 100, 2) if cost else 0.0,
        })
    return items


def _paged(account_id: str, table: str, where: str, params: list,
           order_by: str, limit: int, offset: int) -> dict:
    with store.connect() as paper_con:
        _account_row(paper_con, account_id)
        total = paper_con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE account_id = ?{where}",
            [account_id, *params],
        ).fetchone()[0]
        rows = paper_con.execute(
            f"SELECT * FROM {table} WHERE account_id = ?{where}"
            f" ORDER BY {order_by} LIMIT ? OFFSET ?",
            [account_id, *params, limit, offset],
        ).fetchall()
        cols = [d[0] for d in paper_con.description]
    items = []
    for r in rows:
        d = dict(zip(cols, r))
        for k, v in d.items():
            if k.endswith("_at") or k.endswith("_date"):
                d[k] = str(v)
        items.append(d)
    return {"items": items, "total": total}


def list_orders(account_id: str, status: str | None, limit: int, offset: int) -> dict:
    where, params = "", []
    if status:
        where, params = " AND status = ?", [status]
    result = _paged(account_id, "orders", where, params,
                    "created_at DESC, order_id", limit, offset)
    names = metrics.name_map()
    for item in result["items"]:
        item["code_name"] = names.get(item["code"])
    return result


def list_fills(account_id: str, limit: int, offset: int) -> dict:
    result = _paged(account_id, "fills", "", [],
                    "trade_date DESC, created_at DESC", limit, offset)
    names = metrics.name_map()
    for item in result["items"]:
        item["code_name"] = names.get(item["code"])
    return result


def list_cash_flows(account_id: str, limit: int, offset: int) -> dict:
    return _paged(account_id, "cash_flows", "", [], "created_at DESC, flow_id", limit, offset)


# ---------- 收益分析 ----------

def _snapshots(account_id: str, start: str | None = None) -> list[dict]:
    with store.connect() as paper_con:
        sql = ("SELECT trade_date, cash, frozen, market_value, total_asset"
               " FROM equity_snapshots WHERE account_id = ?")
        params: list = [account_id]
        if start:
            sql += " AND trade_date >= ?"
            params.append(start)
        rows = paper_con.execute(sql + " ORDER BY trade_date", params).fetchall()
    return [dict(zip(["trade_date", "cash", "frozen", "market_value", "total_asset"], r))
            for r in rows]


def equity_curve(account_id: str, start: str | None = None) -> dict:
    with store.connect() as paper_con:
        acc = _account_row(paper_con, account_id)
    snaps = _snapshots(account_id, start)
    curve = [{
        "date": str(s["trade_date"]),
        "total_asset": s["total_asset"],
        "return_pct": round((s["total_asset"] / acc["init_cash"] - 1) * 100, 4),
    } for s in snaps]

    benchmark: list[dict] = []
    if curve:
        series = metrics.equal_weighted_index(start_date=curve[0]["date"])
        if not series.empty:
            first = float(series.iloc[0])
            base = 1 + first
            benchmark = [
                {"date": str(d.date() if hasattr(d, "date") else d),
                 "value": round((1 + float(v)) / base - 1, 6)}
                for d, v in series.items()
            ]
    return {"curve": curve, "benchmark": benchmark}


def account_metrics(account_id: str) -> dict:
    with store.connect() as paper_con:
        acc = _account_row(paper_con, account_id)
        fills = paper_con.execute(
            "SELECT code, side, price, qty, trade_date, created_at FROM fills"
            " WHERE account_id = ? ORDER BY trade_date, created_at",
            [account_id],
        ).fetchall()
    snaps = _snapshots(account_id)
    assets = [s["total_asset"] for s in snaps]

    total_return_pct = round((assets[-1] / acc["init_cash"] - 1) * 100, 2) if assets else 0.0
    n = len(assets)
    if n > 1 and assets[-1] > 0:
        annualized = round(((assets[-1] / acc["init_cash"]) ** (252 / (n - 1)) - 1) * 100, 2)
    else:
        annualized = 0.0
    peak, max_dd = 0.0, 0.0
    for a in assets:
        peak = max(peak, a)
        if peak > 0:
            max_dd = max(max_dd, (peak - a) / peak * 100)

    # 胜率：按摊薄成本滚动，盈利卖出笔数 / 总卖出笔数（0-1 小数）
    cost_qty: dict[str, list[float]] = {}
    sells, wins = 0, 0
    for code, side, price, qty, _td, _ca in fills:
        if side == "buy":
            cq = cost_qty.setdefault(code, [0.0, 0.0])
            new_qty = cq[1] + qty
            cq[0] = (cq[0] * cq[1] + price * qty) / new_qty
            cq[1] = new_qty
        else:
            sells += 1
            cq = cost_qty.get(code)
            if cq and price > cq[0]:
                wins += 1
            if cq:
                cq[1] -= qty
    return {
        "total_return_pct": total_return_pct,
        "annualized_return_pct": annualized,
        "max_drawdown_pct": round(max_dd, 2),
        "win_rate": round(wins / sells, 4) if sells else 0.0,
    }
