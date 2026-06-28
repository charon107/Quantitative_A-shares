import { useState } from "react";
import { useKline, useRankings } from "../api/client";
import type { RankMetric } from "../api/types";
import { Card, CardHeader } from "../components/Card";
import { ErrorState, Loading } from "../components/States";
import { KlineChart } from "../charts/KlineChart";
import { fmtAmount, fmtPct, fmtPrice, fmtTurn, signClass } from "../lib/format";

type Tab = { key: string; label: string; metric: RankMetric; asc: boolean };
const TABS: Tab[] = [
  { key: "gain", label: "涨幅榜", metric: "pctChg", asc: false },
  { key: "loss", label: "跌幅榜", metric: "pctChg", asc: true },
  { key: "amount", label: "成交额", metric: "amount", asc: false },
  { key: "turn", label: "换手率", metric: "turn", asc: false },
];

export function Rankings() {
  const [tab, setTab] = useState<Tab>(TABS[0]);
  const [picked, setPicked] = useState<string | null>(null);
  const q = useRankings(tab.metric, 50, tab.asc);
  const kline = useKline(picked);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="排行榜"
          subtitle="全市场最新一日 · Top 50"
          right={
            <div className="inline-flex rounded-lg border border-line bg-panel2 p-0.5">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  onClick={() => setTab(t)}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition ${
                    tab.key === t.key ? "bg-panel text-clay shadow-soft" : "text-muted hover:text-ink"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          }
        />
        <div className="px-2 pb-4">
          {q.isLoading ? (
            <Loading />
          ) : q.error ? (
            <div className="p-4"><ErrorState error={q.error} /></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">#</th>
                    <th className="px-3 py-2 font-medium">名称</th>
                    <th className="px-3 py-2 font-medium">代码</th>
                    <th className="px-3 py-2 text-right font-medium">最新价</th>
                    <th className="px-3 py-2 text-right font-medium">涨跌幅</th>
                    <th className="px-3 py-2 text-right font-medium">成交额</th>
                    <th className="px-3 py-2 text-right font-medium">换手率</th>
                  </tr>
                </thead>
                <tbody>
                  {(q.data ?? []).map((row, i) => (
                    <tr
                      key={row.code}
                      onClick={() => setPicked(row.code)}
                      className={`cursor-pointer border-b border-line/60 transition hover:bg-panel2 ${
                        picked === row.code ? "bg-clay/5" : ""
                      }`}
                    >
                      <td className="px-3 py-2 nums text-muted">{i + 1}</td>
                      <td className="px-3 py-2 font-medium text-ink">{row.code_name ?? "—"}</td>
                      <td className="px-3 py-2 nums text-xs text-muted">{row.code}</td>
                      <td className="px-3 py-2 text-right nums">{fmtPrice(row.close)}</td>
                      <td className={`px-3 py-2 text-right nums ${signClass(row.pctChg)}`}>{fmtPct(row.pctChg)}</td>
                      <td className="px-3 py-2 text-right nums">{fmtAmount(row.amount)}</td>
                      <td className="px-3 py-2 text-right nums">{fmtTurn(row.turn)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>

      {picked && (
        <Card>
          <CardHeader
            title={`K 线速览 · ${kline.data?.code_name ?? picked}`}
            right={
              <button onClick={() => setPicked(null)} className="text-xs text-muted hover:text-clay">
                收起
              </button>
            }
          />
          <div className="px-2 pb-2">
            {kline.isLoading ? <Loading /> : kline.error ? <div className="p-4"><ErrorState error={kline.error} /></div> : (
              <KlineChart points={kline.data?.points ?? []} height={360} />
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
