"""模拟盘接口：账户/委托/持仓/流水/收益分析。

- account_id 为 path 参数即能力凭证（UUID 不可猜测），无登录体系；
- 写接口 body 含 request_id，靠 (account_id, request_id) 唯一约束幂等；
- 写接口直连 paper.duckdb 不走 Redis；撮合后由撮合任务全量清缓存。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.paper_trading import service

router = APIRouter(prefix="/paper", tags=["paper"])


class CreateAccountBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    init_cash: float = Field(..., gt=0)


class ResetBody(BaseModel):
    confirm: bool = False


class PlaceOrderBody(BaseModel):
    request_id: str = Field(..., min_length=8, max_length=64)
    code: str
    side: str                      # buy / sell
    price_type: str                # market / limit
    limit_price: float | None = None
    qty: int = Field(..., gt=0)


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/accounts", status_code=201)
def create_account(body: CreateAccountBody):
    return _handle(service.create_account, body.name, body.init_cash)


@router.get("/accounts/{account_id}/overview")
def overview(account_id: str):
    return _handle(service.get_overview, account_id)


@router.post("/accounts/{account_id}/reset")
def reset(account_id: str, body: ResetBody):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="重置为高危操作，请确认 confirm=true")
    reset_id = _handle(service.reset_account, account_id)
    return {"ok": True, "reset_id": reset_id}


@router.get("/accounts/{account_id}/positions")
def positions(account_id: str):
    return {"items": _handle(service.list_positions, account_id)}


@router.post("/accounts/{account_id}/orders", status_code=201)
def place_order(account_id: str, body: PlaceOrderBody):
    return _handle(
        service.place_order, account_id, body.request_id, body.code,
        body.side, body.price_type, body.limit_price, body.qty,
    )


@router.delete("/accounts/{account_id}/orders/{order_id}")
def cancel_order(account_id: str, order_id: str):
    _handle(service.cancel_order, account_id, order_id)
    return {"ok": True}


@router.get("/accounts/{account_id}/orders")
def orders(
    account_id: str,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return _handle(service.list_orders, account_id, status, limit, offset)


@router.get("/accounts/{account_id}/fills")
def fills(
    account_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return _handle(service.list_fills, account_id, limit, offset)


@router.get("/accounts/{account_id}/cash-flows")
def cash_flows(
    account_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return _handle(service.list_cash_flows, account_id, limit, offset)


@router.get("/accounts/{account_id}/equity-curve")
def equity_curve(account_id: str, start: str | None = Query(None)):
    return _handle(service.equity_curve, account_id, start)


@router.get("/accounts/{account_id}/metrics")
def account_metrics(account_id: str):
    return _handle(service.account_metrics, account_id)
