import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import {
  useCancelOrder,
  useCreateAccount,
  usePaperCashFlows,
  usePaperEquityCurve,
  usePaperFills,
  usePaperMetrics,
  usePaperOrders,
  usePaperOverview,
  usePaperPositions,
  usePlaceOrder,
  useQuotes,
  useResetAccount,
  useUpdateCostPrice,
} from "../api/client";
import type {
  IndexPoint,
  PaperCashFlowType,
  PaperEquityCurvePoint,
  PaperOrderSide,
  PaperOrderStatus,
  PaperPosition,
  PaperPriceType,
} from "../api/types";
import { Card, CardHeader } from "../components/Card";
import { KpiCard } from "../components/KpiCard";
import { RangeTabs, type RangeKey } from "../components/RangeTabs";
import { SearchBox } from "../components/SearchBox";
import { Empty, ErrorState, Loading } from "../components/States";
import { fmtMoney, fmtPct, fmtPrice, signClass } from "../lib/format";
import { useSliceByRange } from "../lib/useSliceByRange";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

const STORAGE_KEY = "paper_account_id";

const INIT_CASH_PRESETS = [
  { label: "10 万", value: 100_000 },
  { label: "50 万", value: 500_000 },
  { label: "100 万", value: 1_000_000 },
  { label: "500 万", value: 5_000_000 },
] as const;

const MAX_ORDER_QTY = 1_000_000;

// crypto.randomUUID 仅在安全上下文（HTTPS/localhost）可用；站点走 HTTP+IP 访问，需降级
const genRequestId = (): string =>
  typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;

const SIDE_LABEL: Record<PaperOrderSide, string> = { buy: "买入", sell: "卖出" };
const PRICE_TYPE_LABEL: Record<PaperPriceType, string> = { market: "市价", limit: "限价" };
const STATUS_LABEL: Record<PaperOrderStatus, string> = {
  pending: "待成交",
  filled: "已成交",
  cancelled: "已撤单",
  expired: "已过期",
  rejected: "已拒绝",
};
const STATUS_CLASS: Record<PaperOrderStatus, string> = {
  pending: "text-amber",
  filled: "text-up",
  cancelled: "text-muted",
  expired: "text-muted",
  rejected: "text-down",
};
const FLOW_LABEL: Record<PaperCashFlowType, string> = {
  freeze: "冻结",
  unfreeze: "解冻",
  buy: "买入",
  sell: "卖出",
  reset: "重置",
};

const RISK_DISCLOSURE =
  "本功能为模拟交易，不构成任何投资建议；成交按收盘价撮合，与真实交易存在差异；历史行情为前复权数据，长期持仓收益可能与真实情况存在偏差。";

// "2026-07-27T08:43:52" -> "2026-07-27 08:43"
const fmtTime = (s: string | null | undefined) => (s ? s.replace("T", " ").slice(0, 16) : "—");

// 费用预估（与后端规则一致：佣金 max(5, 万2.5) 双向，印花税卖出 万5）
function estimateFee(amount: number, side: PaperOrderSide) {
  const commission = Math.max(5, amount * 0.00025);
  const stampTax = side === "sell" ? amount * 0.0005 : 0;
  return { commission, stampTax, fee: commission + stampTax };
}

export function PaperTrade() {
  const [accountId, setAccountId] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY));

  if (!accountId) {
    return (
      <CreateAccountForm
        onCreated={(id) => {
          localStorage.setItem(STORAGE_KEY, id);
          setAccountId(id);
        }}
      />
    );
  }
  return (
    <PaperDashboard
      accountId={accountId}
      onAccountGone={() => {
        localStorage.removeItem(STORAGE_KEY);
        setAccountId(null);
      }}
    />
  );
}

// ========== 创建账户 ==========

function CreateAccountForm({ onCreated }: { onCreated: (accountId: string) => void }) {
  const [name, setName] = useState("");
  const [preset, setPreset] = useState<number | "custom">(1_000_000);
  const [customCash, setCustomCash] = useState("");
  const create = useCreateAccount();

  const initCash = preset === "custom" ? Number(customCash) : preset;
  const valid = name.trim().length > 0 && Number.isFinite(initCash) && initCash > 0;

  const submit = () => {
    if (!valid || create.isPending) return;
    create.mutate(
      { name: name.trim(), init_cash: initCash },
      {
        onSuccess: (acc) => onCreated(acc.account_id),
      },
    );
  };

  return (
    <div className="mx-auto max-w-lg">
      <Card>
        <CardHeader title="创建模拟账户" subtitle="虚拟资金练习交易，成交按收盘价撮合" />
        <div className="space-y-4 px-5 pb-5">
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">账户名称</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：我的模拟盘"
              className="w-full rounded-xl border border-line bg-panel px-4 py-2.5 text-sm text-ink placeholder:text-muted shadow-soft outline-none transition focus:border-clay focus:ring-2 focus:ring-clay/20"
            />
          </div>
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">初始资金</label>
            <div className="flex flex-wrap gap-2">
              {INIT_CASH_PRESETS.map((p) => (
                <button
                  key={p.value}
                  onClick={() => setPreset(p.value)}
                  className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition ${
                    preset === p.value
                      ? "border-clay bg-clay/10 text-clay"
                      : "border-line bg-panel text-muted hover:text-ink"
                  }`}
                >
                  {p.label}
                </button>
              ))}
              <button
                onClick={() => setPreset("custom")}
                className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition ${
                  preset === "custom"
                    ? "border-clay bg-clay/10 text-clay"
                    : "border-line bg-panel text-muted hover:text-ink"
                }`}
              >
                自定义
              </button>
            </div>
            {preset === "custom" && (
              <input
                value={customCash}
                onChange={(e) => setCustomCash(e.target.value)}
                type="number"
                min={1}
                placeholder="输入金额（元）"
                className="mt-2 w-full rounded-xl border border-line bg-panel px-4 py-2.5 text-sm text-ink placeholder:text-muted shadow-soft outline-none transition focus:border-clay focus:ring-2 focus:ring-clay/20"
              />
            )}
          </div>
          {create.error && <ErrorState error={create.error} />}
          <button
            onClick={submit}
            disabled={!valid || create.isPending}
            className="w-full rounded-xl bg-clay px-4 py-2.5 text-sm font-medium text-white shadow-soft transition hover:bg-clayDark disabled:cursor-not-allowed disabled:opacity-50"
          >
            {create.isPending ? "创建中…" : "创建账户"}
          </button>
          <p className="text-xs text-muted">{RISK_DISCLOSURE}</p>
        </div>
      </Card>
    </div>
  );
}

// ========== 账户主面板 ==========

function PaperDashboard({ accountId, onAccountGone }: { accountId: string; onAccountGone: () => void }) {
  const overview = usePaperOverview(accountId);
  const positions = usePaperPositions(accountId);
  const [prefill, setPrefill] = useState<{ code: string; side: PaperOrderSide } | null>(null);

  // 后端 404（账户不存在）：清除本地 ID，回到创建表单
  useEffect(() => {
    const err = overview.error;
    if (err instanceof Error && err.message.startsWith("404")) onAccountGone();
  }, [overview.error, onAccountGone]);

  if (overview.isLoading) return <Loading label="加载账户…" />;
  if (overview.error) return <ErrorState error={overview.error} />;
  const ov = overview.data!;

  return (
    <div className="space-y-6">
      <AccountHeader accountId={accountId} name={ov.name} onAccountGone={onAccountGone} />

      <section>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <KpiCard label="总资产" value={fmtMoney(ov.total_asset)} tone="neutral" />
          <KpiCard label="可用资金" value={fmtMoney(ov.cash)} tone="neutral" />
          <KpiCard label="持仓市值" value={fmtMoney(ov.market_value)} tone="neutral" />
          <KpiCard
            label="累计盈亏"
            value={fmtMoney(ov.total_pnl)}
            tone={ov.total_pnl > 0 ? "up" : ov.total_pnl < 0 ? "down" : "neutral"}
            delta={ov.total_return_pct}
            deltaText={fmtPct(ov.total_return_pct)}
          />
        </div>
        {ov.asof_date && (
          <p className="mt-2 text-right nums text-xs text-muted">估值日期 {ov.asof_date}</p>
        )}
      </section>

      <EquityCurveCard accountId={accountId} />

      <PositionsCard
        accountId={accountId}
        positions={positions.data?.items ?? []}
        isLoading={positions.isLoading}
        error={positions.error}
        onSell={(code) => setPrefill({ code, side: "sell" })}
      />

      <OrderPanel
        accountId={accountId}
        cash={ov.cash}
        positions={positions.data?.items ?? []}
        prefill={prefill}
      />

      <RecordsCard accountId={accountId} />

      <p className="rounded-xl border border-line bg-panel2 px-4 py-3 text-xs leading-relaxed text-muted">
        {RISK_DISCLOSURE}
      </p>
    </div>
  );
}

// ========== 页头：账户 ID + 重置 ==========

function AccountHeader({
  accountId,
  name,
  onAccountGone,
}: {
  accountId: string;
  name: string;
  onAccountGone: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const reset = useResetAccount(accountId);

  const copyId = async () => {
    try {
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(accountId);
      } else {
        // HTTP 非安全上下文降级：临时 textarea + execCommand
        const ta = document.createElement("textarea");
        ta.value = accountId;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板不可用时静默失败，ID 本身已展示在页面上
    }
  };

  const onReset = () => {
    if (!window.confirm("确定要重置模拟账户吗？资金与持仓将恢复到初始状态，当前页面将退出该账户。")) return;
    reset.mutate(undefined, { onSuccess: () => onAccountGone() });
  };

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold text-ink">{name}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
            <span className="nums break-all">账户 ID：{accountId}</span>
            <button onClick={copyId} className="text-clay hover:underline">
              {copied ? "已复制" : "复制"}
            </button>
          </div>
          <p className="mt-1 text-xs text-muted">账户仅以此 ID 识别，请复制备份；丢失后无法找回账户。</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <button
            onClick={onReset}
            disabled={reset.isPending}
            className="rounded-lg border border-down/40 px-3 py-1.5 text-xs font-medium text-down transition hover:bg-down/5 disabled:opacity-50"
          >
            {reset.isPending ? "重置中…" : "重置账户"}
          </button>
          {reset.error && <span className="text-xs text-down">重置失败</span>}
        </div>
      </div>
    </Card>
  );
}

// ========== 净值曲线 vs 等权指数 ==========

function EquityCurveCard({ accountId }: { accountId: string }) {
  const [range, setRange] = useState<RangeKey>("ALL");
  const curve = usePaperEquityCurve(accountId);
  const metrics = usePaperMetrics(accountId);

  const curvePoints = useSliceByRange(curve.data?.curve, range);
  const benchPoints = useSliceByRange(curve.data?.benchmark, range);

  const m = metrics.data;
  const stats: { label: string; text: string; cls: string }[] = m
    ? [
        { label: "累计收益", text: fmtPct(m.total_return_pct), cls: signClass(m.total_return_pct) },
        { label: "年化收益", text: fmtPct(m.annualized_return_pct), cls: signClass(m.annualized_return_pct) },
        {
          label: "最大回撤",
          text: m.max_drawdown_pct == null ? "—" : `${m.max_drawdown_pct.toFixed(2)}%`,
          cls: "text-down",
        },
        { label: "胜率", text: m.win_rate == null ? "—" : `${(m.win_rate * 100).toFixed(1)}%`, cls: "text-ink" },
      ]
    : [];

  return (
    <Card>
      <CardHeader
        title="净值曲线"
        subtitle="账户收益率 vs 全市场等权指数（同区间归一化，起点 0%）"
        right={<RangeTabs value={range} onChange={setRange} />}
      />
      <div className="px-2 pb-2">
        {m && (
          <div className="flex flex-wrap gap-x-6 gap-y-1 px-3 pb-2">
            {stats.map((s) => (
              <span key={s.label} className="text-xs text-muted">
                {s.label} <span className={`nums font-medium ${s.cls}`}>{s.text}</span>
              </span>
            ))}
          </div>
        )}
        {curve.isLoading ? (
          <Loading />
        ) : curve.error ? (
          <div className="p-4"><ErrorState error={curve.error} /></div>
        ) : curvePoints.length === 0 ? (
          <Empty label="暂无净值数据，等待每日撮合后生成" />
        ) : (
          <EquityChart curve={curvePoints} benchmark={benchPoints} />
        )}
      </div>
    </Card>
  );
}

function EquityChart({
  curve,
  benchmark,
  height = 340,
}: {
  curve: PaperEquityCurvePoint[];
  benchmark: IndexPoint[];
  height?: number;
}) {
  // 账户 return_pct 为百分数；benchmark.value 为小数收益率，统一换算为百分数后叠加
  const series = [
    { name: "账户净值", color: C.clay, points: curve.map((p) => ({ date: p.date, value: p.return_pct })) },
    {
      name: "全市场等权",
      color: C.blue,
      points: benchmark
        .filter((p) => p.value != null)
        .map((p) => ({ date: p.date, value: +(p.value! * 100).toFixed(2) })),
    },
  ];

  const longest = series.reduce((a, b) => (a.points.length >= b.points.length ? a : b));
  const dates = longest.points.map((p) => p.date);

  const seriesDefs = series.map((s) => {
    const valMap = new Map(s.points.map((p) => [p.date, p.value]));
    return {
      name: s.name,
      type: "line" as const,
      data: dates.map((d) => valMap.get(d) ?? null),
      smooth: true,
      showSymbol: false,
      lineStyle: { color: s.color, width: 2 },
      itemStyle: { color: s.color },
    };
  });

  const tooltipFormatter = (params: { seriesName: string; dataIndex: number; value: number | null }[]) => {
    const i = params?.[0]?.dataIndex ?? 0;
    const rows = params
      .filter((s) => s.value != null)
      .map(
        (s) =>
          `<div style="display:flex;justify-content:space-between;gap:22px"><span style="color:${C.muted}">${s.seriesName}</span><b>${s.value}%</b></div>`,
      )
      .join("");
    return `<div style="font-weight:600;margin-bottom:4px">${dates[i]}</div>${rows}`;
  };

  const option = baseOption({
    legend: {
      data: seriesDefs.map((s) => s.name),
      top: 0,
      textStyle: { color: C.muted, fontSize: 11 },
      itemWidth: 24,
      itemHeight: 2,
    },
    grid: { left: 52, right: 44, top: 30, bottom: 28 },
    tooltip: { trigger: "axis", ...tooltipBase, formatter: tooltipFormatter },
    xAxis: { type: "category", data: dates, boundaryGap: false, ...axisBase },
    yAxis: {
      type: "value",
      ...axisBase,
      axisLabel: { ...axisBase.axisLabel, formatter: "{value}%" },
    },
    series: seriesDefs,
  });

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}

// ========== 持仓 ==========

function PositionsCard({
  accountId,
  positions,
  isLoading,
  error,
  onSell,
}: {
  accountId: string;
  positions: PaperPosition[];
  isLoading: boolean;
  error: unknown;
  onSell: (code: string) => void;
}) {
  const [editing, setEditing] = useState<{ code: string; value: string } | null>(null);
  const updateCost = useUpdateCostPrice(accountId);

  const saveCost = () => {
    if (!editing) return;
    const v = Number(editing.value);
    if (!Number.isFinite(v) || v <= 0) return;
    updateCost.mutate(
      { code: editing.code, cost_price: v },
      { onSuccess: () => setEditing(null) },
    );
  };

  const costCell = (p: PaperPosition) => {
    if (editing?.code === p.code) {
      return (
        <span className="inline-flex items-center justify-end gap-1.5">
          <input
            value={editing.value}
            onChange={(e) => setEditing({ code: p.code, value: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter") saveCost();
              if (e.key === "Escape") setEditing(null);
            }}
            type="number"
            min={0}
            step="0.01"
            autoFocus
            className="w-20 rounded-md border border-clay bg-panel px-1.5 py-0.5 text-right text-xs nums text-ink outline-none focus:ring-2 focus:ring-clay/20"
          />
          <button
            onClick={saveCost}
            disabled={updateCost.isPending}
            className="text-xs font-medium text-clay hover:underline disabled:opacity-40"
          >
            确定
          </button>
          <button onClick={() => setEditing(null)} className="text-xs text-muted hover:underline">
            取消
          </button>
        </span>
      );
    }
    return (
      <span className="inline-flex items-center justify-end gap-1.5">
        <span className="nums">{fmtPrice(p.cost_price)}</span>
        <button
          onClick={() => setEditing({ code: p.code, value: String(p.cost_price) })}
          title="修改成本价"
          className="text-xs text-muted transition hover:text-clay hover:underline"
        >
          改
        </button>
      </span>
    );
  };

  return (
    <Card>
      <CardHeader title="持仓" subtitle={`共 ${positions.length} 只`} />
      <div className="px-2 pb-4">
        {isLoading ? (
          <Loading />
        ) : error ? (
          <div className="p-4"><ErrorState error={error} /></div>
        ) : positions.length === 0 ? (
          <Empty label="暂无持仓，可在下方下单面板买入" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-muted">
                  <th className="px-3 py-2 font-medium">代码</th>
                  <th className="px-3 py-2 font-medium">名称</th>
                  <th className="px-3 py-2 text-right font-medium">数量</th>
                  <th className="px-3 py-2 text-right font-medium">可卖</th>
                  <th className="px-3 py-2 text-right font-medium">成本</th>
                  <th className="px-3 py-2 text-right font-medium">现价</th>
                  <th className="px-3 py-2 text-right font-medium">市值</th>
                  <th className="px-3 py-2 text-right font-medium">盈亏%</th>
                  <th className="px-3 py-2 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.code} className="border-b border-line/60 transition hover:bg-panel2">
                    <td className="px-3 py-2 nums text-xs text-muted">{p.code}</td>
                    <td className="px-3 py-2 font-medium text-ink">{p.name ?? "—"}</td>
                    <td className="px-3 py-2 text-right nums">{p.qty.toLocaleString("zh-CN")}</td>
                    <td className="px-3 py-2 text-right nums">{p.sellable_qty.toLocaleString("zh-CN")}</td>
                    <td className="px-3 py-2 text-right">{costCell(p)}</td>
                    <td className="px-3 py-2 text-right nums">{fmtPrice(p.last_close)}</td>
                    <td className="px-3 py-2 text-right nums">{fmtMoney(p.market_value)}</td>
                    <td className={`px-3 py-2 text-right nums ${signClass(p.pnl_pct)}`}>{fmtPct(p.pnl_pct)}</td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => onSell(p.code)}
                        disabled={p.sellable_qty <= 0}
                        className="rounded-md border border-line px-2.5 py-1 text-xs font-medium text-clay transition hover:border-clay hover:bg-clay/5 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        卖出
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}

// ========== 下单面板 ==========

function OrderPanel({
  accountId,
  cash,
  positions,
  prefill,
}: {
  accountId: string;
  cash: number;
  positions: PaperPosition[];
  prefill: { code: string; side: PaperOrderSide } | null;
}) {
  const [code, setCode] = useState<string | null>(null);
  const [side, setSide] = useState<PaperOrderSide>("buy");
  const [priceType, setPriceType] = useState<PaperPriceType>("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [qty, setQty] = useState("");
  const place = usePlaceOrder(accountId);

  // 持仓行内「卖出」预填
  useEffect(() => {
    if (!prefill) return;
    setCode(prefill.code);
    setSide(prefill.side);
    if (prefill.side === "sell") {
      const pos = positions.find((p) => p.code === prefill.code);
      setQty(pos ? String(pos.sellable_qty) : "");
    }
  }, [prefill]); // eslint-disable-line react-hooks/exhaustive-deps

  const quotes = useQuotes(code ? [code] : []);
  const quote = quotes.data?.[0] ?? null;

  const position = code ? positions.find((p) => p.code === code) : undefined;
  const qtyNum = Number(qty);
  const refPrice =
    priceType === "limit" ? (limitPrice.trim() ? Number(limitPrice) : null) : (quote?.close ?? null);

  const estimate = useMemo(() => {
    if (!refPrice || !Number.isFinite(qtyNum) || qtyNum <= 0) return null;
    const amount = qtyNum * refPrice;
    const { fee } = estimateFee(amount, side);
    return { amount, fee, total: side === "buy" ? amount + fee : amount - fee };
  }, [refPrice, qtyNum, side]);

  // 前端基础校验（后端仍会复核）
  const errors: string[] = [];
  if (!code) errors.push("请选择股票");
  if (!qty.trim()) {
    errors.push("请输入数量");
  } else if (!Number.isInteger(qtyNum) || qtyNum <= 0) {
    errors.push("数量须为正整数");
  } else {
    if (qtyNum % 100 !== 0) errors.push("数量须为 100 的整数倍");
    if (qtyNum > MAX_ORDER_QTY) errors.push("单笔数量不超过 1,000,000 股");
  }
  if (priceType === "limit" && (!limitPrice.trim() || !(Number(limitPrice) > 0)))
    errors.push("限价单须填写有效限价");
  if (code && side === "sell" && Number.isInteger(qtyNum) && qtyNum > 0) {
    const sellable = position?.sellable_qty ?? 0;
    if (qtyNum > sellable) errors.push(`卖出数量超过可卖数量（可卖 ${sellable}）`);
  }
  if (code && side === "buy" && estimate && estimate.total > cash)
    errors.push("预估金额（含费用）超过可用资金");
  if (priceType === "market" && code && quote && quote.close == null)
    errors.push("暂无最新价，无法按市价预估");

  const canSubmit = errors.length === 0 && estimate != null && !place.isPending;

  const submit = () => {
    if (!canSubmit || !code) return;
    place.mutate(
      {
        request_id: genRequestId(),
        code,
        side,
        price_type: priceType,
        ...(priceType === "limit" ? { limit_price: Number(limitPrice) } : {}),
        qty: qtyNum,
      },
      { onSuccess: () => setQty("") },
    );
  };

  const toggleCls = (active: boolean, activeCls: string) =>
    `rounded-lg border px-3 py-1.5 text-sm font-medium transition ${
      active ? activeCls : "border-line bg-panel text-muted hover:text-ink"
    }`;

  return (
    <Card>
      <CardHeader title="下单" subtitle={`可用资金 ${fmtMoney(cash)} 元`} />
      <div className="space-y-4 px-5 pb-5">
        <div className="flex flex-wrap items-center gap-3">
          <SearchBox onPick={(c) => setCode(c)} />
          {code && (
            <span className="text-sm text-ink">
              <span className="font-medium">{quote?.code_name ?? position?.name ?? ""}</span>{" "}
              <span className="nums text-xs text-muted">{code}</span>
              {quote?.close != null && (
                <span className="nums ml-2 text-xs text-muted">最新价 {fmtPrice(quote.close)}</span>
              )}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex items-center gap-2">
            <span className="text-[13px] text-muted">方向</span>
            <button onClick={() => setSide("buy")} className={toggleCls(side === "buy", "border-up bg-up/10 text-up")}>
              买入
            </button>
            <button
              onClick={() => setSide("sell")}
              className={toggleCls(side === "sell", "border-down bg-down/10 text-down")}
            >
              卖出
            </button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[13px] text-muted">方式</span>
            <button
              onClick={() => setPriceType("market")}
              className={toggleCls(priceType === "market", "border-clay bg-clay/10 text-clay")}
            >
              市价
            </button>
            <button
              onClick={() => setPriceType("limit")}
              className={toggleCls(priceType === "limit", "border-clay bg-clay/10 text-clay")}
            >
              限价
            </button>
          </div>
          {priceType === "limit" && (
            <div className="flex items-center gap-2">
              <span className="text-[13px] text-muted">限价</span>
              <input
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                type="number"
                min={0}
                step="0.01"
                placeholder="元"
                className="w-28 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm nums text-ink outline-none transition focus:border-clay focus:ring-2 focus:ring-clay/20"
              />
            </div>
          )}
          <div className="flex items-center gap-2">
            <span className="text-[13px] text-muted">数量</span>
            <input
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              type="number"
              min={100}
              step={100}
              placeholder="100 的整数倍"
              className="w-36 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm nums text-ink outline-none transition focus:border-clay focus:ring-2 focus:ring-clay/20"
            />
            {side === "sell" && position && (
              <span className="nums text-xs text-muted">可卖 {position.sellable_qty.toLocaleString("zh-CN")}</span>
            )}
          </div>
        </div>

        {estimate && (
          <div className="rounded-xl bg-panel2 px-4 py-3 text-sm">
            <span className="text-muted">
              预估{side === "buy" ? "买入金额" : "卖出到账"}（
              {priceType === "limit" ? `限价 ${fmtPrice(Number(limitPrice))}` : `最新价 ${fmtPrice(quote?.close ?? null)}`}
              ）：
            </span>{" "}
            <span className="nums font-medium text-ink">{fmtMoney(estimate.total)} 元</span>{" "}
            <span className="nums text-xs text-muted">
              （成交额 {fmtMoney(estimate.amount)} + 预估费用 {fmtMoney(estimate.fee)}）
            </span>
          </div>
        )}

        {errors.length > 0 && code && (
          <ul className="space-y-0.5 text-xs text-down">
            {errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        )}
        {place.error && <ErrorState error={place.error} />}
        {place.isSuccess && !place.isPending && (
          <p className="text-xs text-up">委托已提交，将于下一交易日按收盘价撮合。</p>
        )}

        <button
          onClick={submit}
          disabled={!canSubmit}
          className={`rounded-xl px-6 py-2.5 text-sm font-medium text-white shadow-soft transition disabled:cursor-not-allowed disabled:opacity-50 ${
            side === "buy" ? "bg-up hover:bg-up/90" : "bg-down hover:bg-down/90"
          }`}
        >
          {place.isPending ? "提交中…" : `${SIDE_LABEL[side]}下单`}
        </button>
      </div>
    </Card>
  );
}

// ========== 记录区：委托 / 成交 / 资金流水 ==========

type RecordTab = "orders" | "fills" | "flows";

function RecordsCard({ accountId }: { accountId: string }) {
  const [tab, setTab] = useState<RecordTab>("orders");
  const orders = usePaperOrders(accountId);
  const fills = usePaperFills(accountId);
  const flows = usePaperCashFlows(accountId);
  const cancel = useCancelOrder(accountId);

  const tabs: { key: RecordTab; label: string; count?: number }[] = [
    { key: "orders", label: "委托", count: orders.data?.total },
    { key: "fills", label: "成交", count: fills.data?.total },
    { key: "flows", label: "资金流水", count: flows.data?.total },
  ];

  return (
    <Card>
      <CardHeader
        title="记录"
        right={
          <div className="inline-flex rounded-lg border border-line bg-panel2 p-0.5">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`rounded-md px-3 py-1 text-xs font-medium transition ${
                  tab === t.key ? "bg-panel text-clay shadow-soft" : "text-muted hover:text-ink"
                }`}
              >
                {t.label}
                {t.count != null ? ` (${t.count})` : ""}
              </button>
            ))}
          </div>
        }
      />
      <div className="px-2 pb-4">
        {tab === "orders" &&
          (orders.isLoading ? (
            <Loading />
          ) : orders.error ? (
            <div className="p-4"><ErrorState error={orders.error} /></div>
          ) : !orders.data?.items.length ? (
            <Empty label="暂无委托" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">时间</th>
                    <th className="px-3 py-2 font-medium">代码</th>
                    <th className="px-3 py-2 font-medium">名称</th>
                    <th className="px-3 py-2 font-medium">方向</th>
                    <th className="px-3 py-2 font-medium">方式</th>
                    <th className="px-3 py-2 text-right font-medium">限价</th>
                    <th className="px-3 py-2 text-right font-medium">数量</th>
                    <th className="px-3 py-2 font-medium">状态</th>
                    <th className="px-3 py-2 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.data.items.map((o) => (
                    <tr key={o.order_id} className="border-b border-line/60">
                      <td className="px-3 py-2 nums text-xs text-muted">{fmtTime(o.created_at)}</td>
                      <td className="px-3 py-2 nums text-xs text-muted">{o.code}</td>
                      <td className="px-3 py-2 font-medium text-ink">{o.code_name ?? "—"}</td>
                      <td className={`px-3 py-2 ${o.side === "buy" ? "text-up" : "text-down"}`}>
                        {SIDE_LABEL[o.side]}
                      </td>
                      <td className="px-3 py-2 text-muted">{PRICE_TYPE_LABEL[o.price_type]}</td>
                      <td className="px-3 py-2 text-right nums">{fmtPrice(o.limit_price)}</td>
                      <td className="px-3 py-2 text-right nums">{o.qty.toLocaleString("zh-CN")}</td>
                      <td className={`px-3 py-2 ${STATUS_CLASS[o.status]}`}>
                        {STATUS_LABEL[o.status]}
                        {o.status === "rejected" && o.reject_reason && (
                          <span className="ml-1 text-xs text-muted">({o.reject_reason})</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {o.status === "pending" && (
                          <button
                            onClick={() => cancel.mutate(o.order_id)}
                            disabled={cancel.isPending}
                            className="rounded-md border border-line px-2.5 py-1 text-xs font-medium text-muted transition hover:border-down hover:text-down disabled:opacity-40"
                          >
                            撤单
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}

        {tab === "fills" &&
          (fills.isLoading ? (
            <Loading />
          ) : fills.error ? (
            <div className="p-4"><ErrorState error={fills.error} /></div>
          ) : !fills.data?.items.length ? (
            <Empty label="暂无成交" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">成交日</th>
                    <th className="px-3 py-2 font-medium">代码</th>
                    <th className="px-3 py-2 font-medium">名称</th>
                    <th className="px-3 py-2 font-medium">方向</th>
                    <th className="px-3 py-2 text-right font-medium">价格</th>
                    <th className="px-3 py-2 text-right font-medium">数量</th>
                    <th className="px-3 py-2 text-right font-medium">金额</th>
                    <th className="px-3 py-2 text-right font-medium">费用</th>
                  </tr>
                </thead>
                <tbody>
                  {fills.data.items.map((f) => (
                    <tr key={f.fill_id} className="border-b border-line/60">
                      <td className="px-3 py-2 nums text-xs text-muted">{f.trade_date}</td>
                      <td className="px-3 py-2 nums text-xs text-muted">{f.code}</td>
                      <td className="px-3 py-2 font-medium text-ink">{f.code_name ?? "—"}</td>
                      <td className={`px-3 py-2 ${f.side === "buy" ? "text-up" : "text-down"}`}>
                        {SIDE_LABEL[f.side]}
                      </td>
                      <td className="px-3 py-2 text-right nums">{fmtPrice(f.price)}</td>
                      <td className="px-3 py-2 text-right nums">{f.qty.toLocaleString("zh-CN")}</td>
                      <td className="px-3 py-2 text-right nums">{fmtMoney(f.amount)}</td>
                      <td className="px-3 py-2 text-right nums text-muted">{fmtMoney(f.fee)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}

        {tab === "flows" &&
          (flows.isLoading ? (
            <Loading />
          ) : flows.error ? (
            <div className="p-4"><ErrorState error={flows.error} /></div>
          ) : !flows.data?.items.length ? (
            <Empty label="暂无资金流水" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">时间</th>
                    <th className="px-3 py-2 font-medium">类型</th>
                    <th className="px-3 py-2 text-right font-medium">金额</th>
                    <th className="px-3 py-2 text-right font-medium">变动后可用</th>
                  </tr>
                </thead>
                <tbody>
                  {flows.data.items.map((f) => (
                    <tr key={f.flow_id} className="border-b border-line/60">
                      <td className="px-3 py-2 nums text-xs text-muted">{fmtTime(f.created_at)}</td>
                      <td className="px-3 py-2 text-muted">{FLOW_LABEL[f.type] ?? f.type}</td>
                      <td className={`px-3 py-2 text-right nums ${signClass(f.amount)}`}>
                        {f.amount > 0 ? "+" : ""}
                        {fmtMoney(f.amount)}
                      </td>
                      <td className="px-3 py-2 text-right nums">{fmtMoney(f.balance_after)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </div>
    </Card>
  );
}
