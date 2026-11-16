import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AuditLogsTab from '../AuditLogsTab';

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const log = {
  id: 'audit-1', timestamp: '2026-07-21T03:00:00Z', platform: 'aegis',
  user_id: 'u-1', user_name: 'Alice', agent_name: 'responder',
  goal: 'A long phishing investigation goal that can be expanded for review',
  session_id: '', is_loop: true, is_delegate_output: false, status: 'auth_denied',
};

describe('AuditLogsTab', () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    cleanup();
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('keeps every filter control the same height and bottom-aligned', () => {
    global.fetch = vi.fn(() => new Promise<Response>(() => {})) as typeof global.fetch;

    render(<AuditLogsTab />);

    expect(screen.getByRole('heading', { name: 'Audit Logs' })).toBeInTheDocument();
    const delegateEvidence = screen.getByRole('heading', { name: 'Delegate Evidence' });
    expect(delegateEvidence).toBeInTheDocument();
    expect(delegateEvidence.closest('header')).toHaveClass('aegis-page-content__header--compact');
    const auditId = screen.getByLabelText('Audit Audit ID');
    const timestampFrom = screen.getByLabelText('Audit Timestamp From');
    const timestampTo = screen.getByLabelText('Audit Timestamp To');
    const form = auditId.closest('form');

    expect(form).not.toBeNull();
    form?.querySelectorAll('input, select').forEach((control) => {
      expect(control).toHaveClass('h-10');
    });
    expect(form).toHaveClass('items-end');
    expect(timestampFrom.closest('label')).toHaveClass('flex', 'flex-col', 'justify-end');
    expect(timestampTo.closest('label')).toHaveClass('flex', 'flex-col', 'justify-end');
  });

  it('filters, pages, resets, refreshes, and expands goals', async () => {
    const urls: string[] = [];
    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      urls.push(url);
      const page = new URL(url, 'http://aegis.local').searchParams.get('page');
      return jsonResponse({ logs: page === '2' ? [] : [log], total: 51, page: Number(page), page_size: 50 });
    }) as typeof global.fetch;

    render(<AuditLogsTab />);
    const goal = await screen.findByRole('button', { name: /long phishing investigation/i });
    expect(goal).toHaveClass('line-clamp-2');
    expect(goal).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(goal);
    expect(goal).not.toHaveClass('line-clamp-2');
    expect(goal).toHaveAttribute('aria-expanded', 'true');
    expect(goal).toHaveClass('aegis-btn--selected');

    fireEvent.change(screen.getByLabelText(/audit user id/i), { target: { value: 'u-1' } });
    fireEvent.change(screen.getByLabelText(/audit status/i), { target: { value: 'auth_denied' } });
    fireEvent.change(screen.getByLabelText(/audit loop/i), { target: { value: 'true' } });
    fireEvent.click(screen.getByRole('button', { name: /^search$/i }));
    await waitFor(() => expect(urls.some((url) => url.includes('user_id=u-1') && url.includes('status=auth_denied') && url.includes('is_loop=true'))).toBe(true));

    fireEvent.click(screen.getByLabelText(/next audit page/i));
    await waitFor(() => expect(urls.some((url) => url.includes('page=2'))).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: /reset/i }));
    await waitFor(() => expect(screen.getByLabelText(/audit user id/i)).toHaveValue(''));
    fireEvent.click(screen.getByRole('button', { name: /^refresh$/i }));
    expect(urls.length).toBeGreaterThanOrEqual(5);
  });

  it('ignores an older response that finishes after a filtered search', async () => {
    let resolveInitial: ((response: Response) => void) | undefined;
    const initialResponse = new Promise<Response>((resolve) => { resolveInitial = resolve; });
    let resolveFiltered: ((response: Response) => void) | undefined;
    const filteredResponse = new Promise<Response>((resolve) => { resolveFiltered = resolve; });
    let requestCount = 0;
    global.fetch = vi.fn(async () => {
      requestCount += 1;
      if (requestCount === 1) return initialResponse;
      return filteredResponse;
    }) as typeof global.fetch;

    render(<AuditLogsTab />);
    expect(screen.getByRole('button', { name: /^refresh$/i })).toHaveAttribute('aria-busy', 'true');
    fireEvent.change(screen.getByLabelText(/audit user id/i), { target: { value: 'u-1' } });
    fireEvent.click(screen.getByRole('button', { name: /^search$/i }));
    expect(screen.getByRole('button', { name: /^refresh$/i })).toHaveAttribute('aria-busy', 'false');
    expect(screen.getByRole('button', { name: /^search$/i })).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: /^search$/i })).toHaveClass('aegis-btn--busy');

    resolveFiltered?.(jsonResponse({ logs: [{ ...log, id: 'filtered', goal: 'Filtered evidence' }], total: 1, page: 1, page_size: 50 }));
    expect(await screen.findByText('Filtered evidence')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^search$/i })).toHaveAttribute('aria-busy', 'false');

    resolveInitial?.(jsonResponse({ logs: [{ ...log, id: 'stale', goal: 'Stale evidence' }], total: 1, page: 1, page_size: 50 }));
    await waitFor(() => expect(screen.queryByText('Stale evidence')).not.toBeInTheDocument());
    expect(screen.getByText('Filtered evidence')).toBeInTheDocument();
  });

  it('switches to task audit and searches task fields', async () => {
    const urls: string[] = [];
    const taskLog = {
      id: 'task-1',
      uid: 'u-1',
      uname: 'Alice',
      session_id: 'session-1',
      prompt: 'Investigate phishing',
      create_time: '2026-07-21T01:00:00.000000Z',
    };
    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      urls.push(url);
      if (url.startsWith('/api/audit/tasks?')) {
        return jsonResponse({ logs: [taskLog], total: 1, page: 1, page_size: 50 });
      }
      return jsonResponse({ logs: [], total: 0, page: 1, page_size: 50 });
    }) as typeof global.fetch;

    render(<AuditLogsTab />);
    const delegateTab = screen.getByRole('tab', { name: 'Delegate audit' });
    const taskTab = screen.getByRole('tab', { name: 'Task audit' });
    expect(delegateTab).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(taskTab);

    expect(taskTab).toHaveAttribute('aria-selected', 'true');
    expect(delegateTab).toHaveAttribute('aria-selected', 'false');
    expect(await screen.findByRole('heading', { name: 'Task Evidence' })).toBeInTheDocument();
    expect(urls.some((url) => url.startsWith('/api/audit/tasks?'))).toBe(true);
    const taskForm = screen.getByLabelText('Task UID').closest('form');
    expect(taskForm).toHaveClass('xl:grid-cols-7');
    expect([...taskForm!.querySelectorAll('input')].map((input) => input.getAttribute('aria-label'))).toEqual([
      'Task UID', 'Task User Name', 'Task Session ID', 'Task Prompt',
      'Task Created From UTC', 'Task Created To UTC',
    ]);
    expect(screen.queryByLabelText('Task ID')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Task UID'), { target: { value: 'u-1' } });
    fireEvent.change(screen.getByLabelText('Task User Name'), { target: { value: 'ali' } });
    fireEvent.change(screen.getByLabelText('Task Prompt'), { target: { value: 'phishing' } });
    fireEvent.change(screen.getByLabelText('Task Created From UTC'), { target: { value: '2026-07-21T00:30' } });
    const searchButton = screen.getByRole('button', { name: 'Search' });
    const resetButton = screen.getByRole('button', { name: 'Reset' });
    expect(searchButton).toHaveClass('aegis-btn--icon', 'w-10');
    expect(resetButton).toHaveClass('aegis-btn--icon', 'w-10');
    expect(searchButton).toHaveAttribute('title', 'Search');
    expect(resetButton).toHaveAttribute('title', 'Reset');
    fireEvent.click(searchButton);

    await waitFor(() => expect(urls.some((url) => {
      const params = new URL(url, 'http://aegis.local').searchParams;
      return params.get('uid') === 'u-1'
        && params.get('uname') === 'ali'
        && params.get('prompt') === 'phishing'
        && params.get('create_time_from') === '2026-07-21T00:30:00Z';
    })).toBe(true));

    const prompt = await screen.findByRole('button', { name: 'Investigate phishing' });
    expect(prompt).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(prompt);
    expect(prompt).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('task-1')).toBeInTheDocument();
    expect(screen.getByText('session-1')).toBeInTheDocument();
    expect(screen.getByText('2026-07-21T01:00:00.000000Z')).toBeInTheDocument();
  });
});
