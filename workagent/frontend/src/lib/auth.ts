const TOKEN_KEY = "workagent.sessionToken";

export function getStoredToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setStoredToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearStoredToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function hasStoredToken(): boolean {
  return getStoredToken().trim().length > 0;
}

