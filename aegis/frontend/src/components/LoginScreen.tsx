import { FormEvent, useState } from 'react';

interface LoginScreenProps {
  notice?: string;
  onSubmit: (username: string, password: string) => Promise<void>;
  onAegisSsoLogin: () => void;
  onLarkSsoLogin: () => void;
  larkSsoEnabled: boolean;
  onSwitchToRegister: () => void;
  pending: boolean;
}

export default function LoginScreen({
  notice = '',
  onSubmit,
  onAegisSsoLogin,
  onLarkSsoLogin,
  larkSsoEnabled,
  onSwitchToRegister,
  pending,
}: LoginScreenProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [localError, setLocalError] = useState('');

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password.trim()) {
      setLocalError('请输入用户名和密码。');
      return;
    }

    setLocalError('');
    await onSubmit(username, password);
  }

  return (
    <section className="min-h-screen bg-[#020408] text-slate-200 flex items-center justify-center px-6">
      <div className="w-full max-w-md rounded-3xl border border-slate-800 bg-[#05080F] p-8 shadow-[0_0_40px_rgba(8,145,178,0.15)]">
        <p className="text-[11px] font-mono tracking-[0.32em] uppercase text-cyan-400">Aegis Access</p>
        <h1 className="mt-3 text-3xl font-black uppercase italic tracking-tight text-white">
          Sign In
        </h1>
        <p className="mt-3 text-sm leading-6 text-slate-400">
          使用用户名和密码登录 Aegis 控制台。默认管理员账号为高风险配置，仅建议用于本地初始化。
        </p>

        <form className="mt-8 space-y-4" onSubmit={handleSubmit}>
          <label className="block space-y-2">
            <span className="text-[10px] font-mono uppercase tracking-[0.24em] text-slate-500">
              Username
            </span>
            <input
              aria-label="Username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="Enter your username"
              className="w-full rounded-xl border border-slate-800 bg-[#020408] px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-500"
            />
          </label>
          <label className="block space-y-2">
            <span className="text-[10px] font-mono uppercase tracking-[0.24em] text-slate-500">
              Password
            </span>
            <input
              aria-label="Password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Enter your password"
              className="w-full rounded-xl border border-slate-800 bg-[#020408] px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-500"
            />
          </label>
          <button
            type="submit"
            disabled={pending}
            className="w-full rounded-xl bg-cyan-500 px-4 py-3 text-sm font-bold text-white transition hover:bg-cyan-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {pending ? 'Signing In...' : 'Sign In'}
          </button>
          <button
            type="button"
            onClick={onAegisSsoLogin}
            className="w-full rounded-xl border border-cyan-700 bg-cyan-950/40 px-4 py-3 text-sm font-semibold text-cyan-100 transition hover:border-cyan-400 hover:bg-cyan-900/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400"
          >
            Aegis SSO
          </button>
          {larkSsoEnabled ? (
            <button
              type="button"
              onClick={onLarkSsoLogin}
              className="w-full rounded-xl border border-sky-500/70 bg-sky-950/45 px-4 py-3 text-sm font-semibold text-sky-100 transition hover:border-sky-300 hover:bg-sky-900/55 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300"
            >
              Lark SSO
            </button>
          ) : null}
          <button
            type="button"
            onClick={onSwitchToRegister}
            className="w-full rounded-xl border border-slate-700 bg-transparent px-4 py-3 text-sm font-semibold text-slate-300 transition hover:border-cyan-500 hover:text-white"
          >
            Create Account
          </button>
          {notice ? <p className="text-sm text-emerald-400">{notice}</p> : null}
          {localError ? <p className="text-sm text-rose-400">{localError}</p> : null}
        </form>
      </div>
    </section>
  );
}
