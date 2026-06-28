import { useQuery } from "@tanstack/react-query";
import type {
  Breadth,
  BreadthPoint,
  CompanyInfo,
  DayMovers,
  IndexPoint,
  LimitPoint,
  MaDuration,
  RankMetric,
  RankingRow,
  SearchRow,
  Status,
  StockKline,
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

export const useLimitUpDown = () =>
  useQuery({ queryKey: ["lud"], queryFn: () => get<LimitPoint[]>("/market/limit-up-down") });

export const useBreadthSeries = () =>
  useQuery({ queryKey: ["breadthSeries"], queryFn: () => get<BreadthPoint[]>("/market/breadth-series") });

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

export const useMaDuration = () =>
  useQuery({ queryKey: ["maDuration"], queryFn: () => get<MaDuration>("/ma-duration") });

export const useStatus = () =>
  useQuery({ queryKey: ["status"], queryFn: () => get<Status>("/status") });
