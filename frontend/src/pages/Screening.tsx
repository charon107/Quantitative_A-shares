import { useEffect, useState } from "react";
import { useScreening, useScreeningChart, useScreeningYears } from "../api/client";
import { Card, CardHeader } from "../components/Card";
import { ErrorState, Loading } from "../components/States";
import { ScreeningChart } from "../charts/ScreeningChart";

/** 小数比率 -> 百分比字符串（roe/净利增速/总债务比后端已归一为小数）。 */
const pct = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);
/** 净利润：元 -> 亿。 */
const yi = (v: number | null) => (v == null ? "—" : `${(v / 1e8).toFixed(2)} 亿`);

export function Screening({ onOpenStock }: { onOpenStock: (code: string) => void }) {
  const years = useScreeningYears();
  const [year, setYear] = useState<number | null>(null);
  const [picked, setPicked] = useState<string | null>(null);

  // 默认选中最新一年
  useEffect(() => {
    if (year == null && years.data && years.data.length > 0) {
      setYear(years.data[years.data.length - 1]);
    }
  }, [years.data, year]);

  const list = useScreening(year);
  const chart = useScreeningChart(year, picked);
  const rows = list.data ?? [];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="基本面选股"
          subtitle="逐年回测池 · 近5年 ROE≥15%(最低≥10%) / 净利增速≥10% / 净利CAGR≥15% / 总债务/总资产<50% · 剔除银行股"
        />
        {years.data && years.data.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-line/60 px-5 pb-3">
            <span className="text-xs text-muted">选股年份</span>
            <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-panel2 p-1">
              {years.data.map((y) => (
                <button
                  key={y}
                  onClick={() => {
                    setYear(y);
                    setPicked(null);
                  }}
                  aria-pressed={year === y}
                  className={`min-w-[3.25rem] rounded-md px-3 py-1.5 text-sm font-medium transition ${
                    year === y
                      ? "bg-panel text-clay shadow-soft ring-1 ring-line"
                      : "text-muted hover:bg-panel/60 hover:text-ink"
                  }`}
                >
                  {y}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="px-2 pb-4">
          {years.isLoading || list.isLoading ? (
            <Loading />
          ) : list.error ? (
            <div className="p-4"><ErrorState error={list.error} /></div>
          ) : rows.length === 0 ? (
            <div className="p-6 text-center text-sm text-muted">该年份无选股结果</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">#</th>
                    <th className="px-3 py-2 font-medium">名称</th>
                    <th className="px-3 py-2 font-medium">代码</th>
                    <th className="px-3 py-2 text-right font-medium">ROE</th>
                    <th className="px-3 py-2 text-right font-medium">净利增速</th>
                    <th className="px-3 py-2 text-right font-medium">总债务/总资产</th>
                    <th className="px-3 py-2 text-right font-medium">净利润</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => (
                    <tr
                      key={row.code}
                      onClick={() => setPicked(row.code)}
                      className={`cursor-pointer border-b border-line/60 transition hover:bg-panel2 ${
                        picked === row.code ? "bg-clay/5" : ""
                      }`}
                    >
                      <td className="px-3 py-2 nums text-muted">{i + 1}</td>
                      <td className="px-3 py-2">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenStock(row.code);
                          }}
                          className="font-medium text-ink underline-offset-2 hover:text-clay hover:underline"
                          title="查看个股"
                        >
                          {row.code_name ?? "—"}
                        </button>
                      </td>
                      <td className="px-3 py-2 nums text-xs text-muted">{row.code}</td>
                      <td className="px-3 py-2 text-right nums">{pct(row.roe)}</td>
                      <td className="px-3 py-2 text-right nums">{pct(row.netprofit_yoy)}</td>
                      <td className="px-3 py-2 text-right nums">{pct(row.debt_ratio)}</td>
                      <td className="px-3 py-2 text-right nums">{yi(row.net_profit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>

      {picked && year != null && (
        <Card>
          <CardHeader
            title={`股价图 · ${chart.data?.code_name ?? picked}`}
            subtitle={`${year} 年初起 · 截止 ${chart.data?.next_pub ?? "最新"} · 虚线为财报公布日 · 下方上证指数对照`}
            right={
              <div className="flex items-center gap-3">
                <button onClick={() => onOpenStock(picked)} className="text-xs text-clay hover:underline">
                  在个股查询打开
                </button>
                <button onClick={() => setPicked(null)} className="text-xs text-muted hover:text-clay">
                  收起
                </button>
              </div>
            }
          />
          <div className="px-2 pb-2">
            {chart.isLoading ? (
              <Loading />
            ) : chart.error ? (
              <div className="p-4"><ErrorState error={chart.error} /></div>
            ) : (
              <ScreeningChart
                stock={chart.data?.stock ?? []}
                index={chart.data?.index ?? []}
                pubDates={chart.data?.pub_dates ?? []}
                nextPub={chart.data?.next_pub ?? null}
              />
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
