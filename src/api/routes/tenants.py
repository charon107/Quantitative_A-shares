"""租户管理路由（规范书 §3.3 / §6.3 管理级，M3 最小可用集）。

- POST /api/tenants                          创建租户（admin 角色）
- POST /api/tenants/{tenant_id}/users        在租户下创建用户（本租户 admin）
- POST /api/tenants/{tenant_id}/grants       授权用户访问租户内账户（本租户 admin）

规范未定义「超管」角色，M3 取舍：凡 admin 角色均可创建租户（首个 admin 由迁移
脚本 scripts/migrate_add_tenant.py 引导创建）；用户管理与授权限定操作者
``principal.tenant_id == 路径 tenant_id``，租户管理员只能管本租户。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.auth import config, password, store
from src.auth.dependencies import audit_log, require_tenant_admin, verify_account_access
from src.auth.models import GrantIn, Principal, TenantCreateIn, TenantOut, UserCreateIn, UserOut

router = APIRouter(tags=["tenants"])

_VALID_ROLES = {"admin", "trader", "viewer"}  # §5.1


@router.post("/tenants", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(body: TenantCreateIn, request: Request,
                  principal: Principal = Depends(require_tenant_admin)):
    tenant = store.create_tenant(body.tenant_id, body.name)
    audit_log("tenant_created", tenant_id=tenant["tenant_id"], user_id=principal.user_id,
              jti=principal.jti, ip=request.client.host if request.client else "")
    return tenant


def _require_same_tenant(tenant_id: str, principal: Principal) -> None:
    if tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只能管理本租户")


@router.post("/tenants/{tenant_id}/users", response_model=UserOut,
             status_code=status.HTTP_201_CREATED)
def create_user(tenant_id: str, body: UserCreateIn,
                principal: Principal = Depends(require_tenant_admin)):
    _require_same_tenant(tenant_id, principal)
    if store.get_tenant(tenant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="租户不存在")
    if len(body.password) < config.PASSWORD_MIN_LEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"密码长度至少 {config.PASSWORD_MIN_LEN} 位")
    bad_roles = set(body.roles) - _VALID_ROLES
    if bad_roles:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"非法角色: {sorted(bad_roles)}")
    try:
        user = store.create_user(tenant_id, body.username,
                                 password.hash_password(body.password), body.roles)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    return user


@router.post("/tenants/{tenant_id}/grants", status_code=status.HTTP_201_CREATED)
def grant_account(tenant_id: str, body: GrantIn, request: Request,
                  principal: Principal = Depends(require_tenant_admin)):
    _require_same_tenant(tenant_id, principal)
    user = store.get_user_by_id(body.user_id)
    if user is None or user["tenant_id"] != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在或不属于本租户")
    # 复用集中的归属校验（§5.4.3）：账户必须属于当前租户
    verify_account_access(body.account_id, principal, request)
    store.grant_account(body.user_id, body.account_id)
    return {"status": "ok", "user_id": body.user_id, "account_id": body.account_id}
