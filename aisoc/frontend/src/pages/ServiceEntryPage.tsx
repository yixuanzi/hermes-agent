import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";

import { hasStoredToken } from "../lib/auth";

type EntryState = "idle" | "launching" | "incomplete";

function readLaunchParams(): { organizationId: string; clientId: string } {
  const params = new URLSearchParams(window.location.search);
  return {
    organizationId: params.get("organization_id") || "",
    clientId: params.get("client_id") || "",
  };
}

/**
 * Root route. Portal's "my services" launch entry lands here with
 * organization_id + client_id; everything else falls back to the normal
 * authenticated/unauthenticated redirect.
 */
export function ServiceEntryPage() {
  const [state, setState] = useState<EntryState>(() => {
    const { organizationId, clientId } = readLaunchParams();
    if (!organizationId && !clientId) {
      return "idle";
    }
    return organizationId && clientId ? "launching" : "incomplete";
  });

  useEffect(() => {
    if (state !== "launching") {
      return;
    }
    const { organizationId, clientId } = readLaunchParams();
    const query = new URLSearchParams({ organization_id: organizationId, client_id: clientId });
    window.location.replace(`/api/sso/start?${query.toString()}`);
  }, [state]);

  if (state === "launching") {
    return null;
  }

  if (state === "incomplete") {
    return <Navigate to="/login" replace state={{ ssoNotice: "Service entry parameters are incomplete." }} />;
  }

  return <Navigate to={hasStoredToken() ? "/overview" : "/login"} replace />;
}
