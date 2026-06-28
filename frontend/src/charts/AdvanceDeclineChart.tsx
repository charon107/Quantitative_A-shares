import ReactECharts from "echarts-for-react";
import type { BreadthPoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

export function AdvanceDeclineChart({
  points,
  onPick,
  height = 340,
}: {
  points: BreadthPoint[];
  onPick?: (date: string) => void;
  height?: number;
}) {
  const dates = points.map((p) => p.date);
  const up = points.map((p) => p.up);
  const down = points.map((p) => p.down);

  // 自定义 tooltip：一天的 上涨/下跌/涨跌比 + 涨停/跌停 精确数字
  const tooltipFormatter = (params: { dataIndex: number }[]) => {
    const i = params?.[0]?.dataIndex ?? 0;
    const p = points[i];
    if (!p) return "";
    const ratio = p.down === 0 ? "—" : (p.up / p.down).toFixed(2);
    return `
      <div style="font-weight:600;margin-bottom:4px">${p.date}</div>
      <div style="display:flex;justify-content:space-between;gap:18px">
        <span style="color:${C.up}">● 上涨</span><b>${p.up.toLocaleString()}</b></div>
      <div style="display:flex;justify-content:space-between;gap:18px">
        <span style="color:${C.down}">● 下跌</span><b>${p.down.toLocaleString()}</b></div>
      <div style="display:flex;justify-content:space-between;gap:18px;color:${C.muted}">
        <span>涨跌比</span><b style="color:${C.ink}">${ratio}</b></div>
      <div style="border-top:1px solid ${C.line};margin:5px 0"></div>
      <div style="display:flex;justify-content:space-between;gap:18px;color:${C.muted}">
        <span>涨停</span><b style="color:${C.up}">${p.limit_up}</b></div>
      <div style="display:flex;justify-content:space-between;gap:18px;color:${C.muted}">
        <span>跌停</span><b style="color:${C.down}">${p.limit_down}</b></div>
    `;
  };

  const option = baseOption({
    legend: {
      data: ["上涨", "下跌"],
      top: 0,
      textStyle: { color: C.muted, fontSize: 12 },
      itemWidth: 12,
      itemHeight: 12,
    },
    grid: { left: 48, right: 16, top: 30, bottom: 28 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      ...tooltipBase,
      formatter: tooltipFormatter,
    },
    xAxis: { type: "category", data: dates, ...axisBase },
    yAxis: { type: "value", name: "家数", nameTextStyle: { color: C.muted, fontSize: 11 }, ...axisBase },
    series: [
      { name: "上涨", type: "bar", stack: "ad", data: up, itemStyle: { color: `${C.up}D9` }, barMaxWidth: 22 },
      { name: "下跌", type: "bar", stack: "ad", data: down, itemStyle: { color: `${C.down}D9` }, barMaxWidth: 22 },
    ],
  });

  return (
    <ReactECharts
      option={option}
      style={{ height }}
      notMerge
      lazyUpdate
      onEvents={{
        click: (p: { dataIndex?: number }) => {
          if (onPick && p.dataIndex != null && points[p.dataIndex]) onPick(points[p.dataIndex].date);
        },
      }}
    />
  );
}
