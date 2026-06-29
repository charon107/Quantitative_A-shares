import { useState } from "react";
import { useBreadth, useBreadthSeries, useEqualWeightIndex, useShanghaiEqualWeightIndex, useLimitUpDown } from "../api/client";
import { Card, CardHeader } from "../components/Card";
import { KpiCard } from "../components/KpiCard";
import { RangeTabs, type RangeKey } from "../components/RangeTabs";
import { ErrorState, Loading } from "../components/States";
import { IndexLineChart } from "../charts/IndexLineChart";
import { AdvanceDeclineChart } from "../charts/AdvanceDeclineChart";
import { LimitUpDownChart } from "../charts/LimitUpDownChart";
import { DayMoversPanel } from "../components/DayMoversPanel";
import { fmtInt } from "../lib/format";
import { useSliceByRange } from "../lib/useSliceByRange";
import { C } from "../theme/echarts";

const START = "2025-01-01";

export function Overview({ onOpenStock }: { onOpenStock: (code: string) => void }) {
  const breadth = useBreadth();
  const ewi = useEqualWeightIndex(START);
  const shewi = useShanghaiEqualWeightIndex(START);
  const series = useBreadthSeries();
  const lud = useLimitUpDown();
  const [indexRange, setIndexRange] = useState<RangeKey>("3M");
  const [adRange, setAdRange] = useState<RangeKey>("3M");
  const [ludRange, setLudRange] = useState<RangeKey>("3M");
  const [pickedDate, setPickedDate] = useState<string | null>(null);

  const ewiPoints = useSliceByRange(ewi.data, indexRange);
  const shewiPoints = useSliceByRange(shewi.data, indexRange);
  const adPoints = useSliceByRange(series.data, adRange);
  const ludPoints = useSliceByRange(lud.data, ludRange);

  const b = breadth.data;
  const ratio = b?.ratio == null ? "—" : b.ratio.toFixed(2);
  const latest = series.data && series.data.length ? series.data[series.data.length - 1] : null;
  const latestLud = lud.data && lud.data.length ? lud.data[lud.data.length - 1] : null;

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
          title="指数走势"
          subtitle="全市场等权 + 上证主板等权累计收益"
          right={<RangeTabs value={indexRange} onChange={setIndexRange} />}
        />
        <div className="px-2 pb-2">
          {ewi.isLoading ? <Loading /> : ewi.error ? <div className="p-4"><ErrorState error={ewi.error} /></div> : (
            <IndexLineChart
              series={[
                { name: "全市场等权", points: ewiPoints, color: C.clay },
                { name: "上证等权", points: shewiPoints, color: C.blue },
              ]}
            />
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

      <Card>
        <CardHeader
          title="涨停 / 跌停趋势"
          subtitle={
            latestLud
              ? `最新 ${latestLud.date}：涨停 ${latestLud.limit_up} 家 · 跌停 ${latestLud.limit_down} 家`
              : "全市场每日涨停 / 跌停家数"
          }
          right={<RangeTabs value={ludRange} onChange={setLudRange} />}
        />
        <div className="px-2 pb-2">
          {lud.isLoading ? <Loading /> : lud.error ? <div className="p-4"><ErrorState error={lud.error} /></div> : (
            <LimitUpDownChart points={ludPoints} />
          )}
        </div>
      </Card>
    </div>
  );
}
