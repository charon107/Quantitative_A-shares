"""排行榜接口：涨幅/跌幅/成交额/换手率 Top N。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src import metrics
from src.api import schemas, services
from src.api.util import df_to_records

router = APIRouter(tags=["rankings"])

# 允许排序的指标与默认方向（True=升序，用于跌幅榜）
ALLOWED_METRICS = {"pctChg", "amount", "turn"}


@router.get("/rankings", response_model=list[schemas.RankingRow])
def rankings(
    metric: str = Query("pctChg"),
    n: int = Query(50, ge=1, le=200),
    ascending: bool = Query(False),
):
    if metric not in ALLOWED_METRICS:
        raise HTTPException(status_code=400, detail=f"不支持的指标：{metric}")
    df = services.latest_day()
    top = metrics.top_movers(df, n=n, metric=metric, ascending=ascending)
    if top.empty:
        return []
    nm = services.name_map()
    top = top.copy()
    top["code_name"] = top["code"].map(nm)
    cols = ["code", "code_name", "close", "pctChg", "amount", "turn"]
    top = top[[c for c in cols if c in top.columns]]
    return df_to_records(top)
