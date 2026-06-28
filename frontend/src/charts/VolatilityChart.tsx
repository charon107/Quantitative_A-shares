import ReactECharts from "echarts-for-react";
import type { VolatilityPoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

export function VolatilityChart({ points, height = 240 }: { points: VolatilityPoint[]; height?: number }) {
  const dates = points.map((p) => p.date);
  const vals = points.map((p) => (p.value == null ? null : +(p.value * 100).toFixed(2)));

  const option = baseOption({
    grid: { left: 52, right: 18, top: 16, bottom: 28 },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: number) => (v == null ? "—" : `${v}%`),
      ...tooltipBase,
    },
    xAxis: { type: "category", data: dates, boundaryGap: false, ...axisBase },
    yAxis: { type: "value", ...axisBase, axisLabel: { ...axisBase.axisLabel, formatter: "{value}%" } },
    series: [
      {
        type: "line",
        data: vals,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: C.blue, width: 1.6 },
        itemStyle: { color: C.blue },
      },
    ],
  });

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}
