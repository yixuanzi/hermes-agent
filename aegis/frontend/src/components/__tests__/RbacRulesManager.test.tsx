import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import SettingsTab from '../SettingsTab';


function jsonResponse(payload: unknown, status: number = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}


const rules = {
  admin: {
    summary: 'Full access',
    prompt_constraints: ['Use safe operations.'],
    allow_tools: null,
    denied_tools: [],
    tools_paras: { terminal: [{ command: '^ls' }] },
  },
  operator: {
    summary: 'Operator access',
    prompt_constraints: [],
    allow_tools: ['read_file'],
    denied_tools: [],
    tools_paras: {},
  },
  user: {
    summary: 'Read only',
    prompt_constraints: ['Do not write.'],
    allow_tools: ['read_file'],
    denied_tools: ['terminal'],
    tools_paras: {},
  },
};


describe('RBAC Rules Settings editor', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    window.localStorage.clear();
    window.localStorage.setItem('aegis_session_token', 'rbac-rules-jwt');
  });

  afterEach(() => {
    cleanup();
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('loads three roles with accessible Settings and role tabs', async () => {
    global.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/health') return jsonResponse({ status: 'ok', pid: 123 });
      if (url === '/api/rbac-rules') return jsonResponse({ rules });
      throw new Error(`Unhandled request: ${init?.method || 'GET'} ${url}`);
    }) as typeof global.fetch;

    render(<SettingsTab />);
    const settingsTab = screen.getByRole('tab', { name: 'RBAC Rules' });
    fireEvent.click(settingsTab);

    expect(settingsTab).toHaveAttribute('aria-selected', 'true');
    expect(settingsTab).toHaveAttribute('aria-controls', 'settings-rbac-rules-panel');
    await screen.findByDisplayValue('Full access');
    const rbacPanel = document.getElementById('settings-rbac-rules-panel');
    expect(rbacPanel).toBeInTheDocument();
    expect(rbacPanel).toHaveAttribute('aria-labelledby', 'settings-rbac-rules-tab');
    expect(await screen.findByDisplayValue('Full access')).toBeInTheDocument();
    expect(screen.getAllByRole('tab', { name: /admin|operator|user/i })).toHaveLength(3);
    expect(screen.getByRole('tab', { name: 'Admin' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByDisplayValue('Use safe operations.')).toBeInTheDocument();
    const collapsedTool = screen.getByRole('button', { name: 'Expand terminal parameter rules' });
    expect(collapsedTool).toHaveAttribute('aria-expanded', 'false');
    expect(collapsedTool).toHaveAttribute('aria-controls');
    fireEvent.click(collapsedTool);
    expect(screen.getByRole('button', { name: 'Collapse terminal parameter rules' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('keeps role drafts separate and sends only persisted fields when saving', async () => {
    const requests: Array<{ url: string; method: string; body?: string }> = [];
    const updatedRule = {
      ...rules.admin,
      summary: 'Updated administrator policy',
    };
    global.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method || 'GET';
      requests.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined });
      if (url === '/health') return jsonResponse({ status: 'ok', pid: 123 });
      if (url === '/api/rbac-rules' && method === 'GET') return jsonResponse({ rules });
      if (url === '/api/rbac-rules/admin' && method === 'PUT') return jsonResponse({ role: 'admin', rule: updatedRule, restart_required: true });
      if (url === '/api/rbac-rules/test' && method === 'POST') return jsonResponse({ matched: true });
      throw new Error(`Unhandled request: ${method} ${url}`);
    }) as typeof global.fetch;

    render(<SettingsTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'RBAC Rules' }));
    await screen.findByDisplayValue('Full access');

    const adminSummary = screen.getByLabelText('Admin summary');
    fireEvent.change(adminSummary, { target: { value: updatedRule.summary } });
    fireEvent.click(screen.getByRole('tab', { name: 'Operator' }));
    expect(screen.getByDisplayValue('Operator access')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Admin' }));
    expect(screen.getByDisplayValue(updatedRule.summary)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Expand terminal parameter rules' }));
    const testText = screen.getByLabelText('Parameter 1.1.1 test text');
    fireEvent.change(testText, { target: { value: 'ls -la' } });
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    expect(await screen.findByText('Pattern matched.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Save role' }));
    await waitFor(() => expect(requests.some((request) => request.url === '/api/rbac-rules/admin' && request.method === 'PUT')).toBe(true));
    const saveRequest = requests.find((request) => request.url === '/api/rbac-rules/admin' && request.method === 'PUT');
    expect(JSON.parse(saveRequest?.body || '{}')).toEqual(updatedRule);
    expect(saveRequest?.body).not.toContain('ls -la');
    expect(await screen.findByText(/restart Hermes\/Aegis/i)).toBeInTheDocument();
  });

  it('supports multiple single-line constraints, whitelist toggling, and nested parameter rows', async () => {
    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/health') return jsonResponse({ status: 'ok', pid: 123 });
      if (url === '/api/rbac-rules') return jsonResponse({ rules });
      throw new Error(`Unhandled request: ${url}`);
    }) as typeof global.fetch;

    render(<SettingsTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'RBAC Rules' }));
    await screen.findByDisplayValue('Full access');

    const constraint = screen.getByLabelText('Prompt constraints 1');
    expect(constraint.tagName).toBe('INPUT');
    fireEvent.change(constraint, { target: { value: 'line one' } });
    expect(constraint).toHaveValue('line one');
    const promptList = screen.getByRole('heading', { name: 'Prompt constraints' }).closest('section');
    expect(promptList).not.toBeNull();
    fireEvent.click(within(promptList as HTMLElement).getByRole('button', { name: 'Add' }));
    const secondConstraint = within(promptList as HTMLElement).getByLabelText('Prompt constraints 2');
    expect(secondConstraint).toHaveAttribute('type', 'text');
    fireEvent.change(secondConstraint, { target: { value: 'line two' } });
    expect(secondConstraint).toHaveValue('line two');

    const allowEnabled = screen.getByLabelText('Allow tools enabled');
    fireEvent.click(allowEnabled);
    expect(allowEnabled).toBeChecked();
    const allowList = screen.getByRole('heading', { name: 'Allow tools' }).closest('section');
    expect(allowList).not.toBeNull();
    fireEvent.click(within(allowList as HTMLElement).getByRole('button', { name: 'Add' }));
    expect(within(allowList as HTMLElement).getByLabelText('Allow tools 1')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Add tool' }));
    fireEvent.click(screen.getByRole('button', { name: 'Expand Tool 2 parameter rules' }));
    expect(screen.getByLabelText('Parameter rule tool 2')).toBeInTheDocument();
    const addParameterButtons = screen.getAllByRole('button', { name: 'Add parameter' });
    fireEvent.click(addParameterButtons[addParameterButtons.length - 1]);
    expect(screen.getByLabelText('Parameter 2.1.2 name')).toBeInTheDocument();
  });

  it('adds a second rule under one tool and persists both rules', async () => {
    const requests: Array<{ url: string; method: string; body?: string }> = [];
    global.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method || 'GET';
      requests.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined });
      if (url === '/health') return jsonResponse({ status: 'ok', pid: 123 });
      if (url === '/api/rbac-rules' && method === 'GET') return jsonResponse({ rules });
      if (url === '/api/rbac-rules/admin' && method === 'PUT') {
        return jsonResponse({ role: 'admin', rule: JSON.parse(String(init?.body)), restart_required: true });
      }
      throw new Error(`Unhandled request: ${method} ${url}`);
    }) as typeof global.fetch;

    render(<SettingsTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'RBAC Rules' }));
    await screen.findByDisplayValue('Full access');
    fireEvent.click(screen.getByRole('button', { name: 'Expand terminal parameter rules' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }));

    expect(screen.getByRole('heading', { name: /Rule 2/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Parameter 1.2.1 name'), { target: { value: 'command' } });
    const secondRulePattern = screen.getByLabelText('Parameter 1.2.1 regex');
    fireEvent.change(secondRulePattern, { target: { value: '(?<!\\.secret)$' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save role' }));

    await waitFor(() => expect(requests.some((request) => request.url === '/api/rbac-rules/admin' && request.method === 'PUT')).toBe(true));
    const saveRequest = requests.find((request) => request.url === '/api/rbac-rules/admin' && request.method === 'PUT');
    expect(JSON.parse(saveRequest?.body || '{}').tools_paras).toEqual({
      terminal: [
        { command: '^ls' },
        { command: '(?<!\\.secret)$' },
      ],
    });
  });
});
