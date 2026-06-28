import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import type { KlinePoint } from "../api/types";
import { C, MA_COLORS, axisBase, baseOption, tooltipBase } from "../theme/echarts";

export function KlineChart({ points, height = 460 }: { points: KlinePoint[]; height?: number }) {
  const dates = points.map((p) => p.date);
  // ECharts 蜡烛顺序：[open, close, low, high]
  const candles = points.map((p) => [p.open, p.close, p.low, p.high]);
  const vols = points.map((p) => ({
    value: p.volume == null ? 0 : p.volume / 1e4, // 万股
    itemStyle: { color: (p.close ?? 0) >= (p.open ?? 0) ? `${C.up}99` : `${C.down}99` },
  }));

  const maSeries = ([5, 10, 20, 60] as const).map((w, i) => ({
    name: `MA${w}`,
    type: "line" as const,
    data: points.map((p) => p[`MA${w}` as keyof KlinePoint] as number | null),
    smooth: true,
    showSymbol: false,
    lineStyle: { color: MA_COLORS[i], width: 1.2 },
    itemStyle: { color: MA_COLORS[i] },
  }));

  const option: EChartsOption = baseOption({
    legend: {
      data: ["MA5", "MA10", "MA20", "MA60"],
      top: 4,
      textStyle: { color: C.muted, fontSize: 11, fontFamily: "'IBM Plex Mono', monospace" },
      itemWidth: 14,
      itemHeight: 2,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", lineStyle: { color: C.muted, type: "dashed" } },
      ...tooltipBase,
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 56, right: 18, top: 36, height: "62%" },
      { left: 56, right: 18, top: "74%", height: "16%" },
    ],
    xAxis: [
      { type: "category", data: dates, gridIndex: 0, ...axisBase, boundaryGap: true, axisLabel: { ...axisBase.axisLabel, show: false } },
      { type: "category", data: dates, gridIndex: 1, ...axisBase, boundaryGap: true },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, position: "right", ...axisBase },
      { gridIndex: 1, position: "right", splitNumber: 2, ...axisBase, axisLabel: { ...axisBase.axisLabel } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: 60, end: 100 },
      {
        type: "slider",
        xAxisIndex: [0, 1],
        bottom: 6,
        height: 16,
        borderColor: C.line,
        fillerColor: "rgba(204,120,92,0.12)",
        handleStyle: { color: C.clay },
        dataBackground: { lineStyle: { color: C.line }, areaStyle: { color: C.grid } },
        textStyle: { color: C.muted, fontSize: 10 },
      },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: candles,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: C.panel, // 阳线空心
          color0: C.down, // 阴线实心
          borderColor: C.up,
          borderColor0: C.down,
          borderWidth: 1.2,
        },
      },
      ...maSeries.map((s) => ({ ...s, xAxisIndex: 0, yAxisIndex: 0 })),
      {
        name: "成交量(万股)",
        type: "bar",
        data: vols,
        xAxisIndex: 1,
        yAxisIndex: 1,
      },
    ],
  });

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}
