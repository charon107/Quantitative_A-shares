import ReactECharts from "echarts-for-react";
import type { LimitPoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

export function LimitUpDownChart({ points, height = 320 }: { points: LimitPoint[]; height?: number }) {
  const dates = points.map((p) => p.date);
  const up = points.map((p) => p.limit_up);
  const down = points.map((p) => -p.limit_down); // 向下镜像

  const option = baseOption({
    legend: {
      data: ["涨停", "跌停"],
      top: 0,
      textStyle: { color: C.muted, fontSize: 11 },
      itemWidth: 12,
      itemHeight: 12,
    },
    grid: { left: 48, right: 18, top: 28, bottom: 28 },
    tooltip: {
      trigger: "axis",
      ...tooltipBase,
      valueFormatter: (v: number) => `${Math.abs(v)} 家`,
    },
    xAxis: { type: "category", data: dates, ...axisBase },
    yAxis: {
      type: "value",
      ...axisBase,
      axisLabel: { ...axisBase.axisLabel, formatter: (v: number) => `${Math.abs(v)}` },
    },
    series: [
      { name: "涨停", type: "bar", stack: "total", data: up, itemStyle: { color: `${C.up}CC` }, barMaxWidth: 14 },
      { name: "跌停", type: "bar", stack: "total", data: down, itemStyle: { color: `${C.down}CC` }, barMaxWidth: 14 },
    ],
  });

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}
