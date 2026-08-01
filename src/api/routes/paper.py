"""模拟盘接口：账户/委托/持仓/流水/收益分析。

鉴权（规范书 §7.5 多租户，M4）：
- 读操作：携带有效 JWT → Principal 并校验账户归属（verify_account_access）；
  AUTH_MODE=hybrid/legacy 且 LEGACY_ACCOUNT_ID_AUTH 允许时，无 token 回退
  account_id 凭证（仅校验账户存在，记 DEPRECATED 审计日志）。
- 写操作（下单/撤单/重置/改成本）：JWT 必填（trader/admin 角色）+ 归属校验；
  仅 legacy 模式允许 account_id 凭证回滚（阶段 0 回退基线）。
- create_account：归属租户取自 principal.tenant_id（忽略客户端传入，§7.5）；
  legacy 模式回退 DEFAULT_TENANT_ID。
- GET /accounts（列表）：仅 JWT，返回当前用户可访问的账户（前端登录态自动拉取，§7.11）。
- 写接口 body 含 request_id，靠 (account_id, request_id) 唯一约束幂等；
- 写接口直连 paper.duckdb 不走 Redis；撮合后由撮合任务全量清缓存。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.auth import config as auth_config
from src.auth.dependencies import (audit_log, auth_error, get_current_principal,
                                   get_optional_principal, verify_account_access)
from src.auth.models import Principal
from src.paper_trading import service
from src.paper_trading import store as paper_store

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


class UpdateCostBody(BaseModel):
    cost_price: float = Field(..., gt=0)


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _legacy_account_exists(account_id: str) -> bool:
    """legacy account_id 凭证：仅校验账户存在（旧凭证即能力凭证）。"""
    with paper_store.connect() as conn:
        return conn.execute("SELECT 1 FROM accounts WHERE account_id = ?",
                            [account_id]).fetchone() is not None


def _authorize_read(account_id: str, principal: Principal | None, request: Request) -> None:
    """读操作鉴权：JWT → 归属校验；无 JWT（hybrid/legacy 允许）→ account_id 凭证 + DEPRECATED。"""
    if principal is not None:
        verify_account_access(account_id, principal, request)
        return
    if not _legacy_account_exists(account_id):
        raise auth_error(404, "ACCOUNT_NOT_FOUND", "账户不存在")
    audit_log("legacy_account_id_access", tenant_id=auth_config.DEFAULT_TENANT_ID,
              ip=_client_ip(request),
              detail=f"account_id={account_id}（DEPRECATED：请迁移至 JWT）")


def _authorize_write(account_id: str, principal: Principal | None, request: Request) -> None:
    """写操作鉴权：JWT + 角色 + 归属；仅 legacy 模式回退 account_id 凭证。

    hybrid/jwt 模式下 principal 为 None 只会发生在 LEGACY_ACCOUNT_ID_AUTH 允许
    （get_optional_principal 已放行），此处明确拒绝——写操作必须登录。
    """
    if principal is None:
        if auth_config.AUTH_MODE != "legacy":
            raise auth_error(401, "UNAUTHORIZED", "写操作需要登录")
        if not _legacy_account_exists(account_id):
            raise auth_error(404, "ACCOUNT_NOT_FOUND", "账户不存在")
        audit_log("legacy_account_id_write", tenant_id=auth_config.DEFAULT_TENANT_ID,
                  ip=_client_ip(request),
                  detail=f"account_id={account_id}（DEPRECATED：请迁移至 JWT）")
        return
    if not (set(principal.roles) & {"trader", "admin"}):
        raise auth_error(403, "FORBIDDEN", "需要 trader 或 admin 角色")
    verify_account_access(account_id, principal, request)


@router.get("/accounts")
def list_my_accounts(principal: Principal = Depends(get_current_principal)):
    """列出当前用户可访问的账户（登录态自动拉取，§7.11）。

    admin 或无 account_ids 白名单 → 租户下全部；否则仅白名单内的账户。
    """
    with paper_store.connect() as conn:
        if "admin" in principal.roles or not principal.account_ids:
            rows = conn.execute(
                "SELECT account_id, name, status FROM accounts"
                " WHERE tenant_id = ? ORDER BY created_at",
                [principal.tenant_id]).fetchall()
        else:
            marks = ",".join("?" * len(principal.account_ids))
            rows = conn.execute(
                "SELECT account_id, name, status FROM accounts"
                f" WHERE tenant_id = ? AND account_id IN ({marks}) ORDER BY created_at",
                [principal.tenant_id, *principal.account_ids]).fetchall()
    return {"items": [{"account_id": r[0], "name": r[1], "status": r[2]} for r in rows]}


@router.post("/accounts", status_code=201)
def create_account(body: CreateAccountBody, request: Request,
                   principal: Principal | None = Depends(get_optional_principal)):
    if principal is not None:
        tenant_id = principal.tenant_id
        audit_log("account_created", tenant_id=tenant_id, user_id=principal.user_id,
                  jti=principal.jti, ip=_client_ip(request))
    else:
        if auth_config.AUTH_MODE != "legacy":
            raise auth_error(401, "UNAUTHORIZED", "创建账户需要登录")
        tenant_id = auth_config.DEFAULT_TENANT_ID
        audit_log("legacy_account_created", tenant_id=tenant_id, ip=_client_ip(request),
                  detail="DEPRECATED：请迁移至 JWT")
    return _handle(service.create_account, tenant_id, body.name, body.init_cash)


@router.get("/accounts/{account_id}/overview")
def overview(account_id: str, request: Request,
             principal: Principal | None = Depends(get_optional_principal)):
    _authorize_read(account_id, principal, request)
    return _handle(service.get_overview, account_id)


@router.post("/accounts/{account_id}/reset")
def reset(account_id: str, body: ResetBody, request: Request,
          principal: Principal | None = Depends(get_optional_principal)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="重置为高危操作，请确认 confirm=true")
    _authorize_write(account_id, principal, request)
    if principal is not None:
        audit_log("account_reset", tenant_id=principal.tenant_id, user_id=principal.user_id,
                  jti=principal.jti, ip=_client_ip(request), detail=f"account_id={account_id}")
    reset_id = _handle(service.reset_account, account_id)
    return {"ok": True, "reset_id": reset_id}


@router.get("/accounts/{account_id}/positions")
def positions(account_id: str, request: Request,
              principal: Principal | None = Depends(get_optional_principal)):
    _authorize_read(account_id, principal, request)
    return {"items": _handle(service.list_positions, account_id)}


@router.patch("/accounts/{account_id}/positions/{code}")
def update_cost(account_id: str, code: str, body: UpdateCostBody, request: Request,
                principal: Principal | None = Depends(get_optional_principal)):
    _authorize_write(account_id, principal, request)
    return _handle(service.update_cost_price, account_id, code, body.cost_price)


@router.post("/accounts/{account_id}/orders", status_code=201)
def place_order(account_id: str, body: PlaceOrderBody, request: Request,
                principal: Principal | None = Depends(get_optional_principal)):
    _authorize_write(account_id, principal, request)
    return _handle(
        service.place_order, account_id, body.request_id, body.code,
        body.side, body.price_type, body.limit_price, body.qty,
    )


@router.delete("/accounts/{account_id}/orders/{order_id}")
def cancel_order(account_id: str, order_id: str, request: Request,
                 principal: Principal | None = Depends(get_optional_principal)):
    _authorize_write(account_id, principal, request)
    _handle(service.cancel_order, account_id, order_id)
    return {"ok": True}


@router.get("/accounts/{account_id}/orders")
def orders(
    account_id: str,
    request: Request,
    principal: Principal | None = Depends(get_optional_principal),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _authorize_read(account_id, principal, request)
    return _handle(service.list_orders, account_id, status, limit, offset)


@router.get("/accounts/{account_id}/fills")
def fills(
    account_id: str,
    request: Request,
    principal: Principal | None = Depends(get_optional_principal),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _authorize_read(account_id, principal, request)
    return _handle(service.list_fills, account_id, limit, offset)


@router.get("/accounts/{account_id}/cash-flows")
def cash_flows(
    account_id: str,
    request: Request,
    principal: Principal | None = Depends(get_optional_principal),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _authorize_read(account_id, principal, request)
    return _handle(service.list_cash_flows, account_id, limit, offset)


@router.get("/accounts/{account_id}/equity-curve")
def equity_curve(account_id: str, request: Request,
                 principal: Principal | None = Depends(get_optional_principal),
                 start: str | None = Query(None)):
    _authorize_read(account_id, principal, request)
    return _handle(service.equity_curve, account_id, start)


@router.get("/accounts/{account_id}/metrics")
def account_metrics(account_id: str, request: Request,
                    principal: Principal | None = Depends(get_optional_principal)):
    _authorize_read(account_id, principal, request)
    return _handle(service.account_metrics, account_id)
