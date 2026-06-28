import { useEffect, useState } from "react";
import { useDayMovers } from "../api/client";
import type { MoverRow } from "../api/types";
import { Card, CardHeader } from "./Card";
import { ErrorState, Loading } from "./States";
import { ExpandToggle } from "./ExpandToggle";
import { fmtPct, fmtPrice, signClass } from "../lib/format";

const INITIAL_ROWS = 10;

function MoversTable({
  title,
  tone,
  rows,
  onOpenStock,
}: {
  title: string;
  tone: "up" | "down";
  rows: MoverRow[];
  onOpenStock: (code: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => setExpanded(false), [rows]);
  const shown = expanded ? rows : rows.slice(0, INITIAL_ROWS);

  return (
    <div>
      <div className="mb-2 flex items-center gap-2 px-1">
        <span className={`h-2.5 w-2.5 rounded-full ${tone === "up" ? "bg-up" : "bg-down"}`} />
        <span className="font-semibold text-ink">{title}</span>
        <span className="nums text-sm text-muted">{rows.length}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="px-2 py-2 font-medium">名称</th>
              <th className="px-2 py-2 font-medium">代码</th>
              <th className="px-2 py-2 text-right font-medium">开盘</th>
              <th className="px-2 py-2 text-right font-medium">最新</th>
              <th className="px-2 py-2 text-right font-medium">涨跌幅</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.code} className="border-b border-line/60 hover:bg-panel2">
                <td className="px-2 py-1.5">
                  <button
                    onClick={() => onOpenStock(r.code)}
                    className="font-medium text-ink underline-offset-2 hover:text-clay hover:underline"
                    title="查看个股"
                  >
                    {r.code_name ?? "—"}
                  </button>
                </td>
                <td className="px-2 py-1.5 nums text-xs text-muted">{r.code}</td>
                <td className="px-2 py-1.5 text-right nums">{fmtPrice(r.open)}</td>
                <td className="px-2 py-1.5 text-right nums">{fmtPrice(r.close)}</td>
                <td className={`px-2 py-1.5 text-right nums ${signClass(r.pctChg)}`}>{fmtPct(r.pctChg)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ExpandToggle expanded={expanded} hiddenCount={rows.length - INITIAL_ROWS} onToggle={() => setExpanded((v) => !v)} />
    </div>
  );
}

export function DayMoversPanel({
  date,
  onClose,
  onOpenStock,
}: {
  date: string;
  onClose: () => void;
  onOpenStock: (code: string) => void;
}) {
  const q = useDayMovers(date);

  return (
    <Card>
      <CardHeader
        title={`${date} 个股明细`}
        subtitle="左：上涨 · 右：下跌 · 点击名称查看个股"
        right={<button onClick={onClose} className="text-xs text-muted hover:text-clay">收起</button>}
      />
      <div className="px-5 pb-5">
        {q.isLoading ? (
          <Loading />
        ) : q.error ? (
          <ErrorState error={q.error} />
        ) : (
          <div className="grid gap-6 md:grid-cols-2">
            <MoversTable title="上涨" tone="up" rows={q.data?.up ?? []} onOpenStock={onOpenStock} />
            <MoversTable title="下跌" tone="down" rows={q.data?.down ?? []} onOpenStock={onOpenStock} />
          </div>
        )}
      </div>
    </Card>
  );
}
