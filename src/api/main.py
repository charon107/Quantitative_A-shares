"""FastAPI 应用入口。

- `/api/*`：JSON 接口（看板数据）
- `/`：托管前端构建产物 `frontend/dist`（存在时）。开发期前端跑 Vite dev server，
  此处仅提供 API；生产期由本进程同时托管静态文件与 API（单进程单端口）。

启动：
    uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8501
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.routes import analytics, export, market, rankings, stocks

DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(title="A股股价看板 API", version="2.0.0")

# 开发期允许 Vite dev server 跨域；生产期同源，CORS 无害
_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# API 路由统一挂在 /api 下
app.include_router(market.router, prefix="/api")
app.include_router(stocks.router, prefix="/api")
app.include_router(rankings.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(export.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 静态前端（构建产物存在时）。务必在 API 路由之后挂载根路径。
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="frontend")
