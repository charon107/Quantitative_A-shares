import { useRankings } from "../api/client";
import type { RankMetric } from "../api/types";
import { Card } from "./Card";
import { Loading } from "./States";
import { fmtAmount, fmtPct, fmtPrice, signClass } from "../lib/format";

const N = 6;

interface MiniListProps {
  title: string;
  metric: RankMetric;
  ascending: boolean;
  valueKind: "pct" | "amount";
  onPick: (code: string) => void;
}

function MiniList({ title, metric, ascending, valueKind, onPick }: MiniListProps) {
  const q = useRankings(metric, N, ascending);
  const rows = q.data ?? [];

  return (
    <Card>
      <div className="flex items-center justify-between px-3 pt-3 pb-1.5">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        <span className="text-[11px] text-muted">Top {N}</span>
      </div>
      <div className="px-1.5 pb-2">
        {q.isLoading ? (
          <Loading />
        ) : rows.length === 0 ? (
          <div className="py-6 text-center text-xs text-muted">暂无数据</div>
        ) : (
          rows.map((r, i) => (
            <button
              key={r.code}
              onClick={() => onPick(r.code)}
              className="grid w-full grid-cols-[18px_minmax(0,1fr)_56px_84px] items-center gap-2 rounded-lg px-2 py-1.5 text-sm transition hover:bg-panel2"
            >
              <span className="nums text-right text-xs text-muted">{i + 1}</span>
              <span className="flex min-w-0 items-baseline gap-1.5">
                <span className="truncate font-medium text-ink">{r.code_name ?? r.code}</span>
                <span className="nums hidden shrink-0 text-[11px] text-muted sm:inline">{r.code}</span>
              </span>
              <span className="nums text-right text-ink">{fmtPrice(r.close)}</span>
              <span
                className={`nums whitespace-nowrap text-right ${valueKind === "amount" ? "text-muted" : signClass(r.pctChg)}`}
              >
                {valueKind === "amount" ? fmtAmount(r.amount) : fmtPct(r.pctChg)}
              </span>
            </button>
          ))
        )}
      </div>
    </Card>
  );
}

/** 个股查询页未搜索时的默认内容：今日异动榜（点击任一只直接载入该股）。 */
export function StockHighlights({ onPick }: { onPick: (code: string) => void }) {
  return (
    <div>
      <p className="mb-3 text-sm text-muted">
        输入代码或名称搜索个股，或点击下方<span className="text-clay">今日异动</span>直接查看 K 线。
      </p>
      <div className="grid gap-4 md:grid-cols-3">
        <MiniList title="涨幅榜" metric="pctChg" ascending={false} valueKind="pct" onPick={onPick} />
        <MiniList title="跌幅榜" metric="pctChg" ascending valueKind="pct" onPick={onPick} />
        <MiniList title="成交额榜" metric="amount" ascending={false} valueKind="amount" onPick={onPick} />
      </div>
    </div>
  );
}
