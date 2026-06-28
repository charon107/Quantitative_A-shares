import ReactECharts from "echarts-for-react";
import type { IndexPoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

export function IndexLineChart({ points, height = 320 }: { points: IndexPoint[]; height?: number }) {
  const dates = points.map((p) => p.date);
  const vals = points.map((p) => (p.value == null ? null : +(p.value * 100).toFixed(2)));

  const option = baseOption({
    grid: { left: 52, right: 18, top: 18, bottom: 28 },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: number) => `${v}%`,
      axisPointer: { type: "line", lineStyle: { color: C.muted, type: "dashed" } },
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
        lineStyle: { color: C.clay, width: 2 },
        itemStyle: { color: C.clay },
        areaStyle: {
          color: {
            type: "linear",
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(204,120,92,0.20)" },
              { offset: 1, color: "rgba(204,120,92,0.02)" },
            ],
          },
        },
      },
    ],
  });

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}
