// 数值格式化与涨跌语义辅助

export const fmtPrice = (v: number | null | undefined) =>
  v == null ? "—" : v.toFixed(2);

export const fmtPct = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

export const fmtTurn = (v: number | null | undefined) =>
  v == null ? "—" : `${v.toFixed(2)}%`;

// 成交额：元 -> 亿
export const fmtAmount = (v: number | null | undefined) => {
  if (v == null) return "—";
  const yi = v / 1e8;
  if (yi >= 1) return `${yi.toFixed(2)} 亿`;
  return `${(v / 1e4).toFixed(0)} 万`;
};

export const fmtInt = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString("zh-CN");

// 涨跌色：>0 绿（A股 dashboard 沿用绿涨红跌语义），<0 红，=0 中性
export const signClass = (v: number | null | undefined) =>
  v == null || v === 0 ? "text-muted" : v > 0 ? "text-up" : "text-down";

export const signArrow = (v: number | null | undefined) =>
  v == null || v === 0 ? "" : v > 0 ? "▲" : "▼";
