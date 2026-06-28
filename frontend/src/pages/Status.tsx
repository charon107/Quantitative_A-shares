import type { ReactNode } from "react";
import { useStatus } from "../api/client";
import { Card, CardHeader } from "../components/Card";
import { KpiCard } from "../components/KpiCard";
import { ErrorState, Loading } from "../components/States";
import { fmtInt } from "../lib/format";

export function Status() {
  const q = useStatus();
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  const s = q.data!;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="最新交易日" value={s.latest_date ?? "—"} tone="clay" />
        <KpiCard label="覆盖股票数" value={fmtInt(s.n_codes)} tone="neutral" />
        <KpiCard label="K线总行数" value={fmtInt(s.n_rows)} tone="neutral" />
        <KpiCard label="统计起始" value={s.start_date} tone="neutral" />
      </div>

      <Card>
        <CardHeader title="运行状态" subtitle="数据源与缓存" />
        <div className="card-pad space-y-3 text-sm">
          <Row label="数据源" value="DuckDB（服务器本地）" />
          <Row
            label="Redis L2 缓存"
            value={
              <span className={s.redis_available ? "text-up" : "text-muted"}>
                {s.redis_available ? "● 已连接" : "○ 未启用（实时计算）"}
              </span>
            }
          />
          <Row label="复权方式" value="前复权日线" />
          <Row label="覆盖范围" value="沪深主板全部股票" />
        </div>
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-line/60 pb-2 last:border-0">
      <span className="text-muted">{label}</span>
      <span className="font-medium text-ink">{value}</span>
    </div>
  );
}
