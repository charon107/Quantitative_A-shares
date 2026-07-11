import { useState } from "react";
import { useDividend } from "../api/client";
import { Card, CardHeader } from "./Card";
import { Loading } from "./States";
import { ExpandToggle } from "./ExpandToggle";

const INITIAL_ROWS = 8;

const fmtCash = (v: number | null) => (v == null ? "—" : v.toFixed(4).replace(/\.?0+$/, ""));
const fmtStk = (v: number | null) => (v == null || v === 0 ? "—" : v.toFixed(4).replace(/\.?0+$/, ""));

export function DividendPanel({ code }: { code: string }) {
  const q = useDividend(code);
  const [expanded, setExpanded] = useState(false);

  if (q.isLoading) {
    return (
      <Card>
        <CardHeader title="分红送股" />
        <Loading />
      </Card>
    );
  }
  if (q.error || !q.data || q.data.length === 0) return null; // 无数据时静默隐藏

  const rows = [...q.data].reverse(); // 最近在前
  const shown = expanded ? rows : rows.slice(0, INITIAL_ROWS);

  return (
    <Card>
      <CardHeader title="分红送股" subtitle="已实施 · 每股口径（元/股）" />
      <div className="px-2 pb-4">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="px-3 py-2 font-medium">分红年度</th>
                <th className="px-3 py-2 text-right font-medium">每股分红(税前)</th>
                <th className="px-3 py-2 text-right font-medium">每股分红(税后)</th>
                <th className="px-3 py-2 text-right font-medium">每股送转</th>
                <th className="px-3 py-2 font-medium">除权除息日</th>
                <th className="px-3 py-2 font-medium">派息日</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <tr key={row.end_date} className="border-b border-line/60">
                  <td className="px-3 py-2 nums">{row.end_date}</td>
                  <td className="px-3 py-2 text-right nums">{fmtCash(row.cash_div_tax)}</td>
                  <td className="px-3 py-2 text-right nums">{fmtCash(row.cash_div)}</td>
                  <td className="px-3 py-2 text-right nums">{fmtStk(row.stk_div)}</td>
                  <td className="px-3 py-2 nums text-xs text-muted">{row.ex_date ?? "—"}</td>
                  <td className="px-3 py-2 nums text-xs text-muted">{row.pay_date ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ExpandToggle
          expanded={expanded}
          hiddenCount={rows.length - INITIAL_ROWS}
          onToggle={() => setExpanded((v) => !v)}
        />
      </div>
    </Card>
  );
}
