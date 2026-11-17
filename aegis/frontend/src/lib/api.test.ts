import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, fetchJSON, parseApiErrorMessage } from './api';

function respondWith(body: string, status: number, statusText: string) {
  global.fetch = vi.fn(async () => new Response(body, { status, statusText }));
}

async function errorFrom(body: string, status: number, statusText: string): Promise<ApiError> {
  respondWith(body, status, statusText);
  try {
    await fetchJSON('/api/anything', {}, false);
  } catch (error) {
    return error as ApiError;
  }
  throw new Error('fetchJSON resolved for a failed response');
}

describe('fetchJSON error messages', () => {
  afterEach(() => vi.restoreAllMocks());

  it('surfaces a FastAPI string detail instead of the raw JSON body', async () => {
    const error = await errorFrom('{"detail":"Prompt template tag \'triage\' already exists."}', 409, 'Conflict');

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.message).toBe("Prompt template tag 'triage' already exists.");
  });

  it('turns a validation list into field-prefixed messages', async () => {
    const body = JSON.stringify({
      detail: [
        { type: 'string_too_short', loc: ['body', 'password'], msg: 'String should have at least 8 characters' },
        { type: 'missing', loc: ['body', 'email'], msg: 'Field required' },
      ],
    });

    const error = await errorFrom(body, 422, 'Unprocessable Entity');

    expect(error.message).toBe('password: String should have at least 8 characters; email: Field required');
  });

  it('keeps a short plain-text body and falls back to the status text for HTML or empty bodies', () => {
    expect(parseApiErrorMessage('upstream timed out', 504, 'Gateway Timeout')).toBe('upstream timed out');
    expect(parseApiErrorMessage('<html><body>502 Bad Gateway</body></html>', 502, 'Bad Gateway')).toBe('Bad Gateway');
    expect(parseApiErrorMessage('', 503, 'Service Unavailable')).toBe('Service Unavailable');
    expect(parseApiErrorMessage('', 500, '')).toBe('Request failed (500)');
  });
});
