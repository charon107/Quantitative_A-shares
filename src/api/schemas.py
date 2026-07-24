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


class QuoteRow(BaseModel):
    """单只股票最新一行行情快照（收藏列表用）。停牌/退市股为最后已知行情。"""
    code: str
    code_name: str | None = None
    date: str | None = None
    close: float | None = None
    pctChg: float | None = None


class HotStock(BaseModel):
    code: str
    code_name: str | None = None
    rank_no: int | None = None
    current_price: float | None = None
    pct_change: float | None = None
    hot: float | None = None
    concept: str | None = None
    rank_reason: str | None = None
    trade_date: str | None = None


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


class ScreeningRow(BaseModel):
    code: str
    code_name: str | None = None
    roe: float | None = None
    netprofit_yoy: float | None = None
    debt_ratio: float | None = None
    net_profit: float | None = None
    cfo_np_ratio: float | None = None


class ChartLinePoint(BaseModel):
    date: str
    close: float | None = None
    ma: float | None = None


class ScreeningChart(BaseModel):
    code: str
    code_name: str | None = None
    next_pub: str | None = None
    pub_dates: list[str]
    stock: list[ChartLinePoint]
    index: list[ChartLinePoint]


class QuarterlyFundamentalPoint(BaseModel):
    """季度基本面单期。比率为小数、金额为元；q_ 前缀为单季口径（差分预计算，Q1=累计）。"""
    end_date: str
    year: int
    quarter: int
    ann_date: str | None = None
    roe: float | None = None
    roe_dt: float | None = None
    roa: float | None = None
    netprofit_margin: float | None = None
    grossprofit_margin: float | None = None
    net_profit: float | None = None
    profit_dedt: float | None = None
    revenue: float | None = None
    eps: float | None = None
    bps: float | None = None
    debt_to_assets: float | None = None
    or_yoy: float | None = None
    netprofit_yoy: float | None = None
    dt_netprofit_yoy: float | None = None
    cfo: float | None = None
    total_assets: float | None = None
    total_liab: float | None = None
    total_debt: float | None = None
    q_roe: float | None = None
    q_dt_roe: float | None = None
    q_netprofit_margin: float | None = None
    q_gsprofit_margin: float | None = None
    q_net_profit: float | None = None
    q_revenue: float | None = None
    q_cfo: float | None = None
    q_sales_yoy: float | None = None
    q_sales_qoq: float | None = None
    q_netprofit_yoy: float | None = None
    q_netprofit_qoq: float | None = None


class QuarterlyFundamental(BaseModel):
    code: str
    code_name: str | None = None
    points: list[QuarterlyFundamentalPoint]


class ValuationPoint(BaseModel):
    """估值日频单日。total_mv/circ_mv 万元、dv_* 百分数（tushare 原单位）。"""
    date: str
    pe: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    ps: float | None = None
    ps_ttm: float | None = None
    dv_ratio: float | None = None
    dv_ttm: float | None = None
    total_mv: float | None = None
    circ_mv: float | None = None


class Valuation(BaseModel):
    code: str
    code_name: str | None = None
    points: list[ValuationPoint]


class DividendRow(BaseModel):
    """分红送股（已实施）。stk_div 每股送转股；cash_div 税后/cash_div_tax 税前 每股分红元。"""
    end_date: str
    ann_date: str | None = None
    stk_div: float | None = None
    cash_div: float | None = None
    cash_div_tax: float | None = None
    record_date: str | None = None
    ex_date: str | None = None
    pay_date: str | None = None


class ForecastRow(BaseModel):
    """业绩预告。p_change 为小数；net_profit_min/max 元。"""
    end_date: str
    ann_date: str
    type: str | None = None
    p_change_min: float | None = None
    p_change_max: float | None = None
    net_profit_min: float | None = None
    net_profit_max: float | None = None
    change_reason: str | None = None


class ExpressRow(BaseModel):
    """业绩快报。金额元；diluted_roe/yoy_* 为小数。"""
    end_date: str
    ann_date: str | None = None
    revenue: float | None = None
    operate_profit: float | None = None
    n_income: float | None = None
    diluted_eps: float | None = None
    bps: float | None = None
    diluted_roe: float | None = None
    yoy_sales: float | None = None
    yoy_dedu_np: float | None = None


class EarningsNews(BaseModel):
    """业绩动态：预告 + 快报（均早于正式财报）。"""
    code: str
    code_name: str | None = None
    forecasts: list[ForecastRow]
    express: list[ExpressRow]


class Status(BaseModel):
    latest_date: str | None
    n_codes: int
    n_rows: int
    redis_available: bool
    start_date: str


# ========== 财报日历 ==========
class EarningsCalendarDayCount(BaseModel):
    """某公告日三类披露家数（按 distinct code 计）。"""
    date: str
    report_count: int
    express_count: int
    forecast_count: int


class EarningsCalendarDates(BaseModel):
    latest_date: str | None  # 最近有披露的日期（无数据为 None）
    days: list[EarningsCalendarDayCount]


class ReportSummary(BaseModel):
    """正式财报摘要（单季口径，金额为元，同比为小数）。"""
    end_date: str
    year: int | None = None
    quarter: int | None = None
    q_revenue: float | None = None
    q_net_profit: float | None = None
    q_sales_yoy: float | None = None
    q_netprofit_yoy: float | None = None


class ExpressSummary(BaseModel):
    """业绩快报摘要（金额为元，同比为小数）。"""
    end_date: str
    revenue: float | None = None
    n_income: float | None = None
    yoy_sales: float | None = None
    yoy_dedu_np: float | None = None


class ForecastSummary(BaseModel):
    """业绩预告摘要（幅度为小数，金额为元）。"""
    end_date: str
    type: str | None = None
    p_change_min: float | None = None
    p_change_max: float | None = None
    net_profit_min: float | None = None
    net_profit_max: float | None = None


class EarningsCalendarRow(BaseModel):
    """某公告日一只股票的披露聚合（同日多类型合并为一行）。"""
    code: str
    code_name: str | None = None
    report: ReportSummary | None = None
    express: ExpressSummary | None = None
    forecast: ForecastSummary | None = None


class EarningsCalendarDay(BaseModel):
    date: str
    rows: list[EarningsCalendarRow]
