import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** 全屏浮层：K线图横屏查看 / 移动端友好 */
export function FullscreenOverlay({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col bg-cream">
      <div className="flex items-center justify-between border-b border-line bg-cream/90 px-4 py-3 backdrop-blur">
        <span className="text-sm font-medium text-clay">全屏 K线</span>
        <button
          onClick={onClose}
          className="rounded-lg border border-line bg-panel2 px-3 py-1 text-xs font-medium text-muted transition hover:text-ink"
        >
          ✕ 退出全屏
        </button>
      </div>
      <div className="flex-1 p-2">{children}</div>
    </div>,
    document.body,
  );
}
