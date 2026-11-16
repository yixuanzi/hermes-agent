import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AgentPolicyPanel from '../AgentPolicyPanel';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('AgentPolicyPanel', () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    cleanup();
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('loads, searches, creates, edits, and deletes Agent Policy rules', async () => {
    const requests: Array<{ url: string; method: string }> = [];
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    global.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method || 'GET';
      requests.push({ url, method });
      if (url === '/api/routing/agent' && method === 'GET') {
        return jsonResponse({ policies: [{ rank_id: 1, platform: 'aegis', user_id: 'u-1', agent_name: 'responder', status: 'deny' }] });
      }
      if (url === '/api/routing/agent' && method === 'POST') {
        return jsonResponse({ rank_id: 2, platform: 'slack', user_id: '*', agent_name: 'triage', status: 'allow' }, 201);
      }
      if (url === '/api/routing/agent/2' && method === 'PUT') {
        return jsonResponse({ rank_id: 3, platform: 'slack', user_id: '*', agent_name: 'triage', status: 'deny' });
      }
      if (url === '/api/routing/agent/3' && method === 'DELETE') {
        return jsonResponse({ deleted: true, rank_id: 3 });
      }
      throw new Error(`Unhandled request: ${method} ${url}`);
    }) as typeof global.fetch;

    render(<AgentPolicyPanel agents={[]} />);
    expect(await screen.findByText('responder')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/search agent policy/i), { target: { value: 'missing' } });
    expect(screen.getByText(/no agent policy rules found/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/search agent policy/i), { target: { value: '' } });

    fireEvent.click(screen.getByRole('button', { name: /new policy/i }));
    fireEvent.change(screen.getByLabelText(/agent policy platform/i), { target: { value: 'slack' } });
    fireEvent.change(screen.getByLabelText(/agent policy agent/i), { target: { value: 'triage' } });
    fireEvent.click(screen.getByRole('button', { name: /save policy/i }));
    expect(await screen.findByText('triage')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Edit Agent Policy 2'));
    fireEvent.change(screen.getByLabelText(/agent policy rank/i), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText(/agent policy decision/i), { target: { value: 'deny' } });
    fireEvent.click(screen.getByRole('button', { name: /save policy/i }));
    await waitFor(() => expect(screen.getByLabelText('Delete Agent Policy 3')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Delete Agent Policy 3'));
    await waitFor(() => expect(screen.queryByText('triage')).not.toBeInTheDocument());
    expect(requests).toEqual(expect.arrayContaining([
      { url: '/api/routing/agent', method: 'POST' },
      { url: '/api/routing/agent/2', method: 'PUT' },
      { url: '/api/routing/agent/3', method: 'DELETE' },
    ]));
  });

  it('disables refresh and CRUD controls while a delete is in flight', async () => {
    let resolveDelete: ((response: Response) => void) | undefined;
    const pendingDelete = new Promise<Response>((resolve) => { resolveDelete = resolve; });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    global.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method || 'GET';
      if (url === '/api/routing/agent' && method === 'GET') {
        return jsonResponse({ policies: [{ rank_id: 1, platform: '*', user_id: '*', agent_name: 'responder', status: 'deny' }] });
      }
      if (url === '/api/routing/agent/1' && method === 'DELETE') return pendingDelete;
      throw new Error(`Unhandled request: ${method} ${url}`);
    }) as typeof global.fetch;

    render(<AgentPolicyPanel />);
    fireEvent.click(await screen.findByLabelText('Delete Agent Policy 1'));

    expect(screen.getByRole('button', { name: /^refresh$/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /new policy/i })).toBeDisabled();
    expect(screen.getByLabelText('Edit Agent Policy 1')).toBeDisabled();
    expect(screen.getByLabelText('Delete Agent Policy 1')).toBeDisabled();
    expect(screen.getByLabelText('Delete Agent Policy 1')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByLabelText('Delete Agent Policy 1')).toHaveClass('aegis-btn--busy');

    resolveDelete?.(jsonResponse({ deleted: true, rank_id: 1 }));
    await waitFor(() => expect(screen.queryByText('responder')).not.toBeInTheDocument());
  });

  it('marks the save action busy without changing the modal workflow', async () => {
    let resolveSave: ((response: Response) => void) | undefined;
    const pendingSave = new Promise<Response>((resolve) => { resolveSave = resolve; });
    global.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method || 'GET';
      if (url === '/api/routing/agent' && method === 'GET') {
        return jsonResponse({ policies: [] });
      }
      if (url === '/api/routing/agent' && method === 'POST') return pendingSave;
      throw new Error(`Unhandled request: ${method} ${url}`);
    }) as typeof global.fetch;

    render(<AgentPolicyPanel />);
    await screen.findByText(/no agent policy rules found/i);
    fireEvent.click(screen.getByRole('button', { name: /new policy/i }));
    fireEvent.click(screen.getByRole('button', { name: /save policy/i }));

    const saveButton = screen.getByRole('button', { name: /save policy/i });
    expect(saveButton).toBeDisabled();
    expect(saveButton).toHaveAttribute('aria-busy', 'true');
    expect(saveButton).toHaveClass('aegis-btn--busy');

    resolveSave?.(jsonResponse({
      rank_id: 1,
      platform: '*',
      user_id: '*',
      agent_name: '*',
      status: 'allow',
    }, 201));
    await waitFor(() => expect(screen.queryByRole('button', { name: /save policy/i })).not.toBeInTheDocument());
  });
});
