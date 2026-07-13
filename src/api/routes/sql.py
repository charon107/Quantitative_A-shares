"""只读 SQL 网关：本地 Python 远程查询服务器 DuckDB（配套客户端 scripts/remote_query.py）。

POST /api/sql，body {"sql": "..."}，请求头 Authorization: Bearer <SQL_API_TOKEN>，
返回 Arrow IPC stream（pandas/duckdb/polars 可零拷贝读取，体积远小于 JSON）。

安全：
  - token 未配置时端点 404（安全默认关闭）；校验用常数时间比较。
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
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from src import config, db

router = APIRouter(tags=["sql"])

_BATCH_ROWS = 65536


class SqlQuery(BaseModel):
    sql: str


@router.post("/sql")
def run_sql(q: SqlQuery, authorization: str = Header(default="")):
    if not config.SQL_API_TOKEN:
        raise HTTPException(status_code=404, detail="Not Found")  # 未配置 token 即关闭
    token = authorization.removeprefix("Bearer ").strip()
    if not token or not secrets.compare_digest(token, config.SQL_API_TOKEN):
        raise HTTPException(status_code=403, detail="token 无效")

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
