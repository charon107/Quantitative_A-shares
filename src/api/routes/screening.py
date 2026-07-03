"""基本面选股接口（逐年回测池）：年份列表、某年选股结果、个股复刻股价图。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src import metrics
from src.api import schemas, services
from src.api.util import df_to_records

router = APIRouter(prefix="/screening", tags=["screening"])


@router.get("/years", response_model=list[int])
def years():
    return services.selection_years()


@router.get("/{year}", response_model=list[schemas.ScreeningRow])
def by_year(year: int):
    return df_to_records(services.selected_by_year(year), date_cols=())


@router.get("/{year}/{code}/chart", response_model=schemas.ScreeningChart)
def chart(year: int, code: str):
    data = metrics.screening_chart(year, code)
    if not data["stock"].shape[0]:
        raise HTTPException(status_code=404, detail=f"无价格数据：{code} @ {year}")
    return schemas.ScreeningChart(
        code=data["code"],
        code_name=data["code_name"],
        next_pub=data["next_pub"],
        pub_dates=data["pub_dates"],
        stock=df_to_records(data["stock"]),
        index=df_to_records(data["index"]),
    )
