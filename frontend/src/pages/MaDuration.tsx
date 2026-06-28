import { useMemo, useState } from "react";
import { useMaDuration } from "../api/client";
import { Card, CardHeader } from "../components/Card";
import { KpiCard } from "../components/KpiCard";
import { ErrorState, Loading } from "../components/States";
import { ExpandToggle } from "../components/ExpandToggle";
import { DurationHistogram } from "../charts/DurationHistogram";

const INITIAL_ROWS = 10;

export function MaDuration({ onOpenStock }: { onOpenStock: (code: string) => void }) {
  const q = useMaDuration();
  const [pick, setPick] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);

  const drill = useMemo(() => {
    const samples = q.data?.samples ?? [];
    if (pick == null) return [];
    return samples.filter((s) => s.duration === pick);
  }, [q.data, pick]);

  if (q.isLoading) return <Loading label="计算多头时长分布…" />;
  if (q.error) return <ErrorState error={q.error} />;

  const s = q.data!.summary;
  const shown = expanded ? drill : drill.slice(0, INITIAL_ROWS);

  const selectDuration = (d: number) => {
    setPick(d);
    setExpanded(false);
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <KpiCard label="样本总数" value={s.total.toLocaleString("zh-CN")} tone="neutral" />
        <KpiCard label="中位时长" value={s.median == null ? "—" : `${s.median} 日`} tone="clay" />
        <KpiCard label="P90 时长" value={s.p90 == null ? "—" : `${s.p90} 日`} tone="neutral" />
        <KpiCard label="最长" value={s.max == null ? "—" : `${s.max} 日`} tone="neutral" />
        <KpiCard label="未结束" value={s.ongoing.toLocaleString("zh-CN")} tone="down" />
      </div>

      <Card>
        <CardHeader title="MA5 &gt; MA20 持续时长分布" subtitle="点击柱子查看该时长的个股明细" />
        <div className="px-2 pb-2">
          <DurationHistogram samples={q.data!.samples} onPick={selectDuration} />
        </div>
      </Card>

      {pick != null && (
        <Card>
          <CardHeader
            title={`持续 ${pick} 日的个股（${drill.length}）`}
            right={<button onClick={() => setPick(null)} className="text-xs text-muted hover:text-clay">收起</button>}
          />
          <div className="px-2 pb-4">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">名称</th>
                    <th className="px-3 py-2 font-medium">代码</th>
                    <th className="px-3 py-2 font-medium">上穿日</th>
                    <th className="px-3 py-2 font-medium">结束日</th>
                    <th className="px-3 py-2 font-medium">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r, i) => (
                    <tr key={`${r.code}-${i}`} className="border-b border-line/60">
                      <td className="px-3 py-2">
                        <button
                          onClick={() => onOpenStock(r.code)}
                          className="font-medium text-ink underline-offset-2 hover:text-clay hover:underline"
                          title="查看个股"
                        >
                          {r.code_name ?? "—"}
                        </button>
                      </td>
                      <td className="px-3 py-2 nums text-xs text-muted">{r.code}</td>
                      <td className="px-3 py-2 nums">{r.start_date}</td>
                      <td className="px-3 py-2 nums">{r.end_date}</td>
                      <td className="px-3 py-2">
                        <span className={`rounded-md px-2 py-0.5 text-xs ${r.ongoing ? "bg-amber/15 text-clayDark" : "bg-up/10 text-up"}`}>
                          {r.ongoing ? "未结束" : "已结束"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ExpandToggle
              expanded={expanded}
              hiddenCount={drill.length - INITIAL_ROWS}
              onToggle={() => setExpanded((v) => !v)}
            />
          </div>
        </Card>
      )}
    </div>
  );
}
