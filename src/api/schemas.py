"""API 响应模型（Pydantic）。字段尽量与前端消费一致，可空字段统一 Optional。"""
from __future__ import annotations

from pydantic import BaseModel


class Breadth(BaseModel):
    up: int
    down: int
    flat: int
    ratio: float | None
    latest_date: str | None


class IndexPoint(BaseModel):
    date: str
    value: float | None


class LimitPoint(BaseModel):
    date: str
    limit_up: int
    limit_down: int


class BreadthPoint(BaseModel):
    date: str
    up: int
    down: int
    limit_up: int
    limit_down: int


class MoverRow(BaseModel):
    code: str
    code_name: str | None
    open: float | None
    close: float | None
    pctChg: float | None


class DayMovers(BaseModel):
    date: str
    up: list[MoverRow]
    down: list[MoverRow]


class KlinePoint(BaseModel):
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    amount: float | None
    pctChg: float | None
    turn: float | None
    MA5: float | None = None
    MA10: float | None = None
    MA20: float | None = None
    MA60: float | None = None


class StockKline(BaseModel):
    code: str
    code_name: str | None
    points: list[KlinePoint]


class VolatilityPoint(BaseModel):
    date: str
    value: float | None


class RankingRow(BaseModel):
    code: str
    code_name: str | None
    close: float | None
    pctChg: float | None
    amount: float | None
    turn: float | None


class SearchRow(BaseModel):
    code: str
    code_name: str | None


class CompanyInfo(BaseModel):
    code: str
    code_name: str | None = None
    fullname: str | None = None
    area: str | None = None
    industry: str | None = None
    market: str | None = None
    list_date: str | None = None
    chairman: str | None = None
    manager: str | None = None
    secretary: str | None = None
    reg_capital: float | None = None
    setup_date: str | None = None
    province: str | None = None
    city: str | None = None
    employees: int | None = None
    website: str | None = None
    email: str | None = None
    office: str | None = None
    main_business: str | None = None
    introduction: str | None = None
    business_scope: str | None = None


class DurationSample(BaseModel):
    code: str
    code_name: str | None = None
    start_date: str
    end_date: str
    duration: int
    ongoing: bool


class DurationSummary(BaseModel):
    total: int
    ongoing: int
    closed: int
    median: float | None
    p90: float | None
    max: int | None


class MaDuration(BaseModel):
    summary: DurationSummary
    samples: list[DurationSample]


class Status(BaseModel):
    latest_date: str | None
    n_codes: int
    n_rows: int
    redis_available: bool
    start_date: str
