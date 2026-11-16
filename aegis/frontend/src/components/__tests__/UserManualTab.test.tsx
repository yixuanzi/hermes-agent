import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import UserManualTab from '../UserManualTab';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('UserManualTab', () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('loads the default manual, renders GFM tables, and switches from the directory', async () => {
    global.fetch = vi.fn(async (input) => {
      const url = String(input);
      if (url === '/api/user-manuals') return jsonResponse({
        manuals: [{ id: 'core', title: 'CORE MODULES Guide' }, { id: 'admin', title: 'CONTROL Guide' }],
        default_manual_id: 'core',
      });
      if (url === '/api/user-manuals/core') return jsonResponse({ id: 'core', title: 'CORE MODULES Guide', content: '# CORE MODULES Guide\n\n| Feature | Path |\n| --- | --- |\n| Chat | /chat |' });
      if (url === '/api/user-manuals/admin') return jsonResponse({ id: 'admin', title: 'CONTROL Guide', content: '# CONTROL Guide\n\nAdmin operations.' });
      throw new Error(`Unhandled URL: ${url}`);
    }) as typeof global.fetch;

    render(<UserManualTab />);

    expect(await screen.findByRole('heading', { name: 'CORE MODULES Guide' })).toBeInTheDocument();
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'CORE MODULES Guide' })).toHaveAttribute('aria-current', 'page');

    fireEvent.click(screen.getByRole('button', { name: 'CONTROL Guide' }));
    expect(await screen.findByRole('heading', { name: 'CONTROL Guide' })).toBeInTheDocument();
    expect(screen.getByText('Admin operations.')).toBeInTheDocument();
  });

  it('shows a retryable directory error', async () => {
    global.fetch = vi.fn(async () => new Response('directory unavailable', { status: 503 })) as typeof global.fetch;
    render(<UserManualTab />);

    expect(await screen.findByText('directory unavailable')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
  });
});
