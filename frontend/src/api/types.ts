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
