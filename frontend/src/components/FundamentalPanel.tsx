import { useMemo, useState } from "react";
import { useEarnings, useQuarterlyFundamental } from "../api/client";
import type { ExpressRow, QuarterlyFundamentalPoint } from "../api/types";
import { Card, CardHeader } from "./Card";
import { Loading } from "./States";
import { QuarterlyFundamentalChart } from "../charts/QuarterlyFundamentalChart";
import type { FundamentalMode } from "../charts/QuarterlyFundamentalChart";

const MODES: { key: FundamentalMode; label: string }[] = [
  { key: "cum", label: "累计" },
  { key: "single", label: "单季" },
];

const QUARTER_BY_MONTH: Record<string, number> = { "03": 1, "06": 2, "09": 3, "12": 4 };

const safeDiv = (a: number | null, b: number | null) =>
  a == null || b == null || b === 0 ? null : a / b;

/** 最新快报仅作为前端预览点叠加；正式季度点入库后自动替代，不回写正式财报表。 */
function withExpressPreview(
  points: QuarterlyFundamentalPoint[],
  expressRows: ExpressRow[],
): QuarterlyFundamentalPoint[] {
  const latest = expressRows[expressRows.length - 1];
  const latestFormalEnd = points[points.length - 1]?.end_date ?? "";
  if (!latest || latest.end_date <= latestFormalEnd) return points;

  const match = /^(\d{4})-(\d{2})-\d{2}$/.exec(latest.end_date);
  const year = match ? Number(match[1]) : null;
  const quarter = match ? QUARTER_BY_MONTH[match[2]] : null;
  if (year == null || quarter == null) return points;

  const prev = points.find((p) => p.year === year && p.quarter === quarter - 1);
  const qRevenue =
    latest.revenue == null
      ? null
      : quarter === 1
        ? latest.revenue
        : prev?.revenue == null
          ? null
          : latest.revenue - prev.revenue;
  const qNetProfit =
    latest.n_income == null
      ? null
      : quarter === 1
        ? latest.n_income
        : prev?.net_profit == null
          ? null
          : latest.n_income - prev.net_profit;

  const preview: QuarterlyFundamentalPoint = {
    end_date: latest.end_date,
    year,
    quarter,
    ann_date: latest.ann_date,
    source: "express",
    roe: null, // diluted_roe 与正式 ROE 口径不同，不混填
    roe_dt: null,
    roa: null,
    netprofit_margin: safeDiv(latest.n_income, latest.revenue),
    grossprofit_margin: null,
    net_profit: latest.n_income,
    profit_dedt: null,
    revenue: latest.revenue,
    eps: null,
    bps: null,
    debt_to_assets: null,
    or_yoy: latest.yoy_sales,
    netprofit_yoy: null,
    dt_netprofit_yoy: latest.yoy_dedu_np,
    cfo: null,
    total_assets: null,
    total_liab: null,
    total_debt: null,
    q_roe: null,
    q_dt_roe: null,
    q_netprofit_margin: safeDiv(qNetProfit, qRevenue),
    q_gsprofit_margin: null,
    q_net_profit: qNetProfit,
    q_revenue: qRevenue,
    q_cfo: null,
    q_sales_yoy: null,
    q_sales_qoq: null,
    q_netprofit_yoy: null,
    q_netprofit_qoq: null,
  };
  return [...points, preview];
}

export function FundamentalPanel({ code }: { code: string }) {
  const q = useQuarterlyFundamental(code);
  const earnings = useEarnings(code);
  const [mode, setMode] = useState<FundamentalMode>("cum");
  const points = useMemo(
    () => withExpressPreview(q.data?.points ?? [], earnings.data?.express ?? []),
    [q.data?.points, earnings.data?.express],
  );
  const hasExpressPreview = points[points.length - 1]?.source === "express";

  if (q.isLoading) {
    return (
      <Card>
        <CardHeader title="季度基本面" />
        <Loading />
      </Card>
    );
  }
  if (q.error || !q.data || q.data.points.length === 0) return null; // 无数据时静默隐藏

  const baseSubtitle =
    mode === "cum"
      ? "累计口径（年初至报告期末）· 总债务=短借+长借+应付债券（有息）"
      : "单季口径（相邻累计差分，Q1=累计）· 缺口断线不插值 · 总债务=短借+长借+应付债券";

  return (
    <Card>
      <CardHeader
        title="季度基本面"
        subtitle={`${baseSubtitle}${hasExpressPreview ? " · 最新快报为预览点，正式财报披露后自动替换" : ""}`}
        right={
          <div className="inline-flex rounded-lg border border-line bg-panel2 p-0.5">
            {MODES.map((m) => (
              <button
                key={m.key}
                onClick={() => setMode(m.key)}
                className={`rounded-md px-3 py-1 text-xs font-medium transition ${
                  mode === m.key ? "bg-panel text-clay shadow-soft" : "text-muted hover:text-ink"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        }
      />
      <div className="px-2 pb-2">
        <QuarterlyFundamentalChart points={points} mode={mode} />
      </div>
    </Card>
  );
}
