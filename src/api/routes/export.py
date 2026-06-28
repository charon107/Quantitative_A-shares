"""数据导出接口：把 DuckDB 的全量 K线导出为 Parquet 供下载。"""
from __future__ import annotations

import os
import tempfile
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from src import db

router = APIRouter(tags=["export"])


@router.get("/export/kline.parquet")
def export_kline_parquet():
    """导出 kline 全表为 Parquet（DuckDB COPY 写临时文件后流式返回，下载完即删）。"""
    if not db.database_exists():
        raise HTTPException(status_code=404, detail="数据库不存在")

    tmp = os.path.join(tempfile.gettempdir(), f"market_kline_{uuid.uuid4().hex}.parquet")
    safe = tmp.replace("'", "''")
    try:
        with db.connect(read_only=True) as conn:
            conn.execute(
                f"COPY (SELECT * FROM kline ORDER BY code, date) TO '{safe}' (FORMAT PARQUET)"
            )
    except Exception as e:  # 导出失败时清理
        if os.path.exists(tmp):
            os.remove(tmp)
        raise HTTPException(status_code=500, detail=f"导出失败：{e}")

    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        filename="market_kline.parquet",
        background=BackgroundTask(lambda: os.path.exists(tmp) and os.remove(tmp)),
    )
