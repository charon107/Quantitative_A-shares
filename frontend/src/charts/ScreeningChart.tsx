import { memo } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import type { ChartLinePoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

const MA_N = 20;

interface ScreeningChartProps {
  stock: ChartLinePoint[];
  index: ChartLinePoint[];
  pubDates: string[];
  nextPub: string | null;
  maN?: number;
  height?: number | string;
}

/**
 * 复刻原 matplotlib 看图脚本的三特性：
 * ① 财报公布日竖线（当年公布的报告，虚线 markLine）
 * ② 价格已由后端截断到「次年首个财报公布日」next_pub（醒目 markLine 标注终点）
 * ③ 下方叠加上证指数对照子图（共享 X 轴）
 */
function ScreeningChartImpl({
  stock,
  index,
  pubDates,
  nextPub,
  maN = MA_N,
  height = 520,
}: ScreeningChartProps) {
  const dates = stock.map((p) => p.date);

  const pubMarkLines = pubDates
    .filter((d) => dates.length === 0 || (d >= dates[0] && d <= dates[dates.length - 1]))
    .map((d) => ({ xAxis: d, lineStyle: { color: C.muted, type: "dashed" as const, width: 1 } }));

  const nextMarkLine =
    nextPub && (dates.length === 0 || nextPub >= dates[0])
      ? [
          {
            xAxis: nextPub,
            lineStyle: { color: C.clay, type: "dashdot" as const, width: 1.6 },
            label: { show: true, formatter: `Next\n${nextPub}`, color: C.clay, fontSize: 10, position: "insideEndTop" as const },
          },
        ]
      : [];

  const f2 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));

  const option: EChartsOption = baseOption({
    legend: {
      data: ["收盘价", `MA${maN}`, "上证指数"],
      top: 4,
      textStyle: { color: C.muted, fontSize: 11, fontFamily: "'IBM Plex Mono', monospace" },
      itemWidth: 16,
      itemHeight: 3,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", lineStyle: { color: C.muted, type: "dashed" } },
      ...tooltipBase,
      valueFormatter: (v: unknown) => f2(typeof v === "number" ? v : null),
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 56, right: 18, top: 36, height: "56%" },
      { left: 56, right: 18, top: "72%", height: "18%" },
    ],
    xAxis: [
      { type: "category", data: dates, gridIndex: 0, ...axisBase, boundaryGap: false, axisLabel: { ...axisBase.axisLabel, show: false } },
      { type: "category", data: index.map((p) => p.date), gridIndex: 1, ...axisBase, boundaryGap: false },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, position: "right", name: "收盘价", ...axisBase },
      { scale: true, gridIndex: 1, position: "right", name: "上证", ...axisBase },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: 0, end: 100 },
      {
        type: "slider",
        xAxisIndex: [0, 1],
        start: 0,
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
        name: "收盘价",
        type: "line",
        data: stock.map((p) => p.close),
        xAxisIndex: 0,
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { color: C.clay, width: 1.4 },
        itemStyle: { color: C.clay },
        markLine:
          pubMarkLines.length || nextMarkLine.length
            ? { symbol: "none", silent: true, data: [...pubMarkLines, ...nextMarkLine] }
            : undefined,
      },
      {
        name: `MA${maN}`,
        type: "line",
        data: stock.map((p) => p.ma),
        xAxisIndex: 0,
        yAxisIndex: 0,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: C.blue, width: 1.4, type: "dashed" },
        itemStyle: { color: C.blue },
      },
      {
        name: "上证指数",
        type: "line",
        data: index.map((p) => p.close),
        xAxisIndex: 1,
        yAxisIndex: 1,
        showSymbol: false,
        lineStyle: { color: C.amber, width: 1.2 },
        itemStyle: { color: C.amber },
      },
    ],
  });

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}

export const ScreeningChart = memo(ScreeningChartImpl);
