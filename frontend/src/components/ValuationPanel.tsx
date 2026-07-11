import { useValuation } from "../api/client";
import { Card, CardHeader } from "./Card";
import { Loading } from "./States";
import { ValuationChart } from "../charts/ValuationChart";

export function ValuationPanel({ code }: { code: string }) {
  const q = useValuation(code);

  if (q.isLoading) {
    return (
      <Card>
        <CardHeader title="估值" />
        <Loading />
      </Card>
    );
  }
  if (q.error || !q.data || q.data.points.length === 0) return null; // 无数据时静默隐藏

  return (
    <Card>
      <CardHeader title="估值" subtitle="PE(TTM) / PB · 总市值 / 股息率 · 日频" />
      <div className="px-2 pb-2">
        <ValuationChart points={q.data.points} />
      </div>
    </Card>
  );
}
