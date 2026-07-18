import { memo } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import type { ChartLinePoint } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

const MA_N = 20;

// dates 为升序交易日序列。ECharts 类目轴要求 markLine 的 xAxis 精确等于某个类目
// (交易日),否则静默丢弃。财报公布日常落在周末/节假日/停牌,需先对齐到真实交易日。

/** 返回 >= d 的第一个交易日(财报公布后首个可交易日);无则 null。 */
function firstTradingDayOnOrAfter(d: string, dates: string[]): string | null {
  for (const t of dates) if (t >= d) return t;
  return null;
}

/** 返回 <= d 的最后一个交易日(用于右边界 Next 截断线);无则 null。 */
function lastTradingDayOnOrBefore(d: string, dates: string[]): string | null {
  for (let i = dates.length - 1; i >= 0; i--) if (dates[i] <= d) return dates[i];
  return null;
}

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

  // 每个财报公布日对齐到公布后首个交易日,再去重(多个公布日可能落到同一交易日,
  // 也顺带合并早期年份 ann_date 相邻重复的情况)。落在非交易日不再被 ECharts 丢弃。
  const pubMarkLines = Array.from(
    new Set(
      pubDates
        .filter((d) => d !== nextPub) // next_pub 由下方 Next 线单独标注,避免右边界重叠
        .map((d) => firstTradingDayOnOrAfter(d, dates))
        .filter((d): d is string => d != null),
    ),
  ).map((d) => ({ xAxis: d, lineStyle: { color: C.muted, type: "dashed" as const, width: 1 } }));

  // Next 截断线标在右边界:对齐到 <= next_pub 的最后一个交易日(股价已由后端截断到
  // next_pub,故通常即图最右端);label 仍显示真实的下一份财报公布日。
  const nextAnchor = nextPub ? lastTradingDayOnOrBefore(nextPub, dates) : null;
  const nextMarkLine = nextAnchor
    ? [
        {
          xAxis: nextAnchor,
          lineStyle: { color: C.clay, type: "dashdot" as const, width: 1.6 },
          label: { show: true, formatter: `Next\n${nextPub}`, color: C.clay, fontSize: 10, position: "insideEndTop" as const },
        },
      ]
    : [];

  const f2 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));

  // —— 超额收益：以首个财报公布日（无则首个交易日）为基准，算个股/指数相对涨跌 ——
  const stockCloseByDate = new Map(stock.map((p) => [p.date, p.close]));
  const stockMaByDate = new Map(stock.map((p) => [p.date, p.ma]));
  const indexCloseByDate = new Map(index.map((p) => [p.date, p.close]));

  const baseDate = pubDates[0] ?? dates[0] ?? null;
  // 基准日若停牌/缺值，则向后取第一个有效收盘价作为基准点
  const firstValidFrom = (arr: ChartLinePoint[], from: string | null) => {
    if (!from) return null;
    for (const p of arr) {
      if (p.date >= from && p.close != null) return { date: p.date, close: p.close };
    }
    return null;
  };
  const baseStock = firstValidFrom(stock, baseDate);
  const baseIndex = firstValidFrom(index, baseDate);

  const signPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  const retColor = (v: number) => (v >= 0 ? C.up : C.down);
  const dot = (color: string) =>
    `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px;vertical-align:middle;"></span>`;
  const row = (label: string, value: string, bold = false) =>
    `<div style="display:flex;justify-content:space-between;gap:20px;line-height:1.75;${bold ? "font-weight:700;" : ""}"><span>${label}</span><span style="font-weight:600;">${value}</span></div>`;

  const tooltipFormatter = (raw: unknown): string => {
    const list = (Array.isArray(raw) ? raw : [raw]) as Array<{ axisValue?: string }>;
    const date = list.find((p) => p.axisValue)?.axisValue;
    if (!date) return "";
    const curStock = stockCloseByDate.get(date) ?? null;
    const curMa = stockMaByDate.get(date) ?? null;
    const curIndex = indexCloseByDate.get(date) ?? null;

    let html = `<div style="font-size:11px;color:${C.muted};margin-bottom:4px;">${date}</div>`;
    html += row(`${dot(C.clay)}收盘价`, f2(curStock));
    html += row(`${dot(C.blue)}MA${maN}`, f2(curMa));
    html += row(`${dot(C.amber)}上证指数`, f2(curIndex));

    if (baseStock && baseIndex && curStock != null && curIndex != null) {
      const stockRet = (curStock / baseStock.close - 1) * 100;
      const indexRet = (curIndex / baseIndex.close - 1) * 100;
      const excess = stockRet - indexRet;
      html += `<div style="border-top:1px dashed ${C.line};margin:6px 0 4px;padding-top:5px;font-size:11px;color:${C.muted};">基准 ${baseStock.date} 起</div>`;
      html += row("个股收益", `<span style="color:${retColor(stockRet)}">${signPct(stockRet)}</span>`);
      html += row("指数收益", `<span style="color:${retColor(indexRet)}">${signPct(indexRet)}</span>`);
      html += row("超额收益", `<span style="color:${retColor(excess)}">${signPct(excess)}</span>`, true);
    }
    return html;
  };

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
      formatter: tooltipFormatter,
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
