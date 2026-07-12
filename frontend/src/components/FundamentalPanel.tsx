import { useState } from "react";
import { useQuarterlyFundamental } from "../api/client";
import { Card, CardHeader } from "./Card";
import { Loading } from "./States";
import { QuarterlyFundamentalChart } from "../charts/QuarterlyFundamentalChart";
import type { FundamentalMode } from "../charts/QuarterlyFundamentalChart";

const MODES: { key: FundamentalMode; label: string }[] = [
  { key: "cum", label: "累计" },
  { key: "single", label: "单季" },
];

export function FundamentalPanel({ code }: { code: string }) {
  const q = useQuarterlyFundamental(code);
  const [mode, setMode] = useState<FundamentalMode>("cum");

  if (q.isLoading) {
    return (
      <Card>
        <CardHeader title="季度基本面" />
        <Loading />
      </Card>
    );
  }
  if (q.error || !q.data || q.data.points.length === 0) return null; // 无数据时静默隐藏

  return (
    <Card>
      <CardHeader
        title="季度基本面"
        subtitle={
          mode === "cum"
            ? "累计口径（年初至报告期末）· 总债务=短借+长借+应付债券（有息）"
            : "单季口径（相邻累计差分，Q1=累计）· 缺口断线不插值 · 总债务=短借+长借+应付债券"
        }
        right={
          <div className="inline-flex rounded-lg border border-line bg-panel2 p-0.5">
            {MODES.map((m) => (
              <button
                key={m.key}
                onClick={() => setMode(m.key)}
                className={`rounded-md px-3 py-1 text-xs font-medium transition ${
                  mode === m.key ? "bg-panel text-clay shadow-soft" : "text-muted hover:text-ink"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        }
      />
      <div className="px-2 pb-2">
        <QuarterlyFundamentalChart points={q.data.points} mode={mode} />
      </div>
    </Card>
  );
}
