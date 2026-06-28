// 加载 / 错误 / 空 占位

export function Loading({ label = "加载中…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-muted">
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-line border-t-clay" />
      <span className="text-sm">{label}</span>
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="rounded-xl border border-down/30 bg-down/5 px-4 py-3 text-sm text-down">
      加载失败：{msg}
    </div>
  );
}

export function Empty({ label = "暂无数据" }: { label?: string }) {
  return <div className="py-16 text-center text-sm text-muted">{label}</div>;
}
