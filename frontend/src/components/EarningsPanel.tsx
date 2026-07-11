import { useState } from "react";
import { useEarnings } from "../api/client";
import { Card, CardHeader } from "./Card";
import { Loading } from "./States";
import { ExpandToggle } from "./ExpandToggle";

const INITIAL_ROWS = 6;

const POSITIVE_TYPES = new Set(["预增", "扭亏", "续盈", "略增"]);
const NEGATIVE_TYPES = new Set(["预减", "首亏", "续亏", "略减"]);

const typeClass = (t: string | null) =>
  t == null ? "text-muted" : POSITIVE_TYPES.has(t) ? "text-up" : NEGATIVE_TYPES.has(t) ? "text-down" : "text-ink";

const fmtYi = (v: number | null) => (v == null ? "—" : `${(v / 1e8).toFixed(2)} 亿`);

const fmtPctRange = (lo: number | null, hi: number | null) => {
  const f = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
  if (lo == null && hi == null) return "—";
  if (lo != null && hi != null) return lo === hi ? f(lo) : `${f(lo)} ~ ${f(hi)}`;
  return f((lo ?? hi) as number);
};

const fmtYiRange = (lo: number | null, hi: number | null) => {
  if (lo == null && hi == null) return "—";
  if (lo != null && hi != null && lo !== hi) return `${(lo / 1e8).toFixed(2)} ~ ${(hi / 1e8).toFixed(2)} 亿`;
  return fmtYi(lo ?? hi);
};

const fmtPct = (v: number | null) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`);

const fmtRatio = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);

export function EarningsPanel({ code }: { code: string }) {
  const q = useEarnings(code);
  const [fcExpanded, setFcExpanded] = useState(false);
  const [exExpanded, setExExpanded] = useState(false);

  if (q.isLoading) {
    return (
      <Card>
        <CardHeader title="业绩动态" />
        <Loading />
      </Card>
    );
  }
  if (q.error || !q.data) return null;
  const forecasts = [...q.data.forecasts].reverse(); // 最近在前
  const express = [...q.data.express].reverse();
  if (forecasts.length === 0 && express.length === 0) return null; // 无数据时静默隐藏

  const fcShown = fcExpanded ? forecasts : forecasts.slice(0, INITIAL_ROWS);
  const exShown = exExpanded ? express : express.slice(0, INITIAL_ROWS);

  return (
    <Card>
      <CardHeader title="业绩动态" subtitle="业绩预告 / 业绩快报 · 均早于正式财报披露" />
      <div className="card-pad space-y-6 pt-0">
        {forecasts.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-medium text-muted">业绩预告</div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">报告期</th>
                    <th className="px-3 py-2 font-medium">公告日</th>
                    <th className="px-3 py-2 font-medium">类型</th>
                    <th className="px-3 py-2 text-right font-medium">预告净利润</th>
                    <th className="px-3 py-2 text-right font-medium">变动幅度</th>
                  </tr>
                </thead>
                <tbody>
                  {fcShown.map((row) => (
                    <tr key={`${row.end_date}-${row.ann_date}`} className="border-b border-line/60">
                      <td className="px-3 py-2 nums">{row.end_date}</td>
                      <td className="px-3 py-2 nums text-xs text-muted">{row.ann_date}</td>
                      <td className={`px-3 py-2 font-medium ${typeClass(row.type)}`}>{row.type ?? "—"}</td>
                      <td className="px-3 py-2 text-right nums">
                        {fmtYiRange(row.net_profit_min, row.net_profit_max)}
                      </td>
                      <td className="px-3 py-2 text-right nums">
                        {fmtPctRange(row.p_change_min, row.p_change_max)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ExpandToggle
              expanded={fcExpanded}
              hiddenCount={forecasts.length - INITIAL_ROWS}
              onToggle={() => setFcExpanded((v) => !v)}
            />
          </div>
        )}

        {express.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-medium text-muted">业绩快报</div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">报告期</th>
                    <th className="px-3 py-2 font-medium">公告日</th>
                    <th className="px-3 py-2 text-right font-medium">营收</th>
                    <th className="px-3 py-2 text-right font-medium">净利润</th>
                    <th className="px-3 py-2 text-right font-medium">ROE(摊薄)</th>
                    <th className="px-3 py-2 text-right font-medium">扣非净利同比</th>
                  </tr>
                </thead>
                <tbody>
                  {exShown.map((row) => (
                    <tr key={row.end_date} className="border-b border-line/60">
                      <td className="px-3 py-2 nums">{row.end_date}</td>
                      <td className="px-3 py-2 nums text-xs text-muted">{row.ann_date ?? "—"}</td>
                      <td className="px-3 py-2 text-right nums">{fmtYi(row.revenue)}</td>
                      <td className="px-3 py-2 text-right nums">{fmtYi(row.n_income)}</td>
                      <td className="px-3 py-2 text-right nums">{fmtRatio(row.diluted_roe)}</td>
                      <td className="px-3 py-2 text-right nums">{fmtPct(row.yoy_dedu_np)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ExpandToggle
              expanded={exExpanded}
              hiddenCount={express.length - INITIAL_ROWS}
              onToggle={() => setExExpanded((v) => !v)}
            />
          </div>
        )}
      </div>
    </Card>
  );
}
