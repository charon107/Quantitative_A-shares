"""只读 SQL 网关：本地 Python 远程查询服务器 DuckDB（配套客户端 scripts/remote_query.py）。

POST /api/sql，body {"sql": "..."}，返回 Arrow IPC stream（pandas/duckdb/polars
可零拷贝读取，体积远小于 JSON）。

鉴权（规范书 §7.8 双模式，M6）：
- ``AUTH_MODE=legacy``：仅静态 ``SQL_API_TOKEN``（现状，常数时间比较）。
- ``AUTH_MODE=jwt``：仅 JWT 且 scope 含 ``sql:read``（§7.8），不接受静态 token。
- ``AUTH_MODE=hybrid``：优先 JWT（scope 含 sql:read），失败回退静态 token。

安全：
  - token 未配置且 legacy 模式 → 端点 404（安全默认关闭）；JWT/hybrid 模式仍可走 JWT。
  - 只读连接（防改库）+ enable_external_access=false（防 COPY TO 文件 / ATTACH /
    read_* 等触碰服务器文件系统——只读模式防不了这些）+ lock_configuration=true
    （防 SQL 内 SET 改回配置或抬高 memory_limit）。
  - db._apply_pragmas 的 memory_limit（默认 400MB）兜底大查询，超限报错而非拖垮服务器。
  - SQL_MAX_ROWS 行数上限，超出截断并以 X-Truncated 响应头标注。
"""
from __future__ import annotations

import io
import secrets

import duckdb
import pyarrow as pa
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel

from src import config, db
from src.auth import config as auth_config
from src.auth import dependencies
from src.auth.dependencies import auth_error

router = APIRouter(tags=["sql"])

_BATCH_ROWS = 65536


class SqlQuery(BaseModel):
    sql: str


def _authorize_sql(request: Request, authorization: str) -> None:
    """SQL 网关鉴权（§7.8）。未通过一律抛 HTTPException。

    - 静态 token 用 ``secrets.compare_digest`` 常数时间比较（§10.3.6）。
    - jwt 模式：JWT 失败即拒绝（不回退静态 token，§2.3 阶段 3 语义）。
    """
    token = authorization.removeprefix("Bearer ").strip()

    # 1) JWT 路径（hybrid / jwt 模式优先）
    if auth_config.AUTH_MODE in ("hybrid", "jwt") and token:
        try:
            principal = dependencies.get_current_principal(request, authorization)
        except HTTPException:
            if auth_config.AUTH_MODE == "jwt":
                raise  # jwt 模式：JWT 失败即拒绝
        else:
            if "sql:read" in principal.scope:
                return
            raise auth_error(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "scope 缺少 sql:read")

    # 2) 静态 token 路径（legacy 全量 / hybrid 回退）
    if config.SQL_API_TOKEN and token and secrets.compare_digest(token, config.SQL_API_TOKEN):
        return

    # 3) 兜底拒绝
    if auth_config.AUTH_MODE == "legacy" and not config.SQL_API_TOKEN:
        raise HTTPException(status_code=404, detail="Not Found")  # 未配置即关闭
    if not token:
        raise HTTPException(status_code=401, detail="缺少 Authorization 头")
    raise HTTPException(status_code=403, detail="token 无效")


@router.post("/sql")
def run_sql(q: SqlQuery, request: Request, authorization: str = Header(default="")):
    _authorize_sql(request, authorization)

    try:
        with db.connect(read_only=True) as conn:
            conn.execute("SET enable_external_access=false")  # 禁外部文件/网络（不可再开启）
            conn.execute("SET lock_configuration=true")  # 锁定配置，防 SET 抬 memory_limit
            reader = conn.execute(q.sql).fetch_record_batch(_BATCH_ROWS)

            buf = io.BytesIO()
            total = 0
            truncated = False
            with pa.ipc.new_stream(buf, reader.schema) as writer:
                for batch in reader:
                    if total + batch.num_rows > config.SQL_MAX_ROWS:
                        batch = batch.slice(0, config.SQL_MAX_ROWS - total)
                        truncated = True
                    writer.write_batch(batch)
                    total += batch.num_rows
                    if truncated:
                        break
    except (duckdb.Error, pa.ArrowInvalid, RuntimeError) as e:
        # 语法错/权限拒绝/内存超限等统一 400 带原始信息，便于本地调试 SQL
        raise HTTPException(status_code=400, detail=str(e))

    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.apache.arrow.stream",
        headers={"X-Rows": str(total), "X-Truncated": "true" if truncated else "false"},
    )
