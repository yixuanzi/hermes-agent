import { getStoredToken } from './auth';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

interface ValidationIssue {
  loc?: unknown;
  msg?: unknown;
}

// Longer non-JSON bodies are proxy/server error pages, not messages meant for a person.
const MAX_PLAIN_TEXT_ERROR_LENGTH = 300;

function formatValidationIssue(issue: ValidationIssue): string {
  const msg = typeof issue.msg === 'string' ? issue.msg : '';
  const loc = Array.isArray(issue.loc) ? issue.loc : [];
  const field = [...loc].reverse().find((part) => part !== 'body' && typeof part === 'string');
  return field && msg ? `${field}: ${msg}` : msg;
}

function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((issue) => (issue && typeof issue === 'object' ? formatValidationIssue(issue as ValidationIssue) : ''))
      .filter(Boolean)
      .join('; ');
  }
  if (detail && typeof detail === 'object') {
    const { message, msg } = detail as { message?: unknown; msg?: unknown };
    if (typeof message === 'string') return message;
    if (typeof msg === 'string') return msg;
  }
  return '';
}

/** Turn a failed response body (FastAPI's `{"detail": ...}` or plain text) into a readable message. */
export function parseApiErrorMessage(raw: string, status: number, statusText: string): string {
  const fallback = statusText || `Request failed (${status})`;
  const text = raw.trim();
  if (!text) {
    return fallback;
  }
  try {
    const payload = JSON.parse(text) as unknown;
    if (payload && typeof payload === 'object' && 'detail' in payload) {
      return formatDetail((payload as { detail: unknown }).detail) || fallback;
    }
    return fallback;
  } catch {
    const looksLikeHtml = text.startsWith('<');
    return looksLikeHtml || text.length > MAX_PLAIN_TEXT_ERROR_LENGTH ? fallback : text;
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
    throw new ApiError(parseApiErrorMessage(raw, response.status, response.statusText), response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
