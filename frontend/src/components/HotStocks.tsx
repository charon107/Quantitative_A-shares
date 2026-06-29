import { useHotStocks } from "../api/client";
import type { HotStock } from "../api/types";
import { Card } from "./Card";
import { ErrorState, Loading } from "./States";
import { fmtPct, fmtPrice, signClass } from "../lib/format";

function parseConcepts(s: string | null): string[] {
  if (!s) return [];
  try {
    const arr = JSON.parse(s);
    return Array.isArray(arr) ? arr.map(String).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function fmtHot(v: number | null): string {
  if (v == null) return "—";
  return v >= 10000 ? `${(v / 10000).toFixed(1)} 万` : `${v}`;
}

function HotRow({ h, onPick }: { h: HotStock; onPick: (code: string) => void }) {
  const concepts = parseConcepts(h.concept).slice(0, 2);
  return (
    <button
      onClick={() => onPick(h.code)}
      className="grid w-full grid-cols-[26px_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-3 py-2 text-left transition hover:bg-panel2"
    >
      <span className="nums text-center text-sm font-semibold text-clay">{h.rank_no ?? "·"}</span>
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="truncate font-medium text-ink">{h.code_name ?? h.code}</span>
          <span className="nums shrink-0 text-[11px] text-muted">{h.code}</span>
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1">
          {concepts.map((c) => (
            <span key={c} className="rounded bg-panel2 px-1.5 py-0.5 text-[10px] text-muted">
              {c}
            </span>
          ))}
          {h.rank_reason && <span className="truncate text-[10px] text-clay/70">{h.rank_reason}</span>}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-4">
        <span className="nums w-16 text-right text-sm text-ink">{fmtPrice(h.current_price)}</span>
        <span className={`nums w-16 text-right text-sm ${signClass(h.pct_change)}`}>{fmtPct(h.pct_change)}</span>
        <span className="nums hidden w-16 text-right text-xs text-muted sm:block" title="同花顺热度值">
          {fmtHot(h.hot)}
        </span>
      </div>
    </button>
  );
}

/** 个股查询页未搜索时的默认内容：同花顺人气榜（点击直达个股 K线）。 */
export function HotStocks({ onPick }: { onPick: (code: string) => void }) {
  const q = useHotStocks();
  const rows = q.data ?? [];
  const date = rows[0]?.trade_date;

  return (
    <div>
      <p className="mb-3 text-sm text-muted">
        输入代码或名称搜索个股，或点击下方<span className="text-clay">人气榜</span>直接查看 K 线。
      </p>
      <Card>
        <div className="flex items-baseline justify-between px-4 pt-4 pb-1">
          <h3 className="text-sm font-semibold text-ink">同花顺人气榜</h3>
          <span className="text-[11px] text-muted">
            市场今日关注热度{date ? ` · 截至 ${date}` : ""}
          </span>
        </div>
        <div className="px-2 pb-3">
          {q.isLoading ? (
            <Loading />
          ) : q.error ? (
            <div className="p-4"><ErrorState error={q.error} /></div>
          ) : rows.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted">暂无人气榜数据</div>
          ) : (
            rows.map((h) => <HotRow key={h.code} h={h} onPick={onPick} />)
          )}
        </div>
      </Card>
    </div>
  );
}
