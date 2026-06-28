"""分析接口：MA5>MA20 多头时长分布、数据状态。"""
from __future__ import annotations

from fastapi import APIRouter

from src import cache, metrics
from src.api import schemas, services

router = APIRouter(tags=["analytics"])


@router.get("/ma-duration", response_model=schemas.MaDuration)
def ma_duration():
    df = services.ma_duration()
    if df.empty:
        summary = schemas.DurationSummary(total=0, ongoing=0, closed=0, median=None, p90=None, max=None)
        return schemas.MaDuration(summary=summary, samples=[])

    nm = services.name_map()
    durations = df["duration"]
    n_total = len(df)
    n_ongoing = int(df["ongoing"].sum())
    summary = schemas.DurationSummary(
        total=n_total,
        ongoing=n_ongoing,
        closed=n_total - n_ongoing,
        median=float(durations.median()),
        p90=float(durations.quantile(0.90)),
        max=int(durations.max()),
    )
    samples = [
        schemas.DurationSample(
            code=r["code"],
            code_name=nm.get(r["code"]),
            start_date=str(r["start_date"]),
            end_date=str(r["end_date"]),
            duration=int(r["duration"]),
            ongoing=bool(r["ongoing"]),
        )
        for r in df.to_dict("records")
    ]
    return schemas.MaDuration(summary=summary, samples=samples)


@router.get("/status", response_model=schemas.Status)
def status():
    st = metrics.data_status()
    return schemas.Status(
        latest_date=st["latest_date"],
        n_codes=st["n_codes"],
        n_rows=st["n_rows"],
        redis_available=cache.is_available(),
        start_date=services.DEFAULT_START,
    )
