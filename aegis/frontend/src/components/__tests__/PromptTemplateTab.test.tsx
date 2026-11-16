import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PromptTemplateTab from '../PromptTemplateTab';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('PromptTemplateTab', () => {
  it('creates, edits, and deletes the signed-in user template through the CRUD API', async () => {
    const requests: Array<{ url: string; method: string }> = [];
    let templates: Array<Record<string, string>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method || 'GET';
      requests.push({ url, method });
      if (url === '/api/prompt-templates' && method === 'GET') return jsonResponse({ templates });
      if (url === '/api/prompt-templates' && method === 'POST') {
        const body = JSON.parse(String(init?.body));
        const created = { id: 'tpl-1', ...body, create_time: '2026-01-01T00:00:00Z', update_time: '2026-01-01T00:00:00Z' };
        templates = [created];
        return jsonResponse(created, 201);
      }
      if (url === '/api/prompt-templates/tpl-1' && method === 'PUT') {
        const body = JSON.parse(String(init?.body));
        const updated = { ...templates[0], ...body, update_time: '2026-01-02T00:00:00Z' };
        templates = [updated];
        return jsonResponse(updated);
      }
      if (url === '/api/prompt-templates/tpl-1' && method === 'DELETE') {
        templates = [];
        return jsonResponse({ deleted: true, id: 'tpl-1' });
      }
      throw new Error(`Unhandled request: ${method} ${url}`);
    }));
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<PromptTemplateTab />);
    expect(screen.getByRole('heading', { name: 'Prompt Templates' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Personal Prompt Library' })).toBeInTheDocument();
    expect(await screen.findByText(/no prompt templates yet/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /new template/i }));
    expect(screen.getByRole('dialog', { name: /create prompt template/i })).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'Template tag' }), { target: { value: 'Triage' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Template description' }), { target: { value: 'First response' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Template prompt' }), { target: { value: 'Assess this incident.' } });
    fireEvent.click(screen.getByRole('button', { name: /save template/i }));
    await waitFor(() => expect(requests).toContainEqual({ url: '/api/prompt-templates', method: 'POST' }));
    expect(await screen.findByText('First response')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'View Triage' }));
    expect(screen.getByRole('dialog', { name: 'Triage' })).toHaveTextContent('Assess this incident.');
    fireEvent.click(screen.getByRole('button', { name: 'Close template dialog' }));

    fireEvent.click(screen.getByRole('button', { name: 'Edit Triage' }));
    expect(screen.getByRole('dialog', { name: /update prompt template/i })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Template description' }), { target: { value: 'Updated response' } });
    fireEvent.click(screen.getByRole('button', { name: /save template/i }));
    await waitFor(() => expect(requests).toContainEqual({ url: '/api/prompt-templates/tpl-1', method: 'PUT' }));
    expect(await screen.findByText('Updated response')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Delete Triage' }));
    await waitFor(() => expect(screen.getByText(/no prompt templates yet/i)).toBeInTheDocument());
    expect(requests).toEqual(expect.arrayContaining([
      { url: '/api/prompt-templates', method: 'POST' },
      { url: '/api/prompt-templates/tpl-1', method: 'PUT' },
      { url: '/api/prompt-templates/tpl-1', method: 'DELETE' },
    ]));
  });
});
