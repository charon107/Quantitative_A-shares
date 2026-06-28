import { useEffect, useRef, useState } from "react";
import { useSearch } from "../api/client";

export function SearchBox({ onPick }: { onPick: (code: string) => void }) {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  // 防抖：停止输入 250ms 后再查询
  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 250);
    return () => clearTimeout(t);
  }, [text]);

  // 点击外部关闭下拉
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const { data, isFetching } = useSearch(q);

  const pick = (code: string) => {
    onPick(code);
    setOpen(false);
    setText("");
  };

  return (
    <div ref={boxRef} className="relative w-full max-w-md">
      <input
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder="搜索股票代码或名称…"
        className="w-full rounded-xl border border-line bg-panel px-4 py-2.5 text-sm text-ink placeholder:text-muted shadow-soft outline-none transition focus:border-clay focus:ring-2 focus:ring-clay/20"
      />
      {open && q && (
        <div className="absolute z-20 mt-2 max-h-80 w-full overflow-auto rounded-xl border border-line bg-panel shadow-lift">
          {isFetching && <div className="px-4 py-3 text-sm text-muted">搜索中…</div>}
          {!isFetching && (!data || data.length === 0) && (
            <div className="px-4 py-3 text-sm text-muted">无匹配结果</div>
          )}
          {data?.map((row) => (
            <button
              key={row.code}
              onClick={() => pick(row.code)}
              className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm hover:bg-panel2"
            >
              <span className="font-medium text-ink">{row.code_name ?? row.code}</span>
              <span className="nums text-xs text-muted">{row.code}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
