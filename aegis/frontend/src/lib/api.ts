import { getStoredToken } from './auth';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

export function alertApiError(error: unknown, fallback: string): void {
  window.alert(getApiErrorMessage(error, fallback));
}

export async function fetchJSON<T>(
  path: string,
  init: RequestInit = {},
  includeAuth: boolean = true,
): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (!(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  if (includeAuth) {
    const token = getStoredToken();
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }
  }

  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const raw = await response.text();
    throw new ApiError(raw || response.statusText, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
