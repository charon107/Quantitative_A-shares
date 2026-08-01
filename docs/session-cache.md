# 浏览器会话缓存：切页/重开无需重新登录

## 设计决策

前端不引入 localStorage/sessionStorage 存储 Access Token（规范书 §10.2：防 XSS 读取），而是利用已有的 **HttpOnly Refresh Cookie** 实现无感会话恢复：

- Refresh Token 在 `POST /api/auth/refresh` 返回时由后端 `Set-Cookie: wn_refresh=...; HttpOnly; SameSite=Strict; Path=/api/auth` 下发
- 浏览器自动在所有 `/api/auth/*` 请求中携带该 Cookie（JS 无法读取），跨标签页/重启浏览器后仍存活（7 天 TTL）
- 应用启动时，`App.tsx` 的 `useEffect` 调 `tryRestoreSession()` → 走 `POST /api/auth/refresh` → Cookie 自动携带 → 后端验签 + 轮转 → 前端拿到新 Access Token 写入内存 → `useAuthState` 通知 React 重渲染 → 跳过登录页

## 数据流

```
页面加载 → App.tsx mount
  │
  ├─ useEffect: tryRestoreSession()
  │    └─ refresh() → POST /api/auth/refresh (Cookie: wn_refresh)
  │         ├─ 200 → accessToken = new_token + emit() → useAuthState 更新 → PaperTrade 渲染
  │         └─ !200 / 网络错 → 静默返回 false → 用户看到 Login 页
  │
  └─ useState: sessionReady (初始 false)
       ├─ !sessionReady && page=paper → <Loading label="恢复会话…" />
       └─ sessionReady && page=paper → loggedIn ? <PaperTrade /> : <Login />
```

## 边界情况

| 场景 | 行为 |
|---|---|
| Refresh Cookie 有效 | 静默恢复，直接进模拟盘（~200ms） |
| Refresh Cookie 过期/不存在 | `refresh()` 返回 false，显示登录页 |
| 网络不通 | 同上，静默降级 |
| Access 过期但 Refresh 有效 | `authedFetch` 的 401 拦截器自动刷新后重放（已有逻辑，本次未改） |
| 同浏览器新标签页 | Cookie 共享，新标签页也能静默恢复 |
| 用户主动登出 | `logout()` 调后端撤销 token + 删 Cookie + `accessToken = null`；下次启动无法恢复 |
| SameSite=Strict 限制 | 同站点下正常；从外部链接跳转到模拟盘页时，Cookie 不会被携带（浏览器安全策略），需手动登录 |

## 关键代码

- `frontend/src/api/auth.ts` — `tryRestoreSession()` 封装 `refresh()` + 错误静默处理；同时修复了 `refresh()` 成功时缺 `emit()` 的 bug
- `frontend/src/App.tsx` — `useEffect` 启动时调用 + `sessionReady` 状态控制 Loading 过渡

## 修复的 Bug

`refresh()` 函数成功获取新 access token 后，原实现只设置 `accessToken = data.access_token` 但没有调 `emit()` 通知 `useSyncExternalStore` 订阅者。导致 App 层 `useAuthState()` 永远不更新——启动恢复完成但 React 不会重渲染到 PaperTrade。已在本次改动中修复（加 `emit()` 调用）。

## 为什么不用 localStorage

- Access Token 存 localStorage → 任何 JS 代码（包括注入的 XSS payload）可读取 → 规范书 §10.2 明确禁止
- Refresh Token 已通过 HttpOnly Cookie 持久化，前端无需额外存储
- 页面加载时 ~200ms 的恢复请求开销远小于安全风险
