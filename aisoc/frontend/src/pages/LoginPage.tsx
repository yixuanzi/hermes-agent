import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { hasStoredToken } from "../lib/auth";
import { LoginForm } from "../components/LoginForm";
import { BrandMark } from "../components/BrandMark";
import { HudGrid } from "../components/ambient/HudGrid";

interface BootstrapResponse {
  auth_scheme: string;
  admin_setup_required: boolean;
}

export function LoginPage() {
  const navigate = useNavigate();
  const [adminSetupRequired, setAdminSetupRequired] = useState(false);

  useEffect(() => {
    if (hasStoredToken()) {
      navigate("/overview", { replace: true });
    }
  }, [navigate]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/system/bootstrap")
      .then((response) => response.json() as Promise<BootstrapResponse>)
      .then((payload) => {
        if (!cancelled) setAdminSetupRequired(Boolean(payload.admin_setup_required));
      })
      .catch(() => {
        /* ignore: default to showing the normal login form */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="relative grid h-dvh place-items-center overflow-hidden p-[calc(24px*var(--density-scale))]">
      {/* Command Deck 环境层:深空极光 + 扫描网格,叠在登录卡片之下。 */}
      <div className="app-ambient" aria-hidden="true">
        <HudGrid />
      </div>

      <div className="login-card relative z-[1] w-[min(460px,95vw)] rounded-[var(--aisoc-radius-lg)] border border-aisoc-border bg-aisoc-panel p-[calc(30px*var(--density-scale))] shadow-[var(--aisoc-shadow)] backdrop-blur-[14px] animate-[aisoc-fade-in_340ms_ease]">
        <div className="mb-[calc(18px*var(--density-scale))] flex items-center gap-[calc(14px*var(--density-scale))]">
          <div className="brand-orb" aria-hidden="true">
            <BrandMark size={30} className="brand-orb-mark" />
          </div>
          <div className="brand-text">
            <h1>AISOC</h1>
          </div>
        </div>

        <p className="eyebrow mb-[calc(6px*var(--density-scale))] text-aisoc-accent">AISOC Access</p>
        <h2 className="font-display m-0 text-[calc(22px*var(--density-scale))] font-bold tracking-[0.01em]">
          Authenticate to Continue
        </h2>

        {adminSetupRequired ? (
          <p className="subtle-copy mt-[calc(6px*var(--density-scale))]">
            No admin account exists yet. Set <code className="font-mono">AISOC_BOOTSTRAP_ADMIN_PASSWORD</code> and
            restart the service to create the initial admin account.
          </p>
        ) : (
          <>
            <p className="subtle-copy mt-[calc(6px*var(--density-scale))]">
              Sign in with your AISOC username and password.
            </p>
            <LoginForm onSuccess={() => navigate("/overview", { replace: true })} />
            <button
              type="button"
              onClick={() => window.location.assign("/api/sso/start?sso=1")}
              className="mt-[calc(10px*var(--density-scale))] min-h-[calc(40px*var(--density-scale))] w-full cursor-pointer rounded-[var(--aisoc-radius-sm)] border border-aisoc-border bg-transparent px-[calc(12px*var(--density-scale))] py-[calc(10px*var(--density-scale))] font-bold text-aisoc-accent transition-colors duration-[160ms] hover:border-aisoc-accent"
            >
              SSO Sign-In
            </button>
          </>
        )}
      </div>
    </section>
  );
}
