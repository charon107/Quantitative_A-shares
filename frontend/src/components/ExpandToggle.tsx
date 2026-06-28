// 列表「先部分展示，点击展开全部」的统一控件

export function ExpandToggle({
  expanded,
  hiddenCount,
  onToggle,
}: {
  expanded: boolean;
  hiddenCount: number;
  onToggle: () => void;
}) {
  if (!expanded && hiddenCount <= 0) return null;
  return (
    <div className="flex justify-center pt-3">
      <button
        onClick={onToggle}
        className="rounded-lg border border-line bg-panel2 px-4 py-1.5 text-xs font-medium text-clay transition hover:border-clay/40 hover:bg-clay/5"
      >
        {expanded ? "收起" : `展开剩余 ${hiddenCount} 项`}
      </button>
    </div>
  );
}
