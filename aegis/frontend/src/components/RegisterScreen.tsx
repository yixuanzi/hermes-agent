import { FormEvent, useState } from 'react';

interface RegisterScreenProps {
  pending: boolean;
  onSubmit: (username: string, password: string, email: string) => Promise<void>;
  onSwitchToLogin: () => void;
}

export default function RegisterScreen({
  pending,
  onSubmit,
  onSwitchToLogin,
}: RegisterScreenProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [localError, setLocalError] = useState('');

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password.trim() || !email.trim()) {
      setLocalError('请完整填写用户名、密码和邮箱。');
      return;
    }
    setLocalError('');
    await onSubmit(username, password, email);
  }

  return (
    <section className="min-h-screen bg-[#020408] text-slate-200 flex items-center justify-center px-6">
      <div className="w-full max-w-md rounded-3xl border border-slate-800 bg-[#05080F] p-8 shadow-[0_0_40px_rgba(8,145,178,0.15)]">
        <p className="text-[11px] font-mono tracking-[0.32em] uppercase text-cyan-400">Aegis Access</p>
        <h1 className="mt-3 text-3xl font-black uppercase italic tracking-tight text-white">
          Register
        </h1>
        <p className="mt-3 text-sm leading-6 text-slate-400">
          注册成功后账号默认为停用状态，需要管理员在用户管理中启用后才能登录。
        </p>

        <form className="mt-8 space-y-4" onSubmit={handleSubmit}>
          <label className="block space-y-2">
            <span className="text-[10px] font-mono uppercase tracking-[0.24em] text-slate-500">
              Username
            </span>
            <input
              aria-label="Register Username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="new-user"
              className="w-full rounded-xl border border-slate-800 bg-[#020408] px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-500"
            />
          </label>
          <label className="block space-y-2">
            <span className="text-[10px] font-mono uppercase tracking-[0.24em] text-slate-500">
              Password
            </span>
            <input
              aria-label="Register Password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Create a password"
              className="w-full rounded-xl border border-slate-800 bg-[#020408] px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-500"
            />
          </label>
          <label className="block space-y-2">
            <span className="text-[10px] font-mono uppercase tracking-[0.24em] text-slate-500">
              Email
            </span>
            <input
              aria-label="Register Email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="name@example.com"
              className="w-full rounded-xl border border-slate-800 bg-[#020408] px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-500"
            />
          </label>
          <button
            type="submit"
            disabled={pending}
            className="w-full rounded-xl bg-cyan-500 px-4 py-3 text-sm font-bold text-white transition hover:bg-cyan-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {pending ? 'Registering...' : 'Register'}
          </button>
          <button
            type="button"
            onClick={onSwitchToLogin}
            className="w-full rounded-xl border border-slate-700 bg-transparent px-4 py-3 text-sm font-semibold text-slate-300 transition hover:border-cyan-500 hover:text-white"
          >
            Back To Sign In
          </button>
          {localError ? <p className="text-sm text-rose-400">{localError}</p> : null}
        </form>
      </div>
    </section>
  );
}
