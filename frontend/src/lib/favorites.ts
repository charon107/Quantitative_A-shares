// 股票收藏：localStorage 持久化（键 favorites:v1），useSyncExternalStore 保证跨组件同步。
// 数据按收藏时间倒序（最新在前）；localStorage 损坏/不可用时降级为空列表或会话内存态。
import { useSyncExternalStore } from "react";

export interface Favorite {
  code: string;
  name: string | null;
  addedAt: number;
}

const STORAGE_KEY = "favorites:v1";

let cache: Favorite[] | null = null;
const listeners = new Set<() => void>();

function isFavoriteArray(v: unknown): v is Favorite[] {
  return (
    Array.isArray(v) &&
    v.every(
      (x) =>
        x != null &&
        typeof x === "object" &&
        typeof (x as Favorite).code === "string" &&
        typeof (x as Favorite).addedAt === "number",
    )
  );
}

function read(): Favorite[] {
  if (cache) return cache;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    cache = isFavoriteArray(parsed) ? parsed : [];
  } catch {
    cache = [];
  }
  return cache;
}

function write(next: Favorite[]): void {
  cache = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // 隐私模式等场景写不进去：保留内存态，功能不报错
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // 跨标签页同步：其他标签页写入时丢弃本地缓存，下次读取重新解析
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) {
      cache = null;
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function useFavorites() {
  const favorites = useSyncExternalStore(subscribe, read);

  const isFavorite = (code: string): boolean => favorites.some((f) => f.code === code);

  const addFavorite = (code: string, name: string | null): void => {
    if (isFavorite(code)) return;
    write([{ code, name, addedAt: Date.now() }, ...favorites]);
  };

  const removeFavorite = (code: string): void => {
    write(favorites.filter((f) => f.code !== code));
  };

  const toggleFavorite = (code: string, name: string | null): void => {
    if (isFavorite(code)) removeFavorite(code);
    else addFavorite(code, name);
  };

  return { favorites, isFavorite, addFavorite, removeFavorite, toggleFavorite };
}
