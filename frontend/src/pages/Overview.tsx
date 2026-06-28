import { useMemo, useState } from "react";
import { useBreadth, useBreadthSeries, useEqualWeightIndex } from "../api/client";
import { Card, CardHeader } from "../components/Card";
import { KpiCard } from "../components/KpiCard";
import { RangeTabs, rangeDays, type RangeKey } from "../components/RangeTabs";
import { ErrorState, Loading } from "../components/States";
import { IndexLineChart } from "../charts/IndexLineChart";
import { AdvanceDeclineChart } from "../charts/AdvanceDeclineChart";
import { DayMoversPanel } from "../components/DayMoversPanel";
import { fmtInt } from "../lib/format";

const START = "2025-01-01";

function sliceByRange<T extends { date: string }>(pts: T[], range: RangeKey): T[] {
  const d = rangeDays(range);
  if (!d || pts.length === 0) return pts;
  const cutoff = new Date(pts[pts.length - 1].date);
  cutoff.setDate(cutoff.getDate() - d);
  return pts.filter((p) => new Date(p.date) >= cutoff);
}

export function Overview({ onOpenStock }: { onOpenStock: (code: string) => void }) {
  const breadth = useBreadth();
  const ewi = useEqualWeightIndex(START);
  const series = useBreadthSeries();
  const [indexRange, setIndexRange] = useState<RangeKey>("3M");
  const [adRange, setAdRange] = useState<RangeKey>("3M");
  const [pickedDate, setPickedDate] = useState<string | null>(null);

  const indexPoints = useMemo(() => sliceByRange(ewi.data ?? [], indexRange), [ewi.data, indexRange]);
  const adPoints = useMemo(() => sliceByRange(series.data ?? [], adRange), [series.data, adRange]);

  const b = breadth.data;
  const ratio = b?.ratio == null ? "—" : b.ratio.toFixed(2);
  const latest = series.data && series.data.length ? series.data[series.data.length - 1] : null;

  return (
    <div className="space-y-6">
      <section>
        <div className="mb-3 flex items-end justify-between">
          <h2 className="text-xl font-semibold">市场宽度</h2>
          {b?.latest_date && <span className="nums text-sm text-muted">截至 {b.latest_date}</span>}
        </div>
        {breadth.isLoading ? (
          <Loading />
        ) : breadth.error ? (
          <ErrorState error={breadth.error} />
        ) : (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <KpiCard label="上涨家数" value={fmtInt(b?.up)} tone="up" />
            <KpiCard label="下跌家数" value={fmtInt(b?.down)} tone="down" />
            <KpiCard label="平盘家数" value={fmtInt(b?.flat)} tone="neutral" />
            <KpiCard label="涨跌比" value={ratio} tone="clay" />
          </div>
        )}
      </section>

      <Card>
        <CardHeader
          title="等权指数走势"
          subtitle="全市场等权组合累计收益"
          right={<RangeTabs value={indexRange} onChange={setIndexRange} />}
        />
        <div className="px-2 pb-2">
          {ewi.isLoading ? <Loading /> : ewi.error ? <div className="p-4"><ErrorState error={ewi.error} /></div> : (
            <IndexLineChart points={indexPoints} />
          )}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="每日涨跌家数"
          subtitle={
            latest
              ? `最新 ${latest.date}：上涨 ${fmtInt(latest.up)} · 下跌 ${fmtInt(latest.down)} · 涨停 ${latest.limit_up} · 跌停 ${latest.limit_down}`
              : "全市场每日上涨 / 下跌公司数量"
          }
          right={<RangeTabs value={adRange} onChange={setAdRange} />}
        />
        <div className="px-2 pb-2">
          {series.isLoading ? <Loading /> : series.error ? <div className="p-4"><ErrorState error={series.error} /></div> : (
            <>
              <p className="px-3 pb-1 text-xs text-muted">点击柱子查看当天上涨 / 下跌个股明细</p>
              <AdvanceDeclineChart points={adPoints} onPick={setPickedDate} />
            </>
          )}
        </div>
      </Card>

      {pickedDate && (
        <DayMoversPanel date={pickedDate} onClose={() => setPickedDate(null)} onOpenStock={onOpenStock} />
      )}
    </div>
  );
}
