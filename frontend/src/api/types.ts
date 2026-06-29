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

export interface LimitPoint {
  date: string;
  limit_up: number;
  limit_down: number;
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
