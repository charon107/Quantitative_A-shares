import { memo, useEffect, useRef } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption, EChartsType } from "echarts";
import type { QuarterlyFundamentalPoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

export type FundamentalMode = "cum" | "single";

interface QuarterlyFundamentalChartProps {
  points: QuarterlyFundamentalPoint[];
  mode: FundamentalMode;
  focus?: { dates: string[]; requestId: number } | null;
  height?: number | string;
}

// 默认缩放到最近 N 个季度（dataZoom 起点按数据量换算）
const DEFAULT_QUARTERS = 20;

// 比率系列名集合（tooltip 按名称区分 % 与 亿元 格式）
const RATIO_SERIES = new Set(["ROE", "净利率", "毛利率"]);

interface TooltipParam {
  dataIndex?: number;
  marker?: string;
  seriesName?: string;
  value?: unknown;
}

const fmtRatio = (v: unknown) => (typeof v === "number" ? `${v.toFixed(2)}%` : "—");
const fmtYi = (v: unknown) => (typeof v === "number" ? `${v.toFixed(2)} 亿` : "—");
const reportPeriod = (p: QuarterlyFundamentalPoint) =>
  `${p.year}Q${p.quarter}${p.source === "express" ? "·快报" : ""}`;
const disclosureDate = (p: QuarterlyFundamentalPoint) => p.ann_date ?? "披露日未知";

/**
 * 季度基本面三联图（共享 X 轴 + 联动 axisPointer + dataZoom）：
 * ① 比率折线：ROE / 净利率 / 毛利率（%，缺口断线不插值）
 * ② 利润柱状：营收 / 净利润 / 经营现金流（亿元）
 * ③ 资产负债折线：总资产 / 总负债 / 总债务（亿元，期末时点值，不随口径切换；
 *    总债务=短借+长借+应付债券，与年度选股 debt_ratio 分子同口径）
 * mode="single" 时 ①② 切换为单季口径（q_ 字段）。
 * X 轴使用真实披露日 ann_date；tooltip 同时显示所属季度。
 * source="express" 的点为前端叠加的最新业绩快报预览，tooltip 显式标注“快报”。
 */
function QuarterlyFundamentalChartImpl({
  points,
  mode,
  focus,
  height = 560,
}: QuarterlyFundamentalChartProps) {
  const chartRef = useRef<EChartsType | null>(null);
  const labels = points.map(disclosureDate);
  const single = mode === "single";
  let focusIndex = -1;
  if (focus) {
    for (let i = points.length - 1; i >= 0; i -= 1) {
      const announcementDate = points[i].ann_date;
      if (announcementDate && focus.dates.includes(announcementDate)) {
        focusIndex = i;
        break;
      }
    }
  }

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

  const visibleCount = Math.min(DEFAULT_QUARTERS, labels.length);
  const maxStartIndex = Math.max(0, labels.length - visibleCount);
  const startIndex =
    focusIndex >= 0
      ? Math.max(0, Math.min(focusIndex - Math.floor(visibleCount * 0.65), maxStartIndex))
      : maxStartIndex;
  const endIndex = Math.min(labels.length - 1, startIndex + visibleCount - 1);
  const zoomDenominator = Math.max(labels.length - 1, 1);
  const zoomStart = (startIndex / zoomDenominator) * 100;
  const zoomEnd = labels.length ? (endIndex / zoomDenominator) * 100 : 100;

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
        const point = points[arr[0]?.dataIndex ?? -1];
        const head = point ? `${disclosureDate(point)} · ${reportPeriod(point)}` : "";
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
      { type: "inside", xAxisIndex: [0, 1, 2], start: zoomStart, end: zoomEnd },
      {
        type: "slider",
        xAxisIndex: [0, 1, 2],
        start: zoomStart,
        end: zoomEnd,
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

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !focus || focusIndex < 0) return;

    const timers: number[] = [];
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const seriesBatch = Array.from({ length: 9 }, (_, seriesIndex) => ({
      seriesIndex,
      dataIndex: focusIndex,
    }));

    const highlight = () => {
      chart.dispatchAction({ type: "highlight", batch: seriesBatch });
      chart.dispatchAction({ type: "showTip", seriesIndex: 0, dataIndex: focusIndex });
    };
    const clearHighlight = () => {
      chart.dispatchAction({ type: "downplay", batch: seriesBatch });
      chart.dispatchAction({ type: "hideTip" });
    };

    chart.dispatchAction({ type: "dataZoom", dataZoomIndex: 0, start: zoomStart, end: zoomEnd });
    chart.dispatchAction({ type: "dataZoom", dataZoomIndex: 1, start: zoomStart, end: zoomEnd });

    if (reducedMotion) {
      timers.push(window.setTimeout(highlight, 80));
      timers.push(window.setTimeout(clearHighlight, 700));
    } else {
      // 等页面平滑滚动到季度图后再闪两次，效果与鼠标悬停同源。
      timers.push(window.setTimeout(highlight, 750));
      timers.push(window.setTimeout(clearHighlight, 1400));
      timers.push(window.setTimeout(highlight, 1850));
      timers.push(window.setTimeout(clearHighlight, 2500));
    }

    return () => {
      timers.forEach(window.clearTimeout);
      clearHighlight();
    };
  }, [focus?.requestId, focusIndex, zoomEnd, zoomStart]);

  return (
    <ReactECharts
      option={option}
      style={{ height }}
      notMerge
      lazyUpdate
      onChartReady={(chart: EChartsType) => {
        chartRef.current = chart;
      }}
    />
  );
}

export const QuarterlyFundamentalChart = memo(QuarterlyFundamentalChartImpl);
