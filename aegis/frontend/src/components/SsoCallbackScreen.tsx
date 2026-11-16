import { useEffect, useRef, useState } from 'react';
import { fetchJSON } from '../lib/api';
import type { AuthenticatedUser } from '../types';

interface SsoExchangeResponse {
  authenticated: boolean;
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthenticatedUser;
}

interface SsoCallbackScreenProps {
  onComplete: (response: SsoExchangeResponse) => Promise<void>;
  onBackToLogin: () => void;
}

export default function SsoCallbackScreen({ onComplete, onBackToLogin }: SsoCallbackScreenProps) {
  const [error, setError] = useState('');
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  useEffect(() => {
    let active = true;
    const query = new URLSearchParams(window.location.search);
    const upstreamError = query.get('error_description') || query.get('error');
    if (upstreamError) {
      setError(upstreamError);
      return () => {
        active = false;
      };
    }

    void (async () => {
      try {
        const response = await fetchJSON<SsoExchangeResponse>('/api/sso/exchange', { method: 'POST' }, false);
        if (active) {
          await onCompleteRef.current(response);
        }
      } catch (reason) {
        if (active) {
          setError(reason instanceof Error ? reason.message : 'SSO 登录票据兑换失败。');
        }
      }
    })();

    return () => {
      active = false;
    };
  }, []);

  return (
    <section className="min-h-screen bg-[#020408] text-slate-200 flex items-center justify-center px-6">
      <div className="w-full max-w-md rounded-3xl border border-slate-800 bg-[#05080F] p-8 shadow-[0_0_40px_rgba(8,145,178,0.15)]">
        <p className="text-[11px] font-mono tracking-[0.32em] uppercase text-cyan-400">Aegis Access</p>
        <h1 className="mt-3 text-3xl font-black uppercase italic tracking-tight text-white">
          {error ? 'SSO 登录失败' : 'Completing SSO'}
        </h1>
        {error ? (
          <>
            <p className="mt-4 text-sm leading-6 text-rose-300" role="alert">{error}</p>
            <button
              type="button"
              onClick={onBackToLogin}
              className="mt-8 w-full rounded-xl border border-slate-700 bg-transparent px-4 py-3 text-sm font-semibold text-slate-300 transition hover:border-cyan-500 hover:text-white"
            >
              返回登录页
            </button>
          </>
        ) : (
          <p className="mt-4 text-sm leading-6 text-slate-400" role="status">
            正在完成身份认证并建立本地会话，请稍候…
          </p>
        )}
      </div>
    </section>
  );
}
