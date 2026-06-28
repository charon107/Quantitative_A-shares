import { useMemo } from "react";
import { rangeDays, type RangeKey } from "../components/RangeTabs";

/** 对日期序列按时间范围切片（前端过滤，避免重复 useMemo）。 */
export function useSliceByRange<T extends { date: string }>(
  data: T[] | undefined,
  range: RangeKey,
): T[] {
  return useMemo(() => {
    if (!data?.length) return [];
    const days = rangeDays(range);
    if (!days) return data;
    const cutoff = new Date(data[data.length - 1].date);
    cutoff.setDate(cutoff.getDate() - days);
    return data.filter((p) => new Date(p.date) >= cutoff);
  }, [data, range]);
}
