import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { clearStoredAuth, getStoredToken, hasStoredToken, setStoredUser } from "../lib/auth";
import { CurrentUserProvider } from "../lib/authContext";
import type { AuthenticatedUser } from "../types";

interface SessionResponse {
  authenticated: boolean;
  user?: AuthenticatedUser;
}

export function RequireAuth() {
  const location = useLocation();
  const [state, setState] = useState<"checking" | "ok" | "fail">(() =>
    hasStoredToken() ? "checking" : "fail",
  );

  useEffect(() => {
    if (!hasStoredToken()) {
      setState("fail");
      return;
    }

    let cancelled = false;

    async function verifySession() {
      try {
        const token = getStoredToken();
        const response = await fetch("/api/auth/session", {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });
        if (!response.ok) {
          throw new Error(`session check failed: ${response.status}`);
        }
        const payload = (await response.json()) as SessionResponse;
        if (cancelled) return;
        if (payload.authenticated && payload.user) {
          setStoredUser(payload.user);
          setState("ok");
        } else {
          clearStoredAuth();
          setState("fail");
        }
      } catch {
        if (cancelled) return;
        clearStoredAuth();
        setState("fail");
      }
    }

    void verifySession();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "checking") {
    return <p className="subtle-copy">Validating session...</p>;
  }

  if (state === "fail") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return (
    <CurrentUserProvider>
      <Outlet />
    </CurrentUserProvider>
  );
}
