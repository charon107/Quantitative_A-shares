import type { ReactNode } from "react";
import type { RangeStats } from "../lib/rangeStats";
import { fmtVolume, signClass } from "../lib/format";

function Stat({ label, value, className = "" }: { label: string; value: ReactNode; className?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/50 py-1.5">
      <span className="text-xs text-muted">{label}</span>
      <span className={`nums text-sm font-medium ${className}`}>{value}</span>
    </div>
  );
}

export function RangeStatsPanel({ stats, onClose }: { stats: RangeStats; onClose: () => void }) {
  const yi = (stats.amount * 1000) / 1e8; // 千元 -> 元 -> 亿
  return (
    <div className="card card-pad">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-base font-semibold tracking-tight">区间统计</h3>
        <button onClick={onClose} className="text-xs text-muted hover:text-clay">清除</button>
      </div>
      <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <span className="text-muted">起始 <b className="nums text-ink">{stats.startDate}</b></span>
        <span className="text-muted">终止 <b className="nums text-ink">{stats.endDate}</b></span>
        <span className="text-muted">周期 <b className="nums text-ink">{stats.count}</b> 根</span>
      </div>
      <div className="grid grid-cols-2 gap-x-8 md:grid-cols-3">
        <Stat label="起始价" value={stats.startPrice.toFixed(2)} />
        <Stat label="终止价" value={stats.endPrice.toFixed(2)} />
        <Stat label="均价" value={stats.avgPrice.toFixed(2)} className="text-clay" />
        <Stat label="最高价" value={stats.high.toFixed(2)} className="text-up" />
        <Stat label="最低价" value={stats.low.toFixed(2)} className="text-down" />
        <Stat label="振幅" value={`${stats.amplitude.toFixed(2)}%`} />
        <Stat
          label="涨跌幅"
          value={`${stats.pctChg >= 0 ? "+" : ""}${stats.pctChg.toFixed(2)}%`}
          className={signClass(stats.pctChg)}
        />
        <Stat label="换手率" value={`${stats.turnover.toFixed(2)}%`} />
        <Stat label="总量" value={fmtVolume(stats.volume)} />
        <Stat label="金额" value={`${yi.toFixed(2)} 亿`} />
        <Stat label="阳线" value={stats.up} className="text-up" />
        <Stat label="阴线" value={stats.down} className="text-down" />
        <Stat label="平线" value={stats.flat} />
      </div>
    </div>
  );
}
