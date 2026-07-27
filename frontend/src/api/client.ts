import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  Breadth,
  BreadthPoint,
  CompanyInfo,
  DayMovers,
  DividendRow,
  EarningsCalendarDates,
  EarningsCalendarDay,
  EarningsNews,
  HotStock,
  IndexPoint,
  MaDuration,
  PaperAccount,
  PaperCashFlow,
  PaperEquityCurve,
  PaperFill,
  PaperList,
  PaperMetrics,
  PaperOrder,
  PaperOrderSide,
  PaperOverview,
  PaperPosition,
  PaperPriceType,
  QuarterlyFundamental,
  QuoteRow,
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

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${path} ${detail}`);
  }
  return res.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${path} ${detail}`);
  }
  return res.json() as Promise<T>;
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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

// 批量最新行情（收藏列表）。queryKey 用排序后的 code 串，保证同集合命中同一缓存
export const useQuotes = (codes: string[]) => {
  const key = [...codes].sort().join(",");
  return useQuery({
    queryKey: ["quotes", key],
    queryFn: () => get<QuoteRow[]>(`/stocks/quotes?codes=${encodeURIComponent(key)}`),
    enabled: codes.length > 0,
  });
};

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

// 财报日历：month 为 null 时后端返回最近 120 天（含最近披露日，供初始定位）
export const useEarningsCalendarDates = (month: string | null) =>
  useQuery({
    queryKey: ["earningsCalendarDates", month ?? "auto"],
    queryFn: () =>
      get<EarningsCalendarDates>(`/earnings-calendar/dates${month ? `?month=${encodeURIComponent(month)}` : ""}`),
  });

export const useEarningsCalendarDay = (date: string | null) =>
  useQuery({
    queryKey: ["earningsCalendarDay", date],
    queryFn: () => get<EarningsCalendarDay>(`/earnings-calendar?date=${encodeURIComponent(date!)}`),
    enabled: !!date,
  });

export const useStatus = () =>
  useQuery({ queryKey: ["status"], queryFn: () => get<Status>("/status") });

// ========== 模拟盘（docs/paper-trading-design.md §4） ==========
// 所有 paper 查询挂在 ["paper", ...] 前缀下，写操作后按前缀整体失效

export const usePaperOverview = (accountId: string | null) =>
  useQuery({
    queryKey: ["paper", "overview", accountId],
    queryFn: () => get<PaperOverview>(`/paper/accounts/${accountId}/overview`),
    enabled: !!accountId,
    retry: false, // 404（账户不存在）需立即暴露给页面以清除本地 ID
  });

export const usePaperPositions = (accountId: string | null) =>
  useQuery({
    queryKey: ["paper", "positions", accountId],
    queryFn: () => get<PaperList<PaperPosition>>(`/paper/accounts/${accountId}/positions`),
    enabled: !!accountId,
  });

export const usePaperOrders = (accountId: string | null) =>
  useQuery({
    queryKey: ["paper", "orders", accountId],
    queryFn: () => get<PaperList<PaperOrder>>(`/paper/accounts/${accountId}/orders?limit=200`),
    enabled: !!accountId,
  });

export const usePaperFills = (accountId: string | null) =>
  useQuery({
    queryKey: ["paper", "fills", accountId],
    queryFn: () => get<PaperList<PaperFill>>(`/paper/accounts/${accountId}/fills?limit=200`),
    enabled: !!accountId,
  });

export const usePaperCashFlows = (accountId: string | null) =>
  useQuery({
    queryKey: ["paper", "cashFlows", accountId],
    queryFn: () => get<PaperList<PaperCashFlow>>(`/paper/accounts/${accountId}/cash-flows?limit=200`),
    enabled: !!accountId,
  });

export const usePaperEquityCurve = (accountId: string | null, start?: string) =>
  useQuery({
    queryKey: ["paper", "equityCurve", accountId, start ?? "all"],
    queryFn: () =>
      get<PaperEquityCurve>(`/paper/accounts/${accountId}/equity-curve${start ? `?start=${start}` : ""}`),
    enabled: !!accountId,
  });

export const usePaperMetrics = (accountId: string | null) =>
  useQuery({
    queryKey: ["paper", "metrics", accountId],
    queryFn: () => get<PaperMetrics>(`/paper/accounts/${accountId}/metrics`),
    enabled: !!accountId,
  });

const useInvalidatePaper = () => {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["paper"] });
};

export const useCreateAccount = () => {
  const invalidate = useInvalidatePaper();
  return useMutation({
    mutationFn: (body: { name: string; init_cash: number }) => post<PaperAccount>("/paper/accounts", body),
    onSuccess: invalidate,
  });
};

export interface PlaceOrderBody {
  request_id: string;
  code: string;
  side: PaperOrderSide;
  price_type: PaperPriceType;
  limit_price?: number;
  qty: number;
}

export const usePlaceOrder = (accountId: string | null) => {
  const invalidate = useInvalidatePaper();
  return useMutation({
    mutationFn: (body: PlaceOrderBody) =>
      post<PaperOrder>(`/paper/accounts/${accountId}/orders`, body),
    onSuccess: invalidate,
  });
};

export const useCancelOrder = (accountId: string | null) => {
  const invalidate = useInvalidatePaper();
  return useMutation({
    mutationFn: (orderId: string) =>
      del<{ ok: boolean }>(`/paper/accounts/${accountId}/orders/${orderId}`),
    onSuccess: invalidate,
  });
};

export const useUpdateCostPrice = (accountId: string | null) => {
  const invalidate = useInvalidatePaper();
  return useMutation({
    mutationFn: ({ code, cost_price }: { code: string; cost_price: number }) =>
      patch<{ code: string; cost_price: number }>(
        `/paper/accounts/${accountId}/positions/${code}`,
        { cost_price },
      ),
    onSuccess: invalidate,
  });
};

export const useResetAccount = (accountId: string | null) => {
  const invalidate = useInvalidatePaper();
  return useMutation({
    mutationFn: () =>
      post<{ ok: boolean; reset_id: string }>(`/paper/accounts/${accountId}/reset`, { confirm: true }),
    onSuccess: invalidate,
  });
};
