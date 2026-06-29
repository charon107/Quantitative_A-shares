import { useState } from "react";
import { Overview } from "./pages/Overview";
import { StockQuery } from "./pages/StockQuery";
import { Rankings } from "./pages/Rankings";
import { MaDuration } from "./pages/MaDuration";
import { Status } from "./pages/Status";
import { useStatus } from "./api/client";

const PAGES = [
  { key: "overview", label: "大盘概览" },
  { key: "stock", label: "个股查询" },
  { key: "rankings", label: "排行榜" },
  { key: "maDuration", label: "多头时长" },
  { key: "status", label: "数据状态" },
] as const;

type PageKey = (typeof PAGES)[number]["key"];

interface Focus {
  start: string;
  end: string;
}

export default function App() {
  const [page, setPage] = useState<PageKey>("overview");
  const [queryCode, setQueryCode] = useState<string | null>(null);
  const [queryFocus, setQueryFocus] = useState<Focus | null>(null);
  const [queryFrom, setQueryFrom] = useState<PageKey | null>(null);
  const [maDurationPick, setMaDurationPick] = useState<number | null>(null);
  const status = useStatus();

  // 从排行榜 / 多头时长 / 当日明细点击公司名 -> 跳转个股查询并载入该股
  // focus：可选的 K线聚焦区间；from：可选的来源页（用于返回键）
  const openStock = (code: string, focus?: Focus | null, from?: PageKey) => {
    setQueryCode(code);
    setQueryFocus(focus ?? null);
    setQueryFrom(from ?? null);
    setPage("stock");
  };

  // 多头时长 → 个股查询：携带 pick（持续天数），返回时可还原排行表
  const openStockFromMaDuration = (code: string, focus: { start: string; end: string }, pick: number) => {
    setMaDurationPick(pick);
    openStock(code, focus, "maDuration");
  };

  return (
    <div className="min-h-screen bg-cream">
      <header className="sticky top-0 z-30 border-b border-line bg-cream/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-clay to-clayDark shadow-soft ring-1 ring-clayDark/20"
              aria-hidden
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeLinecap="round">
                <line x1="8" y1="3.5" x2="8" y2="20.5" strokeWidth="1.8" />
                <rect x="5.4" y="7" width="5.2" height="8" rx="1.3" fill="#fff" />
                <line x1="16" y1="5.5" x2="16" y2="18.5" strokeWidth="1.8" />
                <rect x="13.4" y="9.5" width="5.2" height="6" rx="1.3" fill="#fff" />
              </svg>
            </span>
            <div className="flex flex-col leading-none">
              <h1 className="text-[1.35rem] font-semibold tracking-tight text-ink">A股股价看板</h1>
              <span className="mt-1 text-[11px] font-medium uppercase tracking-[0.2em] text-clay/80">
                A-SHARE DASHBOARD
              </span>
            </div>
          </div>
          {status.data?.latest_date && (
            <span className="nums text-xs text-muted">
              数据更新至 {status.data.latest_date} · {status.data.n_codes} 只
            </span>
          )}
        </div>
        <nav className="mx-auto max-w-6xl px-5">
          <div className="flex gap-1 overflow-x-auto">
            {PAGES.map((p) => (
              <button
                key={p.key}
                onClick={() => setPage(p.key)}
                className={`relative whitespace-nowrap px-3 py-2.5 text-sm font-medium transition ${
                  page === p.key ? "text-clay" : "text-muted hover:text-ink"
                }`}
              >
                {p.label}
                {page === p.key && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-clay" />}
              </button>
            ))}
          </div>
        </nav>
      </header>

      <main className="mx-auto max-w-6xl px-5 py-7">
        {page === "overview" && <Overview onOpenStock={openStock} />}
        {page === "stock" && (
          <StockQuery
            key={`${queryCode ?? "none"}-${queryFocus?.start ?? ""}`}
            initialCode={queryCode}
            focus={queryFocus}
            onBack={queryFrom ? () => setPage(queryFrom) : undefined}
            backLabel={queryFrom === "maDuration" ? "返回多头时长" : "返回"}
          />
        )}
        {page === "rankings" && <Rankings onOpenStock={openStock} />}
        {page === "maDuration" && (
          <MaDuration
            initialPick={maDurationPick}
            onClearPick={() => setMaDurationPick(null)}
            onOpenStock={openStockFromMaDuration}
          />
        )}
        {page === "status" && <Status />}
      </main>
    </div>
  );
}
