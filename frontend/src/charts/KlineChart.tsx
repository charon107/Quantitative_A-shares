import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import type { KlinePoint } from "../api/types";
import { C, MA_COLORS, axisBase, baseOption, tooltipBase } from "../theme/echarts";

interface Focus {
  start: string;
  end: string;
}

export function KlineChart({
  points,
  focus,
  height = 460,
}: {
  points: KlinePoint[];
  focus?: Focus | null;
  height?: number;
}) {
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
    lineStyle: { color: MA_COLORS[i], width: 2 },
    itemStyle: { color: MA_COLORS[i] },
  }));

  // 默认看近端；传入 focus 时缩放到对应区间并高亮
  let zoomStart = 60;
  let zoomEnd = 100;
  let markArea: Record<string, unknown> | undefined;
  if (focus) {
    const i0 = dates.findIndex((d) => d >= focus.start);
    let i1 = -1;
    for (let k = dates.length - 1; k >= 0; k--) {
      if (dates[k] <= focus.end) {
        i1 = k;
        break;
      }
    }
    if (i0 >= 0 && i1 >= i0) {
      const pad = Math.max(8, Math.round((i1 - i0) * 0.6));
      const lo = Math.max(0, i0 - pad);
      const hi = Math.min(dates.length - 1, i1 + pad);
      const denom = Math.max(1, dates.length - 1);
      zoomStart = (lo / denom) * 100;
      zoomEnd = (hi / denom) * 100;
      markArea = {
        silent: true,
        itemStyle: { color: "rgba(204,120,92,0.12)" },
        data: [[{ xAxis: dates[i0] }, { xAxis: dates[i1] }]],
      };
    }
  }

  const sign = (v: number | null | undefined) =>
    v == null || v === 0 ? C.muted : v > 0 ? C.up : C.down;
  const f2 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));
  const row = (label: string, val: string, color?: string) =>
    `<div style="display:flex;justify-content:space-between;gap:22px"><span style="color:${C.muted}">${label}</span><b style="color:${color ?? C.ink}">${val}</b></div>`;

  const tooltipFormatter = (params: { dataIndex: number }[]) => {
    const i = params?.[0]?.dataIndex ?? 0;
    const p = points[i];
    if (!p) return "";
    const pctStr = p.pctChg == null ? "—" : `${p.pctChg >= 0 ? "+" : ""}${p.pctChg.toFixed(2)}%`;
    return [
      `<div style="font-weight:600;margin-bottom:4px">${p.date}</div>`,
      row("涨跌幅", pctStr, sign(p.pctChg)),
      row("开", f2(p.open)),
      row("收", f2(p.close), sign(p.pctChg)),
      row("高", f2(p.high)),
      row("低", f2(p.low)),
      `<div style="border-top:1px solid ${C.line};margin:5px 0"></div>`,
      row("MA5", f2(p.MA5), MA_COLORS[0]),
      row("MA10", f2(p.MA10), MA_COLORS[1]),
      row("MA20", f2(p.MA20), MA_COLORS[2]),
      row("MA60", f2(p.MA60), MA_COLORS[3]),
      `<div style="border-top:1px solid ${C.line};margin:5px 0"></div>`,
      row("成交量", p.volume == null ? "—" : `${(p.volume / 1e4).toFixed(0)} 万股`),
    ].join("");
  };

  const option: EChartsOption = baseOption({
    legend: {
      data: ["MA5", "MA10", "MA20", "MA60"],
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
      { type: "inside", xAxisIndex: [0, 1], start: zoomStart, end: zoomEnd },
      {
        type: "slider",
        xAxisIndex: [0, 1],
        start: zoomStart,
        end: zoomEnd,
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
        ...(markArea ? { markArea } : {}),
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
