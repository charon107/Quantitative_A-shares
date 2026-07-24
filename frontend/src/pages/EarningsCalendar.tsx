import { useEffect, useMemo, useRef, useState } from "react";
import { useEarningsCalendarDates, useEarningsCalendarDay } from "../api/client";
import type { EarningsCalendarRow } from "../api/types";
import { Card, CardHeader } from "../components/Card";
import { FavoriteStar } from "../components/FavoriteStar";
import { Empty, ErrorState, Loading } from "../components/States";
import { fmtPctDecimal, fmtPctRange, fmtPeriodLabel, fmtYi } from "../lib/format";
import { useFavorites } from "../lib/favorites";

type TypeFilter = "all" | "report" | "express" | "forecast";

const FILTER_OPTIONS: { key: TypeFilter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "report", label: "正式财报" },
  { key: "express", label: "业绩快报" },
  { key: "forecast", label: "业绩预告" },
];

const INITIAL_ROWS = 100;

// 预告类型涨跌语义（与 EarningsPanel 一致：绿涨红跌）
const POSITIVE_TYPES = new Set(["预增", "扭亏", "续盈", "略增"]);
const NEGATIVE_TYPES = new Set(["预减", "首亏", "续亏", "略减"]);

const forecastTypeClass = (t: string | null) =>
  t == null ? "text-muted" : POSITIVE_TYPES.has(t) ? "text-up" : NEGATIVE_TYPES.has(t) ? "text-down" : "text-ink";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

const shiftMonth = (month: string, delta: number) => {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};

const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/** 月历格子：周一开头，null 为月初前置空格 */
const buildMonthCells = (month: string): (number | null)[] => {
  const [y, m] = month.split("-").map(Number);
  const leadBlanks = (new Date(y, m - 1, 1).getDay() + 6) % 7;
  const daysInMonth = new Date(y, m, 0).getDate();
  const cells: (number | null)[] = Array(leadBlanks).fill(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  return cells;
};

function Tag({ label, className }: { label: string; className: string }) {
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${className}`}>{label}</span>;
}

function CalendarRow({ row, onPick }: { row: EarningsCalendarRow; onPick: (code: string) => void }) {
  const name = row.code_name ?? row.code;
  return (
    <button
      onClick={() => onPick(row.code)}
      className="grid w-full grid-cols-[26px_minmax(0,1fr)] items-start gap-3 rounded-lg px-3 py-2.5 text-left transition hover:bg-panel2"
    >
      <FavoriteStar code={row.code} name={row.code_name} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium text-ink">{name}</span>
          <span className="nums shrink-0 text-[11px] text-muted">{row.code}</span>
          <span className="flex gap-1">
            {row.report && <Tag label="正式财报" className="bg-clay/10 text-clay" />}
            {row.express && <Tag label="快报" className="bg-ink/5 text-ink" />}
            {row.forecast && <Tag label="预告" className="bg-panel2 text-muted" />}
          </span>
        </div>
        <div className="mt-1 space-y-0.5 text-xs text-muted">
          {row.report && (
            <div>
              {fmtPeriodLabel(row.report.end_date)} · 单季营收{" "}
              <span className="nums">{fmtYi(row.report.q_revenue)}</span> · 单季净利{" "}
              <span className="nums">{fmtYi(row.report.q_net_profit)}</span> · 净利同比{" "}
              <span className="nums">{fmtPctDecimal(row.report.q_netprofit_yoy)}</span>
            </div>
          )}
          {row.express && (
            <div>
              {fmtPeriodLabel(row.express.end_date)} 快报 · 营收{" "}
              <span className="nums">{fmtYi(row.express.revenue)}</span> · 净利{" "}
              <span className="nums">{fmtYi(row.express.n_income)}</span> · 扣非同比{" "}
              <span className="nums">{fmtPctDecimal(row.express.yoy_dedu_np)}</span>
            </div>
          )}
          {row.forecast && (
            <div>
              {fmtPeriodLabel(row.forecast.end_date)} 预告 ·{" "}
              <span className={`font-medium ${forecastTypeClass(row.forecast.type)}`}>
                {row.forecast.type ?? "—"}
              </span>{" "}
              · 净利变动 <span className="nums">{fmtPctRange(row.forecast.p_change_min, row.forecast.p_change_max)}</span>
            </div>
          )}
        </div>
      </div>
    </button>
  );
}

/** 财报日历：按公告日查看全市场财报披露（正式财报 / 业绩快报 / 业绩预告）。 */
export function EarningsCalendar({ onOpenStock }: { onOpenStock: (code: string) => void }) {
  const [month, setMonth] = useState<string | null>(null); // YYYY-MM，null=未初始化
  const [selected, setSelected] = useState<string | null>(null); // YYYY-MM-DD
  const [filter, setFilter] = useState<TypeFilter>("all");
  const [onlyFav, setOnlyFav] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const { isFavorite } = useFavorites();

  // 迷你日历弹层：点击外部或 Escape 关闭
  useEffect(() => {
    if (!pickerOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setPickerOpen(false);
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") setPickerOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [pickerOpen]);

  // month 为 null 时后端返回最近 120 天 + 最近披露日，用于初始定位
  const dates = useEarningsCalendarDates(month);
  // 全局最近披露日缓存：month 查询（如无数据的未来月份）会返回 latest_date=null，
  // 入口按钮不能依赖当前查询结果，否则翻到无数据月份后按钮消失
  const [latestDate, setLatestDate] = useState<string | null>(null);
  useEffect(() => {
    const latest = dates.data?.latest_date;
    if (!latest) return;
    if (!latestDate || latest > latestDate) setLatestDate(latest);
    if (!month) {
      setMonth(latest.slice(0, 7));
      setSelected(latest);
    }
  }, [dates.data, month, latestDate]);

  const day = useEarningsCalendarDay(selected);

  // 日历角标：date -> 当日披露总条数（三类相加，同一公司多类型会重复计，仅作密度指示）
  const countByDate = useMemo(
    () => new Map((dates.data?.days ?? []).map((d) => [d.date, d.report_count + d.express_count + d.forecast_count])),
    [dates.data],
  );

  const rows = useMemo(() => {
    let r = day.data?.rows ?? [];
    if (filter !== "all") r = r.filter((row) => row[filter] != null);
    if (onlyFav) r = r.filter((row) => isFavorite(row.code));
    return r;
  }, [day.data, filter, onlyFav, isFavorite]);

  const shown = showAll ? rows : rows.slice(0, INITIAL_ROWS);
  const today = todayStr();

  // 迷你日历的年份下拉：当前年往前 15 年（倒序，新年份在前）
  const pickerYears = useMemo(() => {
    const end = new Date().getFullYear();
    return Array.from({ length: 16 }, (_, i) => end - i);
  }, []);

  const pickDate = (date: string) => {
    setSelected(date);
    setShowAll(false);
    if (date.slice(0, 7) !== month) setMonth(date.slice(0, 7));
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="财报日历"
          subtitle="按公告日查看全市场财报披露 · 财报季内每日更新"
          right={
            latestDate && (
              <div ref={pickerRef} className="relative shrink-0">
                <button
                  onClick={() => setPickerOpen((o) => !o)}
                  className="flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-xs text-muted transition hover:border-clay hover:text-clay"
                >
                  {selected ?? latestDate}
                  <svg
                    className={`h-3 w-3 transition-transform ${pickerOpen ? "rotate-180" : ""}`}
                    viewBox="0 0 20 20"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                  >
                    <path d="M6 8l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                {pickerOpen && month && (
                  <div className="absolute right-0 z-20 mt-2 w-64 rounded-xl border border-line bg-panel p-3 shadow-lift">
                    <div className="mb-2 flex items-center justify-between gap-1">
                      <button
                        onClick={() => setMonth(shiftMonth(month, -1))}
                        className="rounded-md px-1.5 py-0.5 text-sm text-muted transition hover:bg-panel2 hover:text-ink"
                        aria-label="上个月"
                      >
                        ‹
                      </button>
                      <div className="flex gap-1">
                        <select
                          value={Number(month.slice(0, 4))}
                          onChange={(e) => setMonth(`${e.target.value}-${month.slice(5, 7)}`)}
                          className="nums rounded-md border border-line bg-panel px-1 py-0.5 text-xs text-ink outline-none transition focus:border-clay"
                          aria-label="选择年份"
                        >
                          {pickerYears.map((y) => (
                            <option key={y} value={y}>
                              {y}年
                            </option>
                          ))}
                        </select>
                        <select
                          value={Number(month.slice(5, 7))}
                          onChange={(e) =>
                            setMonth(`${month.slice(0, 4)}-${String(Number(e.target.value)).padStart(2, "0")}`)
                          }
                          className="nums rounded-md border border-line bg-panel px-1 py-0.5 text-xs text-ink outline-none transition focus:border-clay"
                          aria-label="选择月份"
                        >
                          {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                            <option key={m} value={m}>
                              {m}月
                            </option>
                          ))}
                        </select>
                      </div>
                      <button
                        onClick={() => setMonth(shiftMonth(month, 1))}
                        className="rounded-md px-1.5 py-0.5 text-sm text-muted transition hover:bg-panel2 hover:text-ink"
                        aria-label="下个月"
                      >
                        ›
                      </button>
                    </div>
                    <div className="grid grid-cols-7 gap-0.5 text-center">
                      {WEEKDAYS.map((w) => (
                        <div key={w} className="py-0.5 text-[9px] text-muted">
                          {w}
                        </div>
                      ))}
                      {buildMonthCells(month).map((d, i) => {
                        if (d == null) return <div key={`b${i}`} />;
                        const date = `${month}-${String(d).padStart(2, "0")}`;
                        const count = countByDate.get(date) ?? 0;
                        const isFuture = date > today;
                        const isSelected = date === selected;
                        return (
                          <button
                            key={date}
                            disabled={isFuture}
                            onClick={() => {
                              pickDate(date);
                              setPickerOpen(false);
                            }}
                            className={`flex flex-col items-center rounded-md py-0.5 transition ${
                              isSelected
                                ? "bg-clay/10 ring-1 ring-clay"
                                : isFuture
                                  ? "text-muted/40"
                                  : "hover:bg-panel2"
                            }`}
                          >
                            <span className={`nums text-[11px] ${isSelected ? "font-semibold text-clay" : "text-ink"}`}>
                              {d}
                            </span>
                            <span
                              className={`nums text-[8px] leading-tight ${count > 0 ? "text-clay" : "text-transparent"}`}
                            >
                              {count > 0 ? count : "·"}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    <button
                      onClick={() => {
                        pickDate(latestDate);
                        setPickerOpen(false);
                      }}
                      className="mt-2 w-full rounded-lg py-1 text-[11px] text-muted transition hover:bg-panel2 hover:text-clay"
                    >
                      回到最近披露日 {latestDate}
                    </button>
                  </div>
                )}
              </div>
            )
          }
        />
        {dates.isLoading || !month ? (
          <Loading />
        ) : dates.error ? (
          <div className="p-4">
            <ErrorState error={dates.error} />
          </div>
        ) : (
          <div className="px-5 pb-5">
            <div className="mb-2 flex items-center justify-between">
              <button
                onClick={() => setMonth(shiftMonth(month, -1))}
                className="rounded-lg px-2 py-1 text-sm text-muted transition hover:bg-panel2 hover:text-ink"
                aria-label="上个月"
              >
                ‹
              </button>
              <span className="nums text-sm font-medium text-ink">{month}</span>
              <button
                onClick={() => setMonth(shiftMonth(month, 1))}
                className="rounded-lg px-2 py-1 text-sm text-muted transition hover:bg-panel2 hover:text-ink"
                aria-label="下个月"
              >
                ›
              </button>
            </div>
            <div className="grid grid-cols-7 gap-1 text-center">
              {WEEKDAYS.map((w) => (
                <div key={w} className="py-1 text-[10px] text-muted">
                  {w}
                </div>
              ))}
              {buildMonthCells(month).map((d, i) => {
                if (d == null) return <div key={`b${i}`} />;
                const date = `${month}-${String(d).padStart(2, "0")}`;
                const count = countByDate.get(date) ?? 0;
                const isFuture = date > today;
                const isSelected = date === selected;
                return (
                  <button
                    key={date}
                    disabled={isFuture}
                    onClick={() => pickDate(date)}
                    className={`flex flex-col items-center rounded-lg py-1 transition ${
                      isSelected
                        ? "bg-clay/10 ring-1 ring-clay"
                        : isFuture
                          ? "text-muted/40"
                          : "hover:bg-panel2"
                    }`}
                  >
                    <span className={`nums text-xs ${isSelected ? "font-semibold text-clay" : "text-ink"}`}>{d}</span>
                    <span className={`nums text-[9px] leading-tight ${count > 0 ? "text-clay" : "text-transparent"}`}>
                      {count > 0 ? count : "·"}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title={selected ? `${selected} 披露` : "当日披露"}
          subtitle={day.data ? `共 ${rows.length} 家${filter !== "all" || onlyFav ? "（已过滤）" : ""}` : undefined}
          right={
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
              <div className="flex rounded-lg border border-line p-0.5">
                {FILTER_OPTIONS.map((o) => (
                  <button
                    key={o.key}
                    onClick={() => {
                      setFilter(o.key);
                      setShowAll(false);
                    }}
                    className={`rounded-md px-2 py-1 text-xs transition ${
                      filter === o.key ? "bg-clay/10 font-medium text-clay" : "text-muted hover:text-ink"
                    }`}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
              <label className="flex cursor-pointer items-center gap-1 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={onlyFav}
                  onChange={(e) => {
                    setOnlyFav(e.target.checked);
                    setShowAll(false);
                  }}
                  className="accent-clay"
                />
                只看收藏
              </label>
            </div>
          }
        />
        <div className="px-2 pb-3">
          {!selected || day.isLoading ? (
            <Loading />
          ) : day.error ? (
            <div className="p-4">
              <ErrorState error={day.error} />
            </div>
          ) : rows.length === 0 ? (
            <Empty
              label={
                onlyFav || filter !== "all"
                  ? "当前过滤条件下无披露记录，试试放宽过滤。"
                  : "当日无财报披露，可从日历选择有数字标注的日期。"
              }
            />
          ) : (
            <>
              {shown.map((row) => (
                <CalendarRow key={row.code} row={row} onPick={onOpenStock} />
              ))}
              {rows.length > INITIAL_ROWS && !showAll && (
                <button
                  onClick={() => setShowAll(true)}
                  className="mt-2 w-full rounded-lg py-2 text-xs text-muted transition hover:bg-panel2 hover:text-ink"
                >
                  显示全部 {rows.length} 家
                </button>
              )}
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
