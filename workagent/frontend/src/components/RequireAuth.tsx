import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { clearStoredToken, getStoredToken, hasStoredToken } from "../lib/auth";

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
        const payload = (await response.json()) as { authenticated?: boolean };
        if (cancelled) return;
        if (payload.authenticated) {
          setState("ok");
        } else {
          clearStoredToken();
          setState("fail");
        }
      } catch {
        if (cancelled) return;
        clearStoredToken();
        setState("fail");
      }
    }

    void verifySession();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "checking") {
    return <p className="subtle-copy">Validating session token...</p>;
  }

  if (state === "fail") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}
