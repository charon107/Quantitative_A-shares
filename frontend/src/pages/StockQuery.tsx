import { useState } from "react";
import { useKline, useVolatility } from "../api/client";
import { SearchBox } from "../components/SearchBox";
import { Card, CardHeader } from "../components/Card";
import { KpiCard } from "../components/KpiCard";
import { ErrorState, Loading } from "../components/States";
import { CompanyInfoPanel } from "../components/CompanyInfoPanel";
import { KlineChart } from "../charts/KlineChart";
import { VolatilityChart } from "../charts/VolatilityChart";
import { fmtAmount, fmtPct, fmtPrice, fmtTurn } from "../lib/format";

export function StockQuery({ initialCode }: { initialCode?: string | null }) {
  const [code, setCode] = useState<string | null>(initialCode ?? null);
  const kline = useKline(code);
  const vol = useVolatility(code);

  const pts = kline.data?.points ?? [];
  const last = pts.length ? pts[pts.length - 1] : null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <SearchBox onPick={setCode} />
        {kline.data && (
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-semibold">{kline.data.code_name ?? kline.data.code}</span>
            <span className="nums text-sm text-muted">{kline.data.code}</span>
          </div>
        )}
        <a
          href="/api/export/kline.parquet"
          download
          className="ml-auto inline-flex items-center gap-1.5 rounded-xl border border-line bg-panel px-3.5 py-2 text-sm font-medium text-clay shadow-soft transition hover:border-clay/40 hover:bg-clay/5"
          title="导出数据库全部 K线为 Parquet 文件"
        >
          <span aria-hidden>↓</span> 下载全部数据
        </a>
      </div>

      {!code && (
        <Card>
          <div className="py-16 text-center text-muted">输入代码或名称，查看个股 K 线与指标。</div>
        </Card>
      )}

      {code && last && (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <KpiCard label="最新价" value={fmtPrice(last.close)} tone="neutral" />
          <KpiCard label="涨跌幅" value={fmtPct(last.pctChg)} tone={(last.pctChg ?? 0) >= 0 ? "up" : "down"} />
          <KpiCard label="成交额" value={fmtAmount(last.amount)} tone="neutral" />
          <KpiCard label="换手率" value={fmtTurn(last.turn)} tone="clay" />
        </div>
      )}

      {code && <CompanyInfoPanel code={code} />}

      {code && (
        <Card>
          <CardHeader title="K 线" subtitle="前复权 · MA5/10/20/60 · 成交量" />
          <div className="px-2 pb-2">
            {kline.isLoading ? <Loading /> : kline.error ? <div className="p-4"><ErrorState error={kline.error} /></div> : (
              <KlineChart points={pts} />
            )}
          </div>
        </Card>
      )}

      {code && (
        <Card>
          <CardHeader title="20 日滚动波动率" subtitle="年化（日收益标准差 × √252）" />
          <div className="px-2 pb-2">
            {vol.isLoading ? <Loading /> : vol.error ? <div className="p-4"><ErrorState error={vol.error} /></div> : (
              <VolatilityChart points={vol.data ?? []} />
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
