import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import { BrandMark } from "../components/BrandMark";
import { HudGrid } from "../components/ambient/HudGrid";
import { fetchJSON } from "../lib/api";

interface RegisterResponse {
  registered: boolean;
  status: string;
}

export function RegisterPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [registered, setRegistered] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");

    try {
      const response = await fetchJSON<RegisterResponse>(
        "/api/auth/register",
        {
          method: "POST",
          body: JSON.stringify({ username, password, email }),
        },
        false,
      );
      if (response.registered) {
        setRegistered(true);
      }
    } catch {
      setError("Registration failed. Check your username, password, and email.");
    } finally {
      setPending(false);
    }
  }

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
          Create Account
        </h2>

        {registered ? (
          <>
            <p className="subtle-copy mt-[calc(6px*var(--density-scale))]">
              Your account was created and is waiting for an administrator to enable it before you can sign in.
            </p>
            <p className="subtle-copy mt-[calc(10px*var(--density-scale))] text-center">
              <Link to="/login">Back to Sign In</Link>
            </p>
          </>
        ) : (
          <>
            <p className="subtle-copy mt-[calc(6px*var(--density-scale))]">
              New accounts are created disabled and require admin approval before they can sign in.
            </p>
            <form className="grid gap-[calc(10px*var(--density-scale))]" onSubmit={handleSubmit}>
              <label htmlFor="register-username">Username</label>
              <input
                id="register-username"
                type="text"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="new-user"
                required
              />
              <label htmlFor="register-password">Password</label>
              <input
                id="register-password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Create a password"
                required
              />
              <label htmlFor="register-email">Email</label>
              <input
                id="register-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@example.com"
                required
              />
              <button
                type="submit"
                disabled={pending}
                className="min-h-[calc(40px*var(--density-scale))] cursor-pointer rounded-[var(--aisoc-radius-sm)] border border-transparent bg-aisoc-accent px-[calc(12px*var(--density-scale))] py-[calc(10px*var(--density-scale))] font-bold text-aisoc-on-accent transition-colors duration-[160ms] hover:bg-aisoc-accent-strong disabled:cursor-not-allowed disabled:opacity-55"
              >
                {pending ? "Registering..." : "Register"}
              </button>
              {error ? <p className="error-text">{error}</p> : null}
              <p className="subtle-copy text-center">
                <Link to="/login">Back to Sign In</Link>
              </p>
            </form>
          </>
        )}
      </div>
    </section>
  );
}
