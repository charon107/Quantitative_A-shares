"""个股相关接口：搜索、K线（含均线）、滚动波动率。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src import metrics
from src.api import schemas, services
from src.api.util import df_to_records

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/search", response_model=list[schemas.SearchRow])
def search(q: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=200)):
    return df_to_records(metrics.search_stocks(q, limit))


@router.get("/{code}/kline", response_model=schemas.StockKline)
def kline(code: str):
    try:
        df = metrics.load_stock_kline(code)
    except LookupError:
        raise HTTPException(status_code=404, detail=f"无数据：{code}")
    df = metrics.add_moving_averages(df)
    nm = services.name_map()
    return schemas.StockKline(code=code, code_name=nm.get(code), points=df_to_records(df))


@router.get("/{code}/volatility", response_model=list[schemas.VolatilityPoint])
def volatility(code: str, window: int = Query(20, ge=2, le=250)):
    try:
        frame = metrics.volatility_frame(code, window)
    except LookupError:
        raise HTTPException(status_code=404, detail=f"无数据：{code}")
    return df_to_records(frame)


@router.get("/{code}/info", response_model=schemas.CompanyInfo)
def company_info(code: str):
    df = metrics.company_info_df(code)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"无公司信息：{code}")
    rec = df_to_records(df, date_cols=())[0]
    return schemas.CompanyInfo(**rec)
