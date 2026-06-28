import ReactECharts from "echarts-for-react";
import type { LimitPoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

export function LimitUpDownChart({ points, height = 280 }: { points: LimitPoint[]; height?: number }) {
  const dates = points.map((p) => p.date);
  const up = points.map((p) => p.limit_up);
  const down = points.map((p) => p.limit_down);

  const tooltipFormatter = (params: { dataIndex: number }[]) => {
    const i = params?.[0]?.dataIndex ?? 0;
    const p = points[i];
    if (!p) return "";
    return `
      <div style="font-weight:600;margin-bottom:4px">${p.date}</div>
      <div style="display:flex;justify-content:space-between;gap:20px">
        <span style="color:${C.up}">● 涨停</span><b style="color:${C.up}">${p.limit_up} 家</b></div>
      <div style="display:flex;justify-content:space-between;gap:20px">
        <span style="color:${C.down}">● 跌停</span><b style="color:${C.down}">${p.limit_down} 家</b></div>
    `;
  };

  const option = baseOption({
    legend: {
      data: ["涨停", "跌停"],
      top: 0,
      textStyle: { color: C.muted, fontSize: 11 },
      itemWidth: 24,
      itemHeight: 2,
    },
    grid: { left: 48, right: 18, top: 30, bottom: 28 },
    tooltip: {
      trigger: "axis",
      ...tooltipBase,
      formatter: tooltipFormatter,
    },
    xAxis: { type: "category", data: dates, ...axisBase },
    yAxis: {
      type: "value",
      name: "家数",
      nameTextStyle: { color: C.muted, fontSize: 11 },
      minInterval: 1,
      ...axisBase,
    },
    series: [
      {
        name: "涨停",
        type: "line",
        data: up,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: C.up, width: 2 },
        itemStyle: { color: C.up },
        areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: `${C.up}28` }, { offset: 1, color: `${C.up}04` }] } },
      },
      {
        name: "跌停",
        type: "line",
        data: down,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: C.down, width: 2 },
        itemStyle: { color: C.down },
        areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: `${C.down}28` }, { offset: 1, color: `${C.down}04` }] } },
      },
    ],
  });

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}
