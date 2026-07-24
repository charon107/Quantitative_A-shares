"""财报日历：按公告日（ann_date）聚合正式财报/业绩快报/业绩预告。

直连 DuckDB 不走 Redis（按日期主键过滤毫秒级，客户端 react-query 缓存即可，
与个股财务接口的既有决策一致，见 stocks.py）。三表任一缺失时对应部分返回空，
不抛 500——老库可能尚未建财务扩展表。
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Query

from src import metrics
from src.api import schemas
from src.api.util import df_to_records

router = APIRouter(prefix="/earnings-calendar", tags=["earnings-calendar"])

_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
_MONTH_RE = r"^\d{4}-\d{2}$"


@router.get("/dates", response_model=schemas.EarningsCalendarDates)
def dates(month: str | None = Query(None, pattern=_MONTH_RE)):
    """各公告日的披露家数（日历角标）。month='YYYY-MM' 限定月份；缺省返回最近 120 天。"""
    df = metrics.earnings_calendar_days(month)
    if df.empty:
        return schemas.EarningsCalendarDates(latest_date=None, days=[])
    days = [schemas.EarningsCalendarDayCount(**r) for r in df_to_records(df, date_cols=())]
    # df 按日期降序，首行即最近披露日；month 模式下 latest_date 同样取首行（供翻月初始定位）
    return schemas.EarningsCalendarDates(latest_date=days[0].date, days=days)


@router.get("", response_model=schemas.EarningsCalendarDay)
def day(date: str = Query(..., pattern=_DATE_RE)):
    """某公告日披露列表：三表明细按 code 聚合成行（同日多类型合并）。"""
    frames = metrics.earnings_calendar_day(date)

    def _summary_map(df: pd.DataFrame, model: type) -> dict[str, dict]:
        if df.empty:
            return {}
        recs = df_to_records(df, date_cols=())
        # 同一 code 同日可能多行（不同报告期），保留报告期最新的一行
        best: dict[str, dict] = {}
        for r in recs:
            code = r.pop("code")
            r.pop("code_name", None)
            if code not in best or (r.get("end_date") or "") > (best[code].get("end_date") or ""):
                best[code] = r
        return {code: model(**r).model_dump() for code, r in best.items()}

    reports = _summary_map(frames["report"], schemas.ReportSummary)
    expresses = _summary_map(frames["express"], schemas.ExpressSummary)
    forecasts = _summary_map(frames["forecast"], schemas.ForecastSummary)

    # 名称映射：任一帧带出的 code_name 优先，缺失再回退 stock_meta 全量映射
    names: dict[str, str | None] = {}
    for df in frames.values():
        if not df.empty and "code_name" in df.columns:
            for code, nm in zip(df["code"], df["code_name"]):
                if pd.notna(nm):
                    names.setdefault(code, nm)
    missing = (set(reports) | set(expresses) | set(forecasts)) - set(names)
    if missing:
        nm = metrics.name_map()
        for code in missing:
            names[code] = nm.get(code)

    rows = []
    for code in sorted(set(reports) | set(expresses) | set(forecasts)):
        rows.append(
            schemas.EarningsCalendarRow(
                code=code,
                code_name=names.get(code),
                report=reports.get(code),
                express=expresses.get(code),
                forecast=forecasts.get(code),
            )
        )
    # 排序：报告期新的在前（取该行各类型最大 end_date），同报告期按代码
    def _sort_key(row: schemas.EarningsCalendarRow):
        end = max(
            (s.end_date for s in (row.report, row.express, row.forecast) if s is not None),
            default="",
        )
        return (end, row.code)

    rows.sort(key=_sort_key, reverse=True)
    return schemas.EarningsCalendarDay(date=date, rows=rows)
