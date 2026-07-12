import { memo } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import type { QuarterlyFundamentalPoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

export type FundamentalMode = "cum" | "single";

interface QuarterlyFundamentalChartProps {
  points: QuarterlyFundamentalPoint[];
  mode: FundamentalMode;
  height?: number | string;
}

// 默认缩放到最近 N 个季度（dataZoom 起点按数据量换算）
const DEFAULT_QUARTERS = 20;

// 比率系列名集合（tooltip 按名称区分 % 与 亿元 格式）
const RATIO_SERIES = new Set(["ROE", "净利率", "毛利率"]);

interface TooltipParam {
  marker?: string;
  seriesName?: string;
  value?: unknown;
  axisValueLabel?: string;
}

const fmtRatio = (v: unknown) => (typeof v === "number" ? `${v.toFixed(2)}%` : "—");
const fmtYi = (v: unknown) => (typeof v === "number" ? `${v.toFixed(2)} 亿` : "—");

/**
 * 季度基本面三联图（共享 X 轴 + 联动 axisPointer + dataZoom）：
 * ① 比率折线：ROE / 净利率 / 毛利率（%，缺口断线不插值）
 * ② 利润柱状：营收 / 净利润 / 经营现金流（亿元）
 * ③ 资产负债折线：总资产 / 总负债 / 总债务（亿元，期末时点值，不随口径切换；
 *    总债务=短借+长借+应付债券，与年度选股 debt_ratio 分子同口径）
 * mode="single" 时 ①② 切换为单季口径（q_ 字段）。
 */
function QuarterlyFundamentalChartImpl({ points, mode, height = 560 }: QuarterlyFundamentalChartProps) {
  const labels = points.map((p) => `${p.year}Q${p.quarter}`);
  const single = mode === "single";

  const pct = (v: number | null) => (v == null ? null : v * 100);
  const yi = (v: number | null) => (v == null ? null : v / 1e8);

  const roe = points.map((p) => pct(single ? p.q_roe : p.roe));
  const margin = points.map((p) => pct(single ? p.q_netprofit_margin : p.netprofit_margin));
  const gross = points.map((p) => pct(single ? p.q_gsprofit_margin : p.grossprofit_margin));
  const revenue = points.map((p) => yi(single ? p.q_revenue : p.revenue));
  const netProfit = points.map((p) => yi(single ? p.q_net_profit : p.net_profit));
  const cfo = points.map((p) => yi(single ? p.q_cfo : p.cfo));
  const totalAssets = points.map((p) => yi(p.total_assets));
  const totalLiab = points.map((p) => yi(p.total_liab));
  const totalDebt = points.map((p) => yi(p.total_debt));

  const zoomStart = Math.max(0, ((labels.length - DEFAULT_QUARTERS) / Math.max(labels.length, 1)) * 100);

  const xAxis = (gridIndex: number, showLabel: boolean) => ({
    type: "category" as const,
    data: labels,
    gridIndex,
    ...axisBase,
    boundaryGap: true,
    axisLabel: { ...axisBase.axisLabel, show: showLabel },
  });

  const line = (
    name: string,
    data: (number | null)[],
    color: string,
    axisIndex: number,
    dashed = false,
  ) => ({
    name,
    type: "line" as const,
    data,
    xAxisIndex: axisIndex,
    yAxisIndex: axisIndex,
    connectNulls: false, // 数据缺口断线呈现（半年报时代/代理缺失段不造数）
    showSymbol: false,
    lineStyle: { color, width: 1.4, type: dashed ? ("dashed" as const) : ("solid" as const) },
    itemStyle: { color },
  });

  const bar = (name: string, data: (number | null)[], color: string, axisIndex: number) => ({
    name,
    type: "bar" as const,
    data,
    xAxisIndex: axisIndex,
    yAxisIndex: axisIndex,
    itemStyle: { color },
    barMaxWidth: 14,
  });

  const option: EChartsOption = baseOption({
    legend: {
      data: ["ROE", "净利率", "毛利率", "营收", "净利润", "经营现金流", "总资产", "总负债", "总债务"],
      top: 4,
      textStyle: { color: C.muted, fontSize: 11, fontFamily: "'IBM Plex Mono', monospace" },
      itemWidth: 16,
      itemHeight: 3,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      ...tooltipBase,
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as TooltipParam[];
        const head = arr[0]?.axisValueLabel ?? "";
        const lines = arr.map((p) => {
          const fmt = RATIO_SERIES.has(p.seriesName ?? "") ? fmtRatio : fmtYi;
          return `${p.marker ?? ""}${p.seriesName}: ${fmt(p.value)}`;
        });
        return [head, ...lines].join("<br/>");
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    // y 轴在右侧：右边距要装下刻度数字 + 轴名（左侧无轴，留小边即可）
    grid: [
      { left: 18, right: 64, top: 34, height: "22%" },
      { left: 18, right: 64, top: "38%", height: "22%" },
      { left: 18, right: 64, top: "68%", height: "20%" },
    ],
    xAxis: [xAxis(0, false), xAxis(1, false), xAxis(2, true)],
    yAxis: [
      { scale: true, gridIndex: 0, position: "right", name: "%", ...axisBase },
      { scale: true, gridIndex: 1, position: "right", name: "亿元", ...axisBase },
      { scale: true, gridIndex: 2, position: "right", name: "亿元", ...axisBase },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1, 2], start: zoomStart, end: 100 },
      {
        type: "slider",
        xAxisIndex: [0, 1, 2],
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
      line("ROE", roe, C.clay, 0),
      line("净利率", margin, C.blue, 0),
      line("毛利率", gross, C.amber, 0, true),
      bar("营收", revenue, C.blue, 1),
      bar("净利润", netProfit, C.clay, 1),
      bar("经营现金流", cfo, C.amber, 1),
      line("总资产", totalAssets, C.amber, 2),
      line("总负债", totalLiab, C.muted, 2),
      line("总债务", totalDebt, C.clayDark, 2),
    ],
  });

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}

export const QuarterlyFundamentalChart = memo(QuarterlyFundamentalChartImpl);
