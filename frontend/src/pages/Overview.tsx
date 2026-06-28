import { useMemo, useState } from "react";
import { useBreadth, useEqualWeightIndex, useLimitUpDown } from "../api/client";
import { Card, CardHeader } from "../components/Card";
import { KpiCard } from "../components/KpiCard";
import { RangeTabs, rangeDays, type RangeKey } from "../components/RangeTabs";
import { ErrorState, Loading } from "../components/States";
import { IndexLineChart } from "../charts/IndexLineChart";
import { LimitUpDownChart } from "../charts/LimitUpDownChart";
import { fmtInt } from "../lib/format";

const START = "2025-01-01";

export function Overview() {
  const breadth = useBreadth();
  const ewi = useEqualWeightIndex(START);
  const lud = useLimitUpDown();
  const [range, setRange] = useState<RangeKey>("3M");

  const indexPoints = useMemo(() => {
    const pts = ewi.data ?? [];
    const d = rangeDays(range);
    if (!d || pts.length === 0) return pts;
    const cutoff = new Date(pts[pts.length - 1].date);
    cutoff.setDate(cutoff.getDate() - d);
    return pts.filter((p) => new Date(p.date) >= cutoff);
  }, [ewi.data, range]);

  const b = breadth.data;
  const ratio = b?.ratio == null ? "—" : b.ratio.toFixed(2);

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
          right={<RangeTabs value={range} onChange={setRange} />}
        />
        <div className="px-2 pb-2">
          {ewi.isLoading ? <Loading /> : ewi.error ? <div className="p-4"><ErrorState error={ewi.error} /></div> : (
            <IndexLineChart points={indexPoints} />
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="涨停 / 跌停家数" subtitle="每日封板镜像分布" />
        <div className="px-2 pb-2">
          {lud.isLoading ? <Loading /> : lud.error ? <div className="p-4"><ErrorState error={lud.error} /></div> : (
            <LimitUpDownChart points={lud.data ?? []} />
          )}
        </div>
      </Card>
    </div>
  );
}
