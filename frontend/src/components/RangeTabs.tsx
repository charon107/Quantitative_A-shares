const RANGES = [
  { key: "1M", label: "1月", days: 30 },
  { key: "3M", label: "3月", days: 90 },
  { key: "6M", label: "6月", days: 180 },
  { key: "1Y", label: "1年", days: 365 },
  { key: "ALL", label: "全部", days: 0 },
] as const;

export type RangeKey = (typeof RANGES)[number]["key"];
export const rangeDays = (k: RangeKey) => RANGES.find((r) => r.key === k)!.days;

export function RangeTabs({ value, onChange }: { value: RangeKey; onChange: (k: RangeKey) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-panel2 p-0.5">
      {RANGES.map((r) => (
        <button
          key={r.key}
          onClick={() => onChange(r.key)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
            value === r.key ? "bg-panel text-clay shadow-soft" : "text-muted hover:text-ink"
          }`}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}
