import type { AuthenticatedUser } from "../types";

const TOKEN_KEY = "aisoc.accessToken";
const USER_KEY = "aisoc.currentUser";

export function getStoredToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function getStoredUser(): AuthenticatedUser | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as AuthenticatedUser;
  } catch {
    return null;
  }
}

export function setStoredAuth(token: string, user: AuthenticatedUser): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function setStoredUser(user: AuthenticatedUser): void {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearStoredAuth(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function hasStoredToken(): boolean {
  return getStoredToken().trim().length > 0;
}
