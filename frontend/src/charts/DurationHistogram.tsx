import ReactECharts from "echarts-for-react";
import type { DurationSample } from "../api/types";
import { C, axisBase, baseOption, tooltipBase } from "../theme/echarts";

export function DurationHistogram({
  samples,
  onPick,
  height = 360,
}: {
  samples: DurationSample[];
  onPick?: (duration: number) => void;
  height?: number;
}) {
  const maxD = samples.reduce((m, s) => Math.max(m, s.duration), 0);
  const xs = Array.from({ length: maxD }, (_, i) => i + 1);
  const closed = xs.map(() => 0);
  const ongoing = xs.map(() => 0);
  for (const s of samples) {
    const idx = s.duration - 1;
    if (idx < 0) continue;
    if (s.ongoing) ongoing[idx] += 1;
    else closed[idx] += 1;
  }

  const option = baseOption({
    legend: { data: ["已结束", "未结束"], top: 0, textStyle: { color: C.muted, fontSize: 11 }, itemWidth: 12, itemHeight: 12 },
    grid: { left: 48, right: 18, top: 28, bottom: 36 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, ...tooltipBase },
    xAxis: { type: "category", data: xs.map(String), name: "持续交易日", nameLocation: "middle", nameGap: 26, nameTextStyle: { color: C.muted, fontSize: 11 }, ...axisBase },
    yAxis: { type: "value", name: "样本数", nameTextStyle: { color: C.muted, fontSize: 11 }, ...axisBase },
    series: [
      { name: "已结束", type: "bar", stack: "t", data: closed, itemStyle: { color: `${C.up}CC` } },
      { name: "未结束", type: "bar", stack: "t", data: ongoing, itemStyle: { color: `${C.amber}CC` } },
    ],
  });

  return (
    <ReactECharts
      option={option}
      style={{ height }}
      notMerge
      lazyUpdate
      onEvents={{
        click: (p: { name?: string }) => {
          if (onPick && p.name) onPick(Number(p.name));
        },
      }}
    />
  );
}
