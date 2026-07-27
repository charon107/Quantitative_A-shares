// 与后端 src/api/schemas.py 对应的 TS 类型

export interface Breadth {
  up: number;
  down: number;
  flat: number;
  ratio: number | null;
  latest_date: string | null;
}

export interface IndexPoint {
  date: string;
  value: number | null;
}

export interface BreadthPoint {
  date: string;
  up: number;
  down: number;
  limit_up: number;
  limit_down: number;
}

export interface MoverRow {
  code: string;
  code_name: string | null;
  open: number | null;
  close: number | null;
  pctChg: number | null;
}

export interface DayMovers {
  date: string;
  up: MoverRow[];
  down: MoverRow[];
}

export interface KlinePoint {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  amount: number | null;
  pctChg: number | null;
  turn: number | null;
  MA5: number | null;
  MA10: number | null;
  MA20: number | null;
  MA60: number | null;
}

export interface StockKline {
  code: string;
  code_name: string | null;
  points: KlinePoint[];
}

export interface VolatilityPoint {
  date: string;
  value: number | null;
}

export interface RankingRow {
  code: string;
  code_name: string | null;
  close: number | null;
  pctChg: number | null;
  amount: number | null;
  turn: number | null;
}

export interface SearchRow {
  code: string;
  code_name: string | null;
}

export interface QuoteRow {
  code: string;
  code_name: string | null;
  date: string | null;
  close: number | null;
  pctChg: number | null;
}

export interface HotStock {
  code: string;
  code_name: string | null;
  rank_no: number | null;
  current_price: number | null;
  pct_change: number | null;
  hot: number | null;
  concept: string | null;
  rank_reason: string | null;
  trade_date: string | null;
}

export interface CompanyInfo {
  code: string;
  code_name: string | null;
  fullname: string | null;
  area: string | null;
  industry: string | null;
  market: string | null;
  list_date: string | null;
  chairman: string | null;
  manager: string | null;
  secretary: string | null;
  reg_capital: number | null;
  setup_date: string | null;
  province: string | null;
  city: string | null;
  employees: number | null;
  website: string | null;
  email: string | null;
  office: string | null;
  main_business: string | null;
  introduction: string | null;
  business_scope: string | null;
}

export interface DurationSample {
  code: string;
  code_name: string | null;
  start_date: string;
  end_date: string;
  duration: number;
  ongoing: boolean;
}

export interface MaDuration {
  summary: {
    total: number;
    ongoing: number;
    closed: number;
    median: number | null;
    p90: number | null;
    max: number | null;
  };
  samples: DurationSample[];
}

export interface Status {
  latest_date: string | null;
  n_codes: number;
  n_rows: number;
  redis_available: boolean;
  start_date: string;
}

export type RankMetric = "pctChg" | "amount" | "turn";

export interface ScreeningRow {
  code: string;
  code_name: string | null;
  roe: number | null;
  netprofit_yoy: number | null;
  debt_ratio: number | null;
  net_profit: number | null;
  cfo_np_ratio: number | null;
}

// 季度基本面单期。比率为小数、金额为元；q_ 前缀为单季口径（差分预计算，Q1=累计）
export interface QuarterlyFundamentalPoint {
  end_date: string;
  year: number;
  quarter: number;
  ann_date: string | null;
  roe: number | null;
  roe_dt: number | null;
  roa: number | null;
  netprofit_margin: number | null;
  grossprofit_margin: number | null;
  net_profit: number | null;
  profit_dedt: number | null;
  revenue: number | null;
  eps: number | null;
  bps: number | null;
  debt_to_assets: number | null;
  or_yoy: number | null;
  netprofit_yoy: number | null;
  dt_netprofit_yoy: number | null;
  cfo: number | null;
  total_assets: number | null;
  total_liab: number | null;
  total_debt: number | null;
  q_roe: number | null;
  q_dt_roe: number | null;
  q_netprofit_margin: number | null;
  q_gsprofit_margin: number | null;
  q_net_profit: number | null;
  q_revenue: number | null;
  q_cfo: number | null;
  q_sales_yoy: number | null;
  q_sales_qoq: number | null;
  q_netprofit_yoy: number | null;
  q_netprofit_qoq: number | null;
}

export interface QuarterlyFundamental {
  code: string;
  code_name: string | null;
  points: QuarterlyFundamentalPoint[];
}

// 估值日频单日。total_mv/circ_mv 万元、dv_* 百分数（tushare 原单位）
export interface ValuationPoint {
  date: string;
  pe: number | null;
  pe_ttm: number | null;
  pb: number | null;
  ps: number | null;
  ps_ttm: number | null;
  dv_ratio: number | null;
  dv_ttm: number | null;
  total_mv: number | null;
  circ_mv: number | null;
}

export interface Valuation {
  code: string;
  code_name: string | null;
  points: ValuationPoint[];
}

// 分红送股（已实施）。stk_div 每股送转股；cash_div 税后/cash_div_tax 税前 每股分红元
export interface DividendRow {
  end_date: string;
  ann_date: string | null;
  stk_div: number | null;
  cash_div: number | null;
  cash_div_tax: number | null;
  record_date: string | null;
  ex_date: string | null;
  pay_date: string | null;
}

// 业绩预告。p_change 为小数；net_profit_min/max 元
export interface ForecastRow {
  end_date: string;
  ann_date: string;
  type: string | null;
  p_change_min: number | null;
  p_change_max: number | null;
  net_profit_min: number | null;
  net_profit_max: number | null;
  change_reason: string | null;
}

// 业绩快报。金额元；diluted_roe/yoy_* 为小数
export interface ExpressRow {
  end_date: string;
  ann_date: string | null;
  revenue: number | null;
  operate_profit: number | null;
  n_income: number | null;
  diluted_eps: number | null;
  bps: number | null;
  diluted_roe: number | null;
  yoy_sales: number | null;
  yoy_dedu_np: number | null;
}

export interface EarningsNews {
  code: string;
  code_name: string | null;
  forecasts: ForecastRow[];
  express: ExpressRow[];
}

export interface ChartLinePoint {
  date: string;
  close: number | null;
  ma: number | null;
}

export interface ScreeningChart {
  code: string;
  code_name: string | null;
  next_pub: string | null;
  pub_dates: string[];
  stock: ChartLinePoint[];
  index: ChartLinePoint[];
}

// ========== 财报日历 ==========

// 某公告日三类披露家数（按 distinct code 计）
export interface EarningsCalendarDayCount {
  date: string;
  report_count: number;
  express_count: number;
  forecast_count: number;
}

export interface EarningsCalendarDates {
  latest_date: string | null;
  days: EarningsCalendarDayCount[];
}

// 正式财报摘要（单季口径，金额为元，同比为小数）
export interface ReportSummary {
  end_date: string;
  year: number | null;
  quarter: number | null;
  q_revenue: number | null;
  q_net_profit: number | null;
  q_sales_yoy: number | null;
  q_netprofit_yoy: number | null;
}

// 业绩快报摘要（金额为元，同比为小数）
export interface ExpressSummary {
  end_date: string;
  revenue: number | null;
  n_income: number | null;
  yoy_sales: number | null;
  yoy_dedu_np: number | null;
}

// 业绩预告摘要（幅度为小数，金额为元）
export interface ForecastSummary {
  end_date: string;
  type: string | null;
  p_change_min: number | null;
  p_change_max: number | null;
  net_profit_min: number | null;
  net_profit_max: number | null;
}

// 某公告日一只股票的披露聚合（同日多类型合并为一行）
export interface EarningsCalendarRow {
  code: string;
  code_name: string | null;
  report: ReportSummary | null;
  express: ExpressSummary | null;
  forecast: ForecastSummary | null;
}

export interface EarningsCalendarDay {
  date: string;
  rows: EarningsCalendarRow[];
}

// ========== 模拟盘（docs/paper-trading-design.md §4） ==========

export interface PaperAccount {
  account_id: string;
  name: string;
  init_cash: number;
  cash: number;
  frozen: number;
  created_at: string;
}

export interface PaperOverview {
  account_id: string;
  name: string;
  init_cash: number;
  cash: number;
  frozen: number;
  market_value: number;
  total_asset: number;
  total_pnl: number;
  total_return_pct: number;
  position_count: number;
  asof_date: string | null;
}

export interface PaperPosition {
  code: string;
  name: string | null;
  qty: number;
  sellable_qty: number;
  cost_price: number;
  last_close: number | null;
  market_value: number;
  pnl: number;
  pnl_pct: number;
}

export type PaperOrderSide = "buy" | "sell";
export type PaperPriceType = "market" | "limit";
export type PaperOrderStatus = "pending" | "filled" | "cancelled" | "expired" | "rejected";

export interface PaperOrder {
  order_id: string;
  account_id: string;
  request_id: string;
  code: string;
  code_name: string | null;
  side: PaperOrderSide;
  price_type: PaperPriceType;
  limit_price: number | null;
  qty: number;
  status: PaperOrderStatus;
  reject_reason: string | null;
  ref_price: number | null;
  frozen_amount: number | null;
  created_at: string;
  updated_at: string;
}

export interface PaperFill {
  fill_id: string;
  order_id: string;
  account_id: string;
  code: string;
  code_name: string | null;
  side: PaperOrderSide;
  price: number;
  qty: number;
  amount: number;
  commission: number;
  stamp_tax: number;
  fee: number;
  trade_date: string;
  created_at: string;
}

export type PaperCashFlowType = "freeze" | "unfreeze" | "buy" | "sell" | "reset";

export interface PaperCashFlow {
  flow_id: string;
  account_id: string;
  type: PaperCashFlowType;
  amount: number;
  balance_after: number;
  ref_id: string | null;
  created_at: string;
}

export interface PaperEquityCurvePoint {
  date: string;
  total_asset: number;
  return_pct: number;
}

export interface PaperEquityCurve {
  curve: PaperEquityCurvePoint[];
  benchmark: IndexPoint[];
}

// win_rate 为小数比率（0-1），其余 *_pct 为百分数
export interface PaperMetrics {
  total_return_pct: number | null;
  annualized_return_pct: number | null;
  max_drawdown_pct: number | null;
  win_rate: number | null;
}

export interface PaperList<T> {
  items: T[];
  total: number;
}
