"""大盘相关接口：市场宽度、等权指数、涨停/跌停走势。"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter

from src import metrics
from src.api import schemas, services
from src.api.util import df_to_records, safe_ratio, series_to_points

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/breadth", response_model=schemas.Breadth)
def breadth():
    df = services.latest_day()
    b = metrics.market_breadth(df)
    latest_date = None
    if not df.empty and "date" in df.columns:
        latest_date = pd.to_datetime(df["date"]).max().strftime("%Y-%m-%d")
    return schemas.Breadth(
        up=b["up"], down=b["down"], flat=b["flat"],
        ratio=safe_ratio(b["ratio"]), latest_date=latest_date,
    )


@router.get("/equal-weight-index", response_model=list[schemas.IndexPoint])
def equal_weight_index(start: str = services.DEFAULT_START):
    return series_to_points(services.equal_weight_index(start))


@router.get("/limit-up-down", response_model=list[schemas.LimitPoint])
def limit_up_down():
    return df_to_records(services.limit_up_down())
