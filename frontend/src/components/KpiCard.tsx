import { signClass } from "../lib/format";

interface Props {
  label: string;
  value: string;
  tone?: "up" | "down" | "neutral" | "clay";
  delta?: number | null;
  deltaText?: string;
}

const toneClass: Record<string, string> = {
  up: "text-up",
  down: "text-down",
  neutral: "text-ink",
  clay: "text-clay",
};

export function KpiCard({ label, value, tone = "neutral", delta, deltaText }: Props) {
  return (
    <div className="card card-pad transition-shadow hover:shadow-lift">
      <div className="text-[13px] font-medium text-muted">{label}</div>
      <div className={`mt-1.5 text-3xl font-semibold nums ${toneClass[tone]}`}>{value}</div>
      {(delta != null || deltaText) && (
        <div className={`mt-1 text-[13px] nums ${signClass(delta)}`}>{deltaText}</div>
      )}
    </div>
  );
}
