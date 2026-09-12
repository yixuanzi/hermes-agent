import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SsoCallbackScreen from '../SsoCallbackScreen';

describe('SsoCallbackScreen', () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    window.history.replaceState({}, '', '/sso/callback');
    vi.restoreAllMocks();
  });

  it('uses the shared callback exchange endpoint for identity authentication', async () => {
    const onComplete = vi.fn().mockResolvedValue(undefined);
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({
      authenticated: true,
      access_token: 'local-jwt',
      token_type: 'bearer',
      expires_in: 28800,
      user: {
        uid: 'oidc-user',
        username: 'oidc_subject',
        email: 'user@example.com',
        status: 'enabled',
        create_time: '2026-01-01T00:00:00Z',
        last_login: null,
        role: 'user',
        is_admin: false,
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })) as typeof global.fetch;
    global.fetch = fetchSpy;

    render(<SsoCallbackScreen onComplete={onComplete} onBackToLogin={vi.fn()} />);

    await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/sso/exchange',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('shows an upstream OIDC error without attempting token exchange', async () => {
    window.history.replaceState({}, '', '/sso/callback?error=access_denied&error_description=组织授权失败');
    const fetchSpy = vi.fn();
    global.fetch = fetchSpy as typeof global.fetch;

    render(<SsoCallbackScreen onComplete={vi.fn()} onBackToLogin={vi.fn()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('组织授权失败');
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
