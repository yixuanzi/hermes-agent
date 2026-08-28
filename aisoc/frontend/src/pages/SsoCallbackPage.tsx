import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { BrandMark } from "../components/BrandMark";
import { HudGrid } from "../components/ambient/HudGrid";
import { fetchJSON } from "../lib/api";
import { setStoredAuth } from "../lib/auth";
import type { AuthenticatedUser } from "../types";

interface SsoExchangeResponse {
  authenticated: boolean;
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthenticatedUser;
}

export function SsoCallbackPage() {
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  useEffect(() => {
    let active = true;
    const query = new URLSearchParams(window.location.search);
    const upstreamError = query.get("error_description") || query.get("error");
    if (upstreamError) {
      setError(upstreamError);
      return () => {
        active = false;
      };
    }

    void (async () => {
      try {
        const response = await fetchJSON<SsoExchangeResponse>("/api/sso/exchange", { method: "POST" }, false);
        if (!active) {
          return;
        }
        setStoredAuth(response.access_token, response.user);
        navigateRef.current("/overview", { replace: true });
      } catch (reason) {
        if (active) {
          setError(reason instanceof Error ? reason.message : "SSO login ticket exchange failed.");
        }
      }
    })();

    return () => {
      active = false;
    };
  }, []);

  return (
    <section className="relative grid h-dvh place-items-center overflow-hidden p-[calc(24px*var(--density-scale))]">
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
          {error ? "SSO Sign-In Failed" : "Completing SSO Sign-In"}
        </h2>

        {error ? (
          <>
            <p className="error-text mt-[calc(6px*var(--density-scale))]" role="alert">
              {error}
            </p>
            <p className="subtle-copy mt-[calc(10px*var(--density-scale))] text-center">
              <Link to="/login">Back to Sign In</Link>
            </p>
          </>
        ) : (
          <p className="subtle-copy mt-[calc(6px*var(--density-scale))]" role="status">
            Verifying your Portal identity and establishing a local session, please wait…
          </p>
        )}
      </div>
    </section>
  );
}
