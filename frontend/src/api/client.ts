import { useQuery } from "@tanstack/react-query";
import type {
  Breadth,
  BreadthPoint,
  CompanyInfo,
  DayMovers,
  DividendRow,
  EarningsNews,
  HotStock,
  IndexPoint,
  MaDuration,
  QuarterlyFundamental,
  RankMetric,
  RankingRow,
  ScreeningChart,
  ScreeningRow,
  SearchRow,
  Status,
  StockKline,
  Valuation,
  VolatilityPoint,
} from "./types";

const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${path} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const useBreadth = () =>
  useQuery({ queryKey: ["breadth"], queryFn: () => get<Breadth>("/market/breadth") });

export const useEqualWeightIndex = (start: string) =>
  useQuery({
    queryKey: ["ewi", start],
    queryFn: () => get<IndexPoint[]>(`/market/equal-weight-index?start=${start}`),
  });

export const useShanghaiEqualWeightIndex = (start: string) =>
  useQuery({
    queryKey: ["shewi", start],
    queryFn: () => get<IndexPoint[]>(`/market/shanghai-equal-weight-index?start=${start}`),
  });

export const useBreadthSeries = () =>
  useQuery({ queryKey: ["breadthSeries"], queryFn: () => get<BreadthPoint[]>("/market/breadth-series") });

export const useHotStocks = () =>
  useQuery({ queryKey: ["hotStocks"], queryFn: () => get<HotStock[]>("/market/hot-stocks") });

export const useDayMovers = (date: string | null) =>
  useQuery({
    queryKey: ["dayMovers", date],
    queryFn: () => get<DayMovers>(`/market/day-movers?date=${date}`),
    enabled: !!date,
  });

export const useRankings = (metric: RankMetric, n = 50, ascending = false) =>
  useQuery({
    queryKey: ["rankings", metric, n, ascending],
    queryFn: () =>
      get<RankingRow[]>(`/rankings?metric=${metric}&n=${n}&ascending=${ascending}`),
  });

export const useSearch = (q: string) =>
  useQuery({
    queryKey: ["search", q],
    queryFn: () => get<SearchRow[]>(`/stocks/search?q=${encodeURIComponent(q)}&limit=50`),
    enabled: q.trim().length > 0,
    staleTime: 60_000,
  });

export const useKline = (code: string | null) =>
  useQuery({
    queryKey: ["kline", code],
    queryFn: () => get<StockKline>(`/stocks/${code}/kline`),
    enabled: !!code,
  });

export const useCompanyInfo = (code: string | null) =>
  useQuery({
    queryKey: ["companyInfo", code],
    queryFn: () => get<CompanyInfo>(`/stocks/${code}/info`),
    enabled: !!code,
    retry: false,
    staleTime: 24 * 3600_000,
  });

export const useVolatility = (code: string | null, window = 20) =>
  useQuery({
    queryKey: ["vol", code, window],
    queryFn: () => get<VolatilityPoint[]>(`/stocks/${code}/volatility?window=${window}`),
    enabled: !!code,
  });

// 财务扩展接口：数据随财报季/每日入库更新，客户端缓存 24h 足够（同 useCompanyInfo）
export const useQuarterlyFundamental = (code: string | null) =>
  useQuery({
    queryKey: ["quarterlyFundamental", code],
    queryFn: () => get<QuarterlyFundamental>(`/stocks/${code}/fundamental/quarterly`),
    enabled: !!code,
    retry: false,
    staleTime: 24 * 3600_000,
  });

export const useValuation = (code: string | null) =>
  useQuery({
    queryKey: ["valuation", code],
    queryFn: () => get<Valuation>(`/stocks/${code}/valuation`),
    enabled: !!code,
    retry: false,
    staleTime: 24 * 3600_000,
  });

export const useDividend = (code: string | null) =>
  useQuery({
    queryKey: ["dividend", code],
    queryFn: () => get<DividendRow[]>(`/stocks/${code}/dividend`),
    enabled: !!code,
    retry: false,
    staleTime: 24 * 3600_000,
  });

export const useEarnings = (code: string | null) =>
  useQuery({
    queryKey: ["earnings", code],
    queryFn: () => get<EarningsNews>(`/stocks/${code}/earnings`),
    enabled: !!code,
    retry: false,
    staleTime: 24 * 3600_000,
  });

export const useScreeningYears = () =>
  useQuery({ queryKey: ["screeningYears"], queryFn: () => get<number[]>("/screening/years") });

export const useScreening = (year: number | null) =>
  useQuery({
    queryKey: ["screening", year],
    queryFn: () => get<ScreeningRow[]>(`/screening/${year}`),
    enabled: year != null,
  });

export const useScreeningChart = (year: number | null, code: string | null) =>
  useQuery({
    queryKey: ["screeningChart", year, code],
    queryFn: () => get<ScreeningChart>(`/screening/${year}/${code}/chart`),
    enabled: year != null && !!code,
  });

export const useMaDuration = () =>
  useQuery({ queryKey: ["maDuration"], queryFn: () => get<MaDuration>("/ma-duration") });

export const useStatus = () =>
  useQuery({ queryKey: ["status"], queryFn: () => get<Status>("/status") });
