import type { AuthenticatedUser } from '../types';

const TOKEN_KEY = 'aegis_session_token';
const USER_KEY = 'aegis_current_user';

export function getStoredToken(): string {
  return window.localStorage.getItem(TOKEN_KEY) || '';
}

export function getStoredUser(): AuthenticatedUser | null {
  const raw = window.localStorage.getItem(USER_KEY);
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
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function setStoredUser(user: AuthenticatedUser): void {
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearStoredAuth(): void {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}

export function hasStoredToken(): boolean {
  return getStoredToken().trim().length > 0;
}
