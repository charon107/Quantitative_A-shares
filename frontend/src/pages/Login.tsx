// 登录页（规范书 §7.11）：用户名密码 → /api/auth/login。
// 登录成功后 Access Token 存内存、Refresh Cookie 由后端下发，
// App 的 useAuthState 订阅到变化即自动切换到业务页。
import { useState } from "react";
import { login } from "../api/auth";
import { Card, CardHeader } from "../components/Card";

export function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const valid = username.trim().length > 0 && password.length > 0 && !busy;

  const submit = async () => {
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      // 登录成功后 App 通过 useAuthState 自动切换到模拟盘页
    } catch (e) {
      setError(e instanceof Error ? e.message : "登录失败，请稍后再试");
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-md pt-10">
      <Card>
        <CardHeader title="登录" subtitle="登录后可访问模拟盘（行情数据无需登录）" />
        <div className="space-y-4 px-5 pb-5">
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">用户名</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder="请输入用户名"
              className="w-full rounded-xl border border-line bg-panel px-4 py-2.5 text-sm text-ink placeholder:text-muted shadow-soft outline-none transition focus:border-clay focus:ring-2 focus:ring-clay/20"
            />
          </div>
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">密码</label>
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              type="password"
              autoComplete="current-password"
              placeholder="请输入密码"
              className="w-full rounded-xl border border-line bg-panel px-4 py-2.5 text-sm text-ink placeholder:text-muted shadow-soft outline-none transition focus:border-clay focus:ring-2 focus:ring-clay/20"
            />
          </div>
          {error && <p className="text-xs text-down">{error}</p>}
          <button
            onClick={submit}
            disabled={!valid}
            className="w-full rounded-xl bg-clay px-4 py-2.5 text-sm font-medium text-white shadow-soft transition hover:bg-clayDark disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "登录中…" : "登录"}
          </button>
          <p className="text-xs text-muted">
            登录由服务器签发 JWT；Access Token 仅存于内存，Refresh Token 走 HttpOnly Cookie。
          </p>
        </div>
      </Card>
    </div>
  );
}
