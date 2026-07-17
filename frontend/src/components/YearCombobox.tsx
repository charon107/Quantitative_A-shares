import { useEffect, useRef, useState, type KeyboardEvent } from "react";

interface YearComboboxProps {
  years: number[]; // 可选年份（升序）
  value: number | null; // 当前选中
  onChange: (year: number) => void;
}

const YEAR_MIN = 1990;
const YEAR_MAX = 2100;

/** 自定义年份下拉：既可从面板选择，也可手动输入任意合法年份（1990–2100）。 */
export function YearCombobox({ years, value, onChange }: YearComboboxProps) {
  const [input, setInput] = useState<string>(value != null ? String(value) : "");
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // 选中值变化（默认选中最新年 / 用户提交）时同步输入显示
  useEffect(() => {
    if (value != null) setInput(String(value));
  }, [value]);

  // 点击外部：关闭并把未提交的无效输入恢复为当前值
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
        setInput(value != null ? String(value) : "");
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [value]);

  const desc = [...years].sort((a, b) => b - a); // 倒序，最新在前
  // 输入为空或恰为当前选中值时展示全部，方便换年；否则按前缀过滤
  const showAll = input === "" || input === String(value);
  const filtered = showAll ? desc : desc.filter((y) => String(y).startsWith(input));

  const commit = (y: number) => {
    onChange(y);
    setInput(String(y));
    setOpen(false);
  };

  const submitTyped = (raw: string) => {
    const y = parseInt(raw, 10);
    if (!Number.isNaN(y) && y >= YEAR_MIN && y <= YEAR_MAX) {
      commit(y);
    } else {
      setInput(value != null ? String(value) : "");
      setOpen(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActiveIdx((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && filtered[activeIdx] != null) commit(filtered[activeIdx]);
      else submitTyped(input);
    } else if (e.key === "Escape") {
      setOpen(false);
      setInput(value != null ? String(value) : "");
    }
  };

  return (
    <div ref={boxRef} className="relative">
      <input
        ref={inputRef}
        type="text"
        inputMode="numeric"
        role="combobox"
        aria-expanded={open}
        aria-controls="year-listbox"
        value={input}
        onChange={(e) => {
          setInput(e.target.value.replace(/[^\d]/g, "").slice(0, 4));
          setOpen(true);
          setActiveIdx(0);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        placeholder="选择或输入"
        className="w-36 rounded-xl border border-line bg-panel py-2 pl-3 pr-9 text-sm text-ink placeholder:text-muted shadow-soft outline-none transition focus:border-clay focus:ring-2 focus:ring-clay/20"
      />
      <button
        type="button"
        tabIndex={-1}
        onClick={() => {
          setOpen((o) => !o);
          inputRef.current?.focus();
        }}
        aria-label="展开年份列表"
        className="absolute inset-y-0 right-0 flex items-center pr-3 text-muted transition hover:text-ink"
      >
        <svg
          className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`}
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
        >
          <path d="M6 8l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && filtered.length > 0 && (
        <ul
          id="year-listbox"
          role="listbox"
          className="absolute z-20 mt-2 max-h-64 w-full overflow-auto rounded-xl border border-line bg-panel py-1 shadow-lift"
        >
          {filtered.map((y, idx) => {
            const selected = y === value;
            const active = idx === activeIdx;
            return (
              <li key={y} role="option" aria-selected={selected}>
                <button
                  type="button"
                  onMouseEnter={() => setActiveIdx(idx)}
                  onClick={() => commit(y)}
                  className={`block w-full px-3 py-1.5 text-left text-sm transition ${
                    active ? "bg-panel2" : ""
                  } ${selected ? "font-medium text-clay" : "text-ink"}`}
                >
                  {y}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
