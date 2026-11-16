import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { hasStoredToken } from "../lib/auth";
import { LoginForm } from "../components/LoginForm";

export function LoginPage() {
  const navigate = useNavigate();

  useEffect(() => {
    if (hasStoredToken()) {
      navigate("/overview", { replace: true });
    }
  }, [navigate]);

  return (
    <section className="login-page">
      <div className="login-card">
        <p className="brand-kicker">WORKAGENT Access</p>
        <h1>Authenticate to Continue</h1>
        <p className="subtle-copy">
          Enter the token from `WORKAGENT_SESSION_TOKEN` or the startup log output.
        </p>
        <LoginForm onSuccess={() => navigate("/overview", { replace: true })} />
      </div>
    </section>
  );
}
