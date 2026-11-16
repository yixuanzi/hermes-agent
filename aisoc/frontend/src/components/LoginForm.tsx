import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import { fetchJSON } from "../lib/api";
import { setStoredAuth } from "../lib/auth";
import type { AuthenticatedUser } from "../types";

type LoginFormProps = {
  onSuccess: (user: AuthenticatedUser) => void;
};

interface LoginResponse {
  authenticated: boolean;
  access_token: string;
  user: AuthenticatedUser;
}

export function LoginForm({ onSuccess }: LoginFormProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");

    try {
      const response = await fetchJSON<LoginResponse>(
        "/api/auth/login",
        {
          method: "POST",
          body: JSON.stringify({ username, password }),
        },
        false,
      );
      if (!response.authenticated) {
        setError("Invalid username or password.");
        return;
      }
      setStoredAuth(response.access_token, response.user);
      onSuccess(response.user);
    } catch {
      setError("Invalid username or password.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="grid gap-[calc(10px*var(--density-scale))]" onSubmit={handleSubmit}>
      <label htmlFor="username-input">Username</label>
      <input
        id="username-input"
        type="text"
        autoComplete="username"
        value={username}
        onChange={(event) => setUsername(event.target.value)}
        placeholder="Enter your username"
        required
      />
      <label htmlFor="password-input">Password</label>
      <input
        id="password-input"
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        placeholder="Enter your password"
        required
      />
      <button
        type="submit"
        disabled={pending}
        className="min-h-[calc(40px*var(--density-scale))] cursor-pointer rounded-[var(--aisoc-radius-sm)] border border-transparent bg-aisoc-accent px-[calc(12px*var(--density-scale))] py-[calc(10px*var(--density-scale))] font-bold text-aisoc-on-accent transition-colors duration-[160ms] hover:bg-aisoc-accent-strong disabled:cursor-not-allowed disabled:opacity-55"
      >
        {pending ? "Signing In..." : "Sign In"}
      </button>
      {error ? <p className="error-text">{error}</p> : null}
      <p className="subtle-copy text-center">
        No account yet? <Link to="/register">Create one</Link>
      </p>
    </form>
  );
}
