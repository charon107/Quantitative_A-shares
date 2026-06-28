// Anthropic 浅色暖调的 ECharts 公用样式（与 tailwind.config.ts 同源）
import type { EChartsOption } from "echarts";

export const C = {
  cream: "#FAF9F5",
  panel: "#FFFFFF",
  ink: "#1A1A18",
  muted: "#6B6760",
  line: "#E5E1D8",
  grid: "#ECE8DF",
  clay: "#CC785C",
  clayDark: "#BD5D3A",
  up: "#C84B31", // 上涨红（A股「红涨绿跌」）
  down: "#3A8C5F", // 下跌绿
  amber: "#D9A441",
  blue: "#5B7C99",
};

export const MA_COLORS = ["#6B6760", "#D9A441", "#5B7C99", "#9A6FB0"]; // MA5/10/20/60

const FONT = "Inter, 'Noto Sans SC', sans-serif";

export const axisBase = {
  axisLine: { lineStyle: { color: C.line } },
  axisTick: { show: false },
  axisLabel: { color: C.muted, fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 },
  splitLine: { lineStyle: { color: C.grid, type: "dashed" as const } },
};

export const tooltipBase = {
  backgroundColor: C.panel,
  borderColor: C.line,
  borderWidth: 1,
  textStyle: { color: C.ink, fontFamily: FONT, fontSize: 12 },
  padding: [8, 12] as [number, number],
  extraCssText: "box-shadow: 0 4px 16px rgba(26,26,24,0.10); border-radius: 10px;",
};

// 入参放宽为 Record，避免与 ECharts 严格联合类型反复冲突；
// 数据构建仍在各图表里受类型检查，配置形状由 ECharts 运行时校验。
export function baseOption(extra: Record<string, unknown>): EChartsOption {
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: FONT, color: C.ink },
    animationDuration: 300,
    ...extra,
  } as EChartsOption;
}
