// 数值格式化与涨跌语义辅助

export const fmtPrice = (v: number | null | undefined) =>
  v == null ? "—" : v.toFixed(2);

export const fmtPct = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

export const fmtTurn = (v: number | null | undefined) =>
  v == null ? "—" : `${v.toFixed(2)}%`;

// 成交额：tushare amount 单位为「千元」-> 元 -> 亿/万
export const fmtAmount = (v: number | null | undefined) => {
  if (v == null) return "—";
  const yuan = v * 1000; // 千元 -> 元
  const yi = yuan / 1e8;
  if (yi >= 1) return `${yi.toFixed(2)} 亿`;
  return `${(yuan / 1e4).toFixed(0)} 万`;
};

// 成交量：tushare volume 单位为「手」-> 万手
export const fmtVolume = (v: number | null | undefined) =>
  v == null ? "—" : `${(v / 1e4).toFixed(2)} 万手`;

export const fmtInt = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString("zh-CN");

// 金额（元）：千分位 + 两位小数。模拟盘资金/市值用
export const fmtMoney = (v: number | null | undefined) =>
  v == null
    ? "—"
    : v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// 涨跌色：>0 绿（A股 dashboard 沿用绿涨红跌语义），<0 红，=0 中性
export const signClass = (v: number | null | undefined) =>
  v == null || v === 0 ? "text-muted" : v > 0 ? "text-up" : "text-down";

export const signArrow = (v: number | null | undefined) =>
  v == null || v === 0 ? "" : v > 0 ? "▲" : "▼";

// 金额（元）-> 亿。财报日历等财务摘要用
export const fmtYi = (v: number | null | undefined) =>
  v == null ? "—" : `${(v / 1e8).toFixed(2)} 亿`;

// 小数比率 -> 带符号百分数（0.15 -> "+15.0%"）。库内比率一律为小数
export const fmtPctDecimal = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;

// 小数比率区间（业绩预告变动幅度）：相同则单值，缺一则给单值
export const fmtPctRange = (lo: number | null | undefined, hi: number | null | undefined) => {
  if (lo == null && hi == null) return "—";
  if (lo != null && hi != null) return lo === hi ? fmtPctDecimal(lo) : `${fmtPctDecimal(lo)} ~ ${fmtPctDecimal(hi)}`;
  return fmtPctDecimal((lo ?? hi) as number);
};

// 报告期 end_date -> 中文标签（"2026-06-30" -> "2026 半年报"）
export const fmtPeriodLabel = (endDate: string) => {
  const y = endDate.slice(0, 4);
  const mmdd = endDate.slice(5);
  if (mmdd === "12-31") return `${y} 年报`;
  if (mmdd === "06-30") return `${y} 半年报`;
  if (mmdd === "03-31") return `${y} 一季报`;
  if (mmdd === "09-30") return `${y} 三季报`;
  return endDate;
};
