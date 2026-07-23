// 星标收藏按钮：☆ 未收藏 / ★ 已收藏（主题色填充）
import { useFavorites } from "../lib/favorites";

export function FavoriteStar({ code, name }: { code: string; name: string | null }) {
  const { isFavorite, toggleFavorite } = useFavorites();
  const active = isFavorite(code);
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        toggleFavorite(code, name);
      }}
      title={active ? "取消收藏" : "收藏"}
      aria-label={active ? `取消收藏 ${name ?? code}` : `收藏 ${name ?? code}`}
      className={`shrink-0 rounded-lg px-1.5 py-0.5 text-lg leading-none transition hover:bg-panel2 ${
        active ? "text-clay" : "text-muted hover:text-clay"
      }`}
    >
      {active ? "★" : "☆"}
    </button>
  );
}
