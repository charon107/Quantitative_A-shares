import ReactECharts from "echarts-for-react";
import type { IndexPoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

export interface SeriesConfig {
  name: string;
  points: IndexPoint[];
  color: string;
}

export function IndexLineChart({
  series,
  height = 340,
}: {
  series: SeriesConfig[];
  height?: number;
}) {
  if (!series.length) return null;

  const longest = series.reduce((a, b) =>
    a.points.length >= b.points.length ? a : b,
  );
  const dates = longest.points.map((p) => p.date);

  const seriesDefs = series.map((s) => {
    const valMap = new Map(s.points.map((p) => [p.date, +(p.value! * 100).toFixed(2)]));
    const data = dates.map((d) => valMap.get(d) ?? null);

    return {
      name: s.name,
      type: "line" as const,
      data,
      smooth: true,
      showSymbol: false,
      lineStyle: { color: s.color, width: 2 },
      itemStyle: { color: s.color },
    };
  });

  const tooltipFormatter = (params: { seriesName: string; dataIndex: number; value: number | null }[]) => {
    const i = params?.[0]?.dataIndex ?? 0;
    const rows = params
      .filter((s) => s.value != null)
      .map(
        (s) =>
          `<div style="display:flex;justify-content:space-between;gap:22px"><span style="color:${C.muted}">${s.seriesName}</span><b>${s.value}%</b></div>`,
      )
      .join("");
    return `<div style="font-weight:600;margin-bottom:4px">${dates[i]}</div>${rows}`;
  };

  const option = baseOption({
    legend: {
      data: seriesDefs.map((s) => s.name),
      top: 0,
      textStyle: { color: C.muted, fontSize: 11 },
      itemWidth: 24,
      itemHeight: 2,
    },
    grid: { left: 52, right: 18, top: 30, bottom: 28 },
    tooltip: {
      trigger: "axis",
      ...tooltipBase,
      formatter: tooltipFormatter,
    },
    xAxis: { type: "category", data: dates, boundaryGap: false, ...axisBase },
    yAxis: {
      type: "value",
      ...axisBase,
      axisLabel: { ...axisBase.axisLabel, formatter: "{value}%" },
    },
    series: seriesDefs,
  });

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}
