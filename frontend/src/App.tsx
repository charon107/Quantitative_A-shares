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
  const status = useStatus();

  // 从排行榜 / 多头时长 / 当日明细点击公司名 -> 跳转个股查询并载入该股
  // focus：可选的 K线聚焦区间；from：可选的来源页（用于返回键）
  const openStock = (code: string, focus?: Focus | null, from?: PageKey) => {
    setQueryCode(code);
    setQueryFocus(focus ?? null);
    setQueryFrom(from ?? null);
    setPage("stock");
  };

  return (
    <div className="min-h-screen bg-cream">
      <header className="sticky top-0 z-30 border-b border-line bg-cream/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-4">
          <div className="flex items-baseline gap-3">
            <span className="h-5 w-5 rounded-md bg-clay" aria-hidden />
            <h1 className="text-xl font-semibold tracking-tight">A股股价看板</h1>
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
          <MaDuration onOpenStock={(code, focus) => openStock(code, focus, "maDuration")} />
        )}
        {page === "status" && <Status />}
      </main>
    </div>
  );
}
