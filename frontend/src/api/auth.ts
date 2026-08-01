// 前端鉴权状态（规范书 §7.11）
//
// Access Token 只存内存（JS 变量），绝不落 localStorage（防 XSS 读取，§10.2）；
// Refresh Token 由后端 HttpOnly + SameSite=Strict Cookie 管理，前端不可读。
// 所有业务请求经 authedFetch：自动附加 Authorization，遇 401 尝试刷新一次并
// 重放原请求，刷新失败则清空登录态（页面回到登录门控）。
import { useSyncExternalStore } from "react";

const BASE = "/api";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type: string;
}

let accessToken: string | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((l) => l());
}

export function isLoggedIn(): boolean {
  return accessToken !== null;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React 订阅登录态：变化时组件重渲染。 */
export function useAuthState(): boolean {
  return useSyncExternalStore(subscribeAuth, isLoggedIn);
}

function bearerHeaders(): Headers {
  const h = new Headers();
  if (accessToken) h.set("Authorization", `Bearer ${accessToken}`);
  return h;
}

async function refresh(): Promise<boolean> {
  // Refresh Cookie 由浏览器自动携带（Path=/api/auth）
  let res: Response;
  try {
    res = await fetch(`${BASE}/auth/refresh`, { method: "POST" });
  } catch {
    return false;
  }
  if (!res.ok) {
    if (accessToken) {
      accessToken = null; // 刷新失败（refresh 也过期/被撤销）→ 登出
      emit();
    }
    return false;
  }
  const data = (await res.json()) as TokenPair;
  accessToken = data.access_token;
  return true;
}

/**
 * 带鉴权的 fetch：自动附 Bearer；401 时刷新一次并重放，再失败抛错。
 * login / refresh 不走本函数（避免刷新递归）。
 */
export async function authedFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  let res = await fetch(`${BASE}${path}`, { ...init, headers });
  const retryable = res.status === 401 && accessToken !== null && !/^\/auth\/(login|refresh)/.test(path);

  if (retryable) {
    const ok = await refresh();
    if (ok) {
      const retryHeaders = new Headers(init?.headers);
      if (accessToken) retryHeaders.set("Authorization", `Bearer ${accessToken}`);
      res = await fetch(`${BASE}${path}`, { ...init, headers: retryHeaders });
    }
  }
  return res;
}

export async function login(username: string, password: string): Promise<void> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    throw new Error(await loginErrorMessage(res));
  }
  const data = (await res.json()) as TokenPair;
  accessToken = data.access_token;
  emit();
}

async function loginErrorMessage(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail?.error?.message === "string") return data.detail.error.message;
    if (typeof data?.detail === "string") {
      // 后端错误码 → 友好文案（限流等已直接返回中文 detail）
      if (data.detail === "INVALID_CREDENTIALS") return "用户名或密码错误";
      return data.detail;
    }
  } catch {
    // 非 JSON 响应，用状态码兜底
  }
  return `登录失败（${res.status}）`;
}

export async function logout(): Promise<void> {
  try {
    if (accessToken) {
      await fetch(`${BASE}/auth/logout`, { method: "POST", headers: bearerHeaders() });
    }
  } catch {
    // 登出失败不影响本地清理（token 进黑名单失败会自然过期）
  }
  accessToken = null;
  emit();
}
