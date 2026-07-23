import { useQuotes } from "../api/client";
import type { QuoteRow } from "../api/types";
import { Card, CardHeader } from "../components/Card";
import { FavoriteStar } from "../components/FavoriteStar";
import { Empty, ErrorState, Loading } from "../components/States";
import { fmtPct, fmtPrice, signClass } from "../lib/format";
import { useFavorites, type Favorite } from "../lib/favorites";

function FavoriteRow({
  fav,
  quote,
  onPick,
}: {
  fav: Favorite;
  quote: QuoteRow | undefined;
  onPick: (code: string) => void;
}) {
  const name = quote?.code_name ?? fav.name;
  return (
    <button
      onClick={() => onPick(fav.code)}
      className="grid w-full grid-cols-[26px_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-3 py-2 text-left transition hover:bg-panel2"
    >
      <FavoriteStar code={fav.code} name={name} />
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="truncate font-medium text-ink">{name ?? fav.code}</span>
          <span className="nums shrink-0 text-[11px] text-muted">{fav.code}</span>
        </div>
        {quote?.date && <div className="mt-0.5 text-[10px] text-muted">截至 {quote.date}</div>}
      </div>
      <div className="flex shrink-0 items-center gap-4">
        <span className="nums w-16 text-right text-sm text-ink">{fmtPrice(quote?.close ?? null)}</span>
        <span className={`nums w-16 text-right text-sm ${signClass(quote?.pctChg ?? null)}`}>
          {fmtPct(quote?.pctChg ?? null)}
        </span>
      </div>
    </button>
  );
}

/** 我的收藏：收藏股票列表（最新价/涨跌幅），点击直达个股 K线。 */
export function Favorites({ onOpenStock }: { onOpenStock: (code: string) => void }) {
  const { favorites } = useFavorites();
  const codes = favorites.map((f) => f.code);
  const q = useQuotes(codes);
  const byCode = new Map((q.data ?? []).map((r) => [r.code, r]));

  if (favorites.length === 0) {
    return (
      <Card>
        <CardHeader title="我的收藏" subtitle="收藏的股票会显示在这里" />
        <Empty label="还没有收藏股票。在「个股查询」页点击股票名称旁的 ☆ 即可收藏。" />
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader title="我的收藏" subtitle={`共 ${favorites.length} 只 · 按收藏时间排序`} />
      <div className="px-2 pb-3">
        {q.isLoading ? (
          <Loading />
        ) : q.error ? (
          <div className="p-4">
            <ErrorState error={q.error} />
          </div>
        ) : (
          favorites.map((f) => <FavoriteRow key={f.code} fav={f} quote={byCode.get(f.code)} onPick={onOpenStock} />)
        )}
      </div>
    </Card>
  );
}
