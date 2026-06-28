import type { KlinePoint } from "../api/types";

export interface RangeStats {
  startDate: string;
  endDate: string;
  count: number; // 周期个数
  startPrice: number; // 起始价（首根开盘）
  endPrice: number; // 终止价（末根收盘）
  avgPrice: number; // 均价（VWAP = 成交额/成交量）
  high: number; // 区间最高
  low: number; // 区间最低
  amplitude: number; // 振幅 %  =(high-low)/起始价
  pctChg: number; // 涨跌幅 % =(终止-起始)/起始
  turnover: number; // 换手率 %（区间累计）
  volume: number; // 总量（手，原始累计）
  amount: number; // 金额（千元，原始累计）
  up: number; // 阳线
  down: number; // 阴线
  flat: number; // 平线
}

/** 从已加载的 K线点计算 [i0, i1] 闭区间的统计。volume 单位手、amount 单位千元。 */
export function computeRangeStats(points: KlinePoint[], i0: number, i1: number): RangeStats | null {
  if (!points.length) return null;
  const lo = Math.max(0, Math.min(i0, i1));
  const hi = Math.min(points.length - 1, Math.max(i0, i1));
  const seg = points.slice(lo, hi + 1).filter((p) => p.close != null);
  if (seg.length === 0) return null;

  const first = seg[0];
  const last = seg[seg.length - 1];
  const startPrice = first.open ?? first.close ?? 0;
  const endPrice = last.close ?? 0;

  let high = -Infinity;
  let low = Infinity;
  let volume = 0;
  let amount = 0;
  let turnover = 0;
  let up = 0;
  let down = 0;
  let flat = 0;
  let closeSum = 0;

  for (const p of seg) {
    if (p.high != null) high = Math.max(high, p.high);
    if (p.low != null) low = Math.min(low, p.low);
    volume += p.volume ?? 0;
    amount += p.amount ?? 0;
    turnover += p.turn ?? 0;
    closeSum += p.close ?? 0;
    const o = p.open ?? 0;
    const c = p.close ?? 0;
    if (c > o) up += 1;
    else if (c < o) down += 1;
    else flat += 1;
  }

  // VWAP：成交额(千元→元) / 成交量(手→股) = amount*1000 / (volume*100) = amount*10/volume
  const avgPrice = volume > 0 ? (amount * 10) / volume : closeSum / seg.length;
  const amplitude = startPrice ? ((high - low) / startPrice) * 100 : 0;
  const pctChg = startPrice ? ((endPrice - startPrice) / startPrice) * 100 : 0;

  return {
    startDate: first.date,
    endDate: last.date,
    count: seg.length,
    startPrice,
    endPrice,
    avgPrice,
    high,
    low,
    amplitude,
    pctChg,
    turnover,
    volume,
    amount,
    up,
    down,
    flat,
  };
}
