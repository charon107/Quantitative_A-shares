import { memo } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import type { ValuationPoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

interface ValuationChartProps {
  points: ValuationPoint[];
  height?: number | string;
}

// 默认缩放到最近 ~3 年（约 750 个交易日）
const DEFAULT_DAYS = 750;

interface TooltipParam {
  marker?: string;
  seriesName?: string;
  value?: unknown;
  axisValueLabel?: string;
}

const FMT_BY_SERIES: Record<string, (v: number) => string> = {
  "PE(TTM)": (v) => v.toFixed(2),
  PB: (v) => v.toFixed(2),
  总市值: (v) => `${v.toFixed(0)} 亿`,
  "股息率(TTM)": (v) => `${v.toFixed(2)}%`,
};

/**
 * 估值双联图（共享 X 轴）：
 * ① PE(TTM) 左轴 + PB 右轴
 * ② 总市值（亿元）左轴 + 股息率TTM（%）右轴
 */
function ValuationChartImpl({ points, height = 420 }: ValuationChartProps) {
  const dates = points.map((p) => p.date);
  const zoomStart = Math.max(0, ((dates.length - DEFAULT_DAYS) / Math.max(dates.length, 1)) * 100);

  const peTtm = points.map((p) => p.pe_ttm);
  const pb = points.map((p) => p.pb);
  const totalMv = points.map((p) => (p.total_mv == null ? null : p.total_mv / 1e4)); // 万元 -> 亿
  const dvTtm = points.map((p) => p.dv_ttm);

  const option: EChartsOption = baseOption({
    legend: {
      data: ["PE(TTM)", "PB", "总市值", "股息率(TTM)"],
      top: 4,
      textStyle: { color: C.muted, fontSize: 11, fontFamily: "'IBM Plex Mono', monospace" },
      itemWidth: 16,
      itemHeight: 3,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", lineStyle: { color: C.muted, type: "dashed" } },
      ...tooltipBase,
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as TooltipParam[];
        const head = arr[0]?.axisValueLabel ?? "";
        const lines = arr.map((p) => {
          const fmt = FMT_BY_SERIES[p.seriesName ?? ""];
          const text = typeof p.value === "number" && fmt ? fmt(p.value) : "—";
          return `${p.marker ?? ""}${p.seriesName}: ${text}`;
        });
        return [head, ...lines].join("<br/>");
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 56, right: 56, top: 34, height: "36%" },
      { left: 56, right: 56, top: "56%", height: "30%" },
    ],
    xAxis: [
      { type: "category", data: dates, gridIndex: 0, ...axisBase, boundaryGap: false, axisLabel: { ...axisBase.axisLabel, show: false } },
      { type: "category", data: dates, gridIndex: 1, ...axisBase, boundaryGap: false },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, position: "left", name: "PE", ...axisBase },
      { scale: true, gridIndex: 0, position: "right", name: "PB", ...axisBase, splitLine: { show: false } },
      { scale: true, gridIndex: 1, position: "left", name: "亿元", ...axisBase },
      { scale: true, gridIndex: 1, position: "right", name: "%", ...axisBase, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: zoomStart, end: 100 },
      {
        type: "slider",
        xAxisIndex: [0, 1],
        start: zoomStart,
        end: 100,
        bottom: 4,
        height: 16,
        borderColor: C.line,
        fillerColor: "rgba(204,120,92,0.12)",
        handleStyle: { color: C.clay },
        textStyle: { color: C.muted, fontSize: 10 },
      },
    ],
    series: [
      {
        name: "PE(TTM)",
        type: "line",
        data: peTtm,
        xAxisIndex: 0,
        yAxisIndex: 0,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { color: C.clay, width: 1.4 },
        itemStyle: { color: C.clay },
      },
      {
        name: "PB",
        type: "line",
        data: pb,
        xAxisIndex: 0,
        yAxisIndex: 1,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { color: C.blue, width: 1.4 },
        itemStyle: { color: C.blue },
      },
      {
        name: "总市值",
        type: "line",
        data: totalMv,
        xAxisIndex: 1,
        yAxisIndex: 2,
        connectNulls: false,
        showSymbol: false,
        areaStyle: { color: "rgba(217,164,65,0.12)" },
        lineStyle: { color: C.amber, width: 1.2 },
        itemStyle: { color: C.amber },
      },
      {
        name: "股息率(TTM)",
        type: "line",
        data: dvTtm,
        xAxisIndex: 1,
        yAxisIndex: 3,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { color: C.down, width: 1.2, type: "dashed" },
        itemStyle: { color: C.down },
      },
    ],
  });

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}

export const ValuationChart = memo(ValuationChartImpl);
