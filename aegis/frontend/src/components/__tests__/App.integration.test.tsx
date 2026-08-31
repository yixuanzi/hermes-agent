import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../../App';
import type { AuthenticatedUser } from '../../types';

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 1;
  sent: string[] = [];
  url: string;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
    queueMicrotask(() => {
      this.onopen?.();
    });
  }

  send(payload: string) {
    this.sent.push(payload);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  emit(payload: unknown) {
    this.onmessage?.({
      data: JSON.stringify(payload),
    } as MessageEvent<string>);
  }
}

function jsonResponse(payload: unknown, status: number = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const adminUser: AuthenticatedUser = {
  uid: 'admin-uid',
  username: 'admin',
  email: 'admin@aegis.local',
  status: 'enabled',
  create_time: '2026-06-17T00:00:00Z',
  last_login: '2026-06-17T01:00:00Z',
  is_admin: true,
};

const analystUser: AuthenticatedUser = {
  uid: 'analyst-uid',
  username: 'analyst',
  email: 'analyst@example.com',
  status: 'enabled',
  create_time: '2026-06-17T00:00:00Z',
  last_login: '2026-06-17T01:00:00Z',
  is_admin: false,
};

function seedStoredAuth(user: AuthenticatedUser = adminUser) {
  window.localStorage.setItem('aegis_session_token', 'frontend-test-token');
  window.localStorage.setItem('aegis_current_user', JSON.stringify(user));
}

function getChatComposer(): HTMLDivElement {
  return screen.getByRole('combobox', { name: 'Chat message' }) as HTMLDivElement;
}

function setChatComposerText(composer: HTMLDivElement, text: string) {
  composer.textContent = text;
  fireEvent.input(composer);
}

describe('Aegis App integration', () => {
  const originalFetch = global.fetch;
  const originalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState({}, '', '/');
    MockWebSocket.instances = [];
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    cleanup();
    global.fetch = originalFetch;
    globalThis.WebSocket = originalWebSocket;
    vi.unstubAllGlobals();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('leaves login inputs empty by default and surfaces raw API errors via alert', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

    global.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method || 'GET';

      if (url === '/api/auth/login' && method === 'POST') {
        return new Response('{"detail":"用户名或密码错误"}', {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: false });
      }

      throw new Error(`Unhandled request: ${method} ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    const usernameInput = await screen.findByLabelText(/^username$/i);
    const passwordInput = screen.getByLabelText(/^password$/i) as HTMLInputElement;

    expect(usernameInput).toHaveValue('');
    expect(passwordInput).toHaveValue('');

    fireEvent.change(usernameInput, {
      target: { value: 'admin' },
    });
    fireEvent.change(passwordInput, {
      target: { value: 'wrong-password' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^sign in$/i }));

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith('{"detail":"用户名或密码错误"}');
    });
  });

  it('routes each SSO button to its provider start endpoint', async () => {
    const assignSpy = vi.fn();
    const originalWindow = window;
    const mockedWindow = Object.create(originalWindow) as Window;
    Object.defineProperty(mockedWindow, 'location', {
      configurable: true,
      value: {
        assign: assignSpy,
        pathname: '/',
        search: '',
      },
    });
    vi.stubGlobal('window', new Proxy(mockedWindow, {
      get(target, property) {
        const value = property === 'location'
          ? target.location
          : Reflect.get(originalWindow, property, originalWindow);
        return typeof value === 'function' ? value.bind(target) : value;
      },
    }));

    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/system/bootstrap') {
        return jsonResponse({
          embedded_chat: false,
          auth_scheme: 'jwt-password',
          admin_setup_required: false,
          lark_sso_enabled: true,
        });
      }
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: false });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Aegis SSO' }));
    expect(assignSpy).toHaveBeenLastCalledWith('/api/sso/start?sso=1');

    fireEvent.click(screen.getByRole('button', { name: 'Lark SSO' }));
    expect(assignSpy).toHaveBeenLastCalledWith('/api/lark/start');
  });

  it('fails closed and hides Lark SSO when bootstrap configuration is unavailable', async () => {
    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: false });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Lark SSO' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Aegis SSO' })).toBeInTheDocument();
  });

  it('supports register, login, agent/rule CRUD, and admin user management', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(() => true);
    const agents = [
      {
        agent_id: 'seed-agent',
        url: 'http://127.0.0.1:9086/a2a',
        description: 'Seed agent',
        headers: { Authorization: 'Bearer seed' },
        status: 'active',
        extcapabilities: ['seed capability'],
      },
    ];
    const rules = [
      {
        id: 'rule0001',
        name: 'Seed Rule',
        policy: 'Seed policy',
        status: 'active',
      },
    ];
    const users = [adminUser];
    const requests: Array<{ url: string; method: string; auth: string | null; body?: string }> = [];

    global.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method || 'GET';
      const headers = new Headers(init?.headers || {});
      requests.push({
        url,
        method,
        auth: headers.get('Authorization'),
        body: typeof init?.body === 'string' ? init.body : undefined,
      });

      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: false });
      }
      if (url === '/api/overview/agents' && method === 'GET') {
        return jsonResponse({ agents });
      }
      if (url === '/api/auth/register' && method === 'POST') {
        return jsonResponse({ registered: true, status: 'disabled' }, 201);
      }
      if (url === '/api/auth/login' && method === 'POST') {
        return jsonResponse({
          authenticated: true,
          access_token: 'jwt-admin-token',
          token_type: 'bearer',
          expires_in: 28800,
          user: adminUser,
        });
      }
      if (url === '/api/agents' && method === 'GET') {
        return jsonResponse({ agents });
      }
      if (url === '/api/routing/global' && method === 'GET') {
        return jsonResponse({ rules });
      }
      if (url === '/api/users' && method === 'GET') {
        return jsonResponse({ users });
      }
      if (url === '/api/agents/new-agent' && method === 'POST') {
        agents.push({
          agent_id: 'new-agent',
          url: 'http://127.0.0.1:9090/a2a',
          description: 'New agent description',
          headers: { Authorization: 'Bearer abc' },
          status: 'idle',
          extcapabilities: ['cap-a\nwith details', 'cap-b'],
        });
        return jsonResponse(agents[agents.length - 1], 201);
      }
      if (url === '/api/agents/new-agent' && method === 'PUT') {
        const requestBody = JSON.parse(String(init?.body));
        const agentIndex = agents.findIndex((agent) => agent.agent_id === 'new-agent');
        agents[agentIndex] = { ...agents[agentIndex], ...requestBody };
        return jsonResponse(agents[agentIndex]);
      }
      if (url === '/api/routing/global' && method === 'POST') {
        rules.push({
          id: 'rule0002',
          name: 'New Rule',
          policy: 'Route suspicious email to email-sec',
          status: 'inactive',
        });
        return jsonResponse(rules[rules.length - 1], 201);
      }
      if (url === '/api/users' && method === 'POST') {
        users.push({
          uid: 'alice-uid',
          username: 'alice',
          email: 'alice@example.com',
          status: 'enabled',
          create_time: '2026-06-17T02:00:00Z',
          last_login: null,
          is_admin: false,
        });
        return jsonResponse(users[users.length - 1], 201);
      }
      if (url === '/api/users/alice-uid/status' && method === 'PUT') {
        users[1] = { ...users[1], status: 'disabled' };
        return jsonResponse(users[1]);
      }
      if (url === '/api/users/alice-uid/password' && method === 'PUT') {
        return jsonResponse({ updated: true, uid: 'alice-uid' });
      }
      if (url === '/api/users/alice-uid' && method === 'DELETE') {
        users.splice(1, 1);
        return jsonResponse({ deleted: true, uid: 'alice-uid' });
      }

      throw new Error(`Unhandled request: ${method} ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /create account/i }));
    expect(await screen.findByRole('heading', { name: /register/i })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/register username/i), {
      target: { value: 'pending-user' },
    });
    fireEvent.change(screen.getByLabelText(/register password/i), {
      target: { value: 'Password123!' },
    });
    fireEvent.change(screen.getByLabelText(/register email/i), {
      target: { value: 'pending@example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^register$/i }));
    expect(await screen.findByText(/registration submitted/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/^username$/i), {
      target: { value: 'admin' },
    });
    fireEvent.change(screen.getByLabelText(/^password$/i), {
      target: { value: 'admin123456' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^sign in$/i }));

    await screen.findByRole('button', { name: /agent orchestration/i });
    expect(window.location.pathname).toBe('/overview');
    expect(screen.getByText('Administrator')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /agent orchestration/i }));
    fireEvent.click(await screen.findByRole('button', { name: /注册智能体/i }));

    fireEvent.change(screen.getByPlaceholderText(/e\.g\. threat-intel/i), {
      target: { value: 'new-agent' },
    });
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/127\.0\.0\.1:9086\/a2a/i), {
      target: { value: 'http://127.0.0.1:9090/a2a' },
    });
    fireEvent.change(screen.getByPlaceholderText(/authorization/i), {
      target: { value: 'Authorization' },
    });
    fireEvent.change(screen.getByPlaceholderText(/bearer token/i), {
      target: { value: 'Bearer abc' },
    });
    fireEvent.change(screen.getByPlaceholderText(/审计服务器特权指令偏差/i), {
      target: { value: 'New agent description' },
    });
    fireEvent.click(screen.getByRole('button', { name: /添加能力描述/i }));
    fireEvent.click(screen.getByRole('button', { name: /添加能力描述/i }));
    const capabilityInputs = screen.getAllByRole('textbox', { name: /能力描述/i });
    fireEvent.change(capabilityInputs[0], {
      target: { value: 'cap-a\nwith details' },
    });
    fireEvent.change(capabilityInputs[1], {
      target: { value: 'cap-b' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await screen.findByText('new-agent');
    expect(JSON.parse(requests.find(({ url, method }) => url === '/api/agents/new-agent' && method === 'POST')?.body || '{}'))
      .toMatchObject({ extcapabilities: ['cap-a\nwith details', 'cap-b'] });

    const newAgentRow = screen.getByText('new-agent').closest('tr');
    expect(newAgentRow).not.toBeNull();
    fireEvent.click(within(newAgentRow as HTMLTableRowElement).getByTitle('Edit Agent details'));
    expect(screen.getAllByRole('textbox', { name: /能力描述/i })).toHaveLength(2);
    expect(screen.getByRole('textbox', { name: '能力描述 1' })).toHaveValue('cap-a\nwith details');
    fireEvent.click(screen.getByRole('button', { name: '删除能力描述 1' }));
    fireEvent.click(screen.getByRole('button', { name: '删除能力描述 1' }));
    fireEvent.click(screen.getByRole('button', { name: /添加能力描述/i }));
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => {
      const updateRequest = requests.find(({ url, method }) => url === '/api/agents/new-agent' && method === 'PUT');
      expect(updateRequest).toBeDefined();
      expect(JSON.parse(updateRequest?.body || '{}')).toMatchObject({ extcapabilities: [] });
    });

    fireEvent.click(screen.getByRole('button', { name: /routing policy/i }));
    fireEvent.click(await screen.findByRole('button', { name: /新建路由规则/i }));
    fireEvent.change(screen.getByPlaceholderText(/钓鱼邮件重设优先级/i), {
      target: { value: 'New Rule' },
    });
    fireEvent.change(screen.getByPlaceholderText(/统一分流、过滤、兜底处置/i), {
      target: { value: 'Route suspicious email to email-sec' },
    });
    fireEvent.change(screen.getByDisplayValue(/enabled/i), {
      target: { value: 'Disabled' },
    });
    fireEvent.click(screen.getByRole('button', { name: /保存规则/i }));
    await screen.findByText('New Rule');

    fireEvent.click(screen.getByRole('button', { name: /user management/i }));
    expect(await screen.findByRole('heading', { name: /user management/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /user directory/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /user directory/i }).closest('header')).toHaveClass('aegis-page-content__header--compact');
    fireEvent.click(await screen.findByRole('button', { name: /新增用户/i }));
    fireEvent.change(screen.getByLabelText(/create username/i), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByLabelText(/create password/i), {
      target: { value: 'Password123!' },
    });
    fireEvent.change(screen.getByLabelText(/create email/i), {
      target: { value: 'alice@example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create user/i }));
    await screen.findByText('alice@example.com');

    const usernameColumn = screen.getByRole('columnheader', { name: /username & id/i });
    expect(usernameColumn).toHaveClass('p-3');
    expect(usernameColumn.closest('table')).toHaveClass('border-b');
    expect(screen.getByText('alice@example.com').closest('td')).toHaveClass('p-3');
    expect(screen.getByRole('button', { name: /disable alice/i })).toHaveClass('aegis-btn--icon');
    expect(screen.getByRole('button', { name: /open password reset for alice/i })).toHaveClass('aegis-btn--icon');
    expect(screen.getByRole('button', { name: /delete alice/i })).toHaveClass('aegis-btn--icon');

    fireEvent.click(screen.getByRole('button', { name: /disable alice/i }));
    await screen.findByText('disabled');
    expect(confirmSpy).toHaveBeenCalledWith('Disable alice? (确定要disabled该用户吗？)');

    fireEvent.click(screen.getByRole('button', { name: /open password reset for alice/i }));
    fireEvent.change(screen.getByLabelText(/new password for alice/i), {
      target: { value: 'NewPassword123!' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save password/i }));

    fireEvent.click(screen.getByRole('button', { name: /delete alice/i }));
    await waitFor(() => {
      expect(screen.queryByText('alice@example.com')).not.toBeInTheDocument();
    });
    expect(confirmSpy).toHaveBeenCalledWith('Delete alice? (确定删除该用户吗？此操作不可逆)');

    await waitFor(() => {
      expect(requests.some((request) => request.url === '/api/agents/new-agent' && request.auth === 'Bearer jwt-admin-token')).toBe(true);
      expect(requests.some((request) => request.url === '/api/routing/global' && request.method === 'POST' && request.auth === 'Bearer jwt-admin-token')).toBe(true);
      expect(requests.some((request) => request.url === '/api/users' && request.method === 'POST' && request.auth === 'Bearer jwt-admin-token')).toBe(true);
    });
  });

  it('supports self password change from the user menu', async () => {
    seedStoredAuth();

    global.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method || 'GET';
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: adminUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/routing/global') {
        return jsonResponse({ rules: [] });
      }
      if (url === '/api/users') {
        return jsonResponse({ users: [adminUser] });
      }
      if (url === '/api/auth/password' && method === 'PUT') {
        return jsonResponse({ updated: true });
      }
      throw new Error(`Unhandled request: ${method} ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    await screen.findByRole('button', { name: /user menu/i });
    fireEvent.click(screen.getByRole('button', { name: /user menu/i }));
    expect(screen.getByText('admin-uid')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /change password/i }));
    fireEvent.change(screen.getByLabelText(/current password/i), {
      target: { value: 'admin123456' },
    });
    fireEvent.change(screen.getByLabelText(/^new password$/i), {
      target: { value: 'AdminPassword123!' },
    });
    fireEvent.click(screen.getByRole('button', { name: /update password/i }));

    await waitFor(() => {
      expect(screen.queryByText(/change password/i)).not.toBeInTheDocument();
    });
  });

  it('hides admin-only control pages from regular users and redirects direct access attempts', async () => {
    seedStoredAuth(analystUser);
    window.history.replaceState({}, '', '/orchestration');
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: analystUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents') {
        return jsonResponse({
          agents: [
            {
              agent_id: 'analyst-visible-agent',
              url: 'http://127.0.0.1:9086/a2a',
              description: 'Visible to overview',
              status: 'active',
              extcapabilities: ['query-domain'],
            },
          ],
        });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    await screen.findByRole('button', { name: /overview/i });
    expect(window.location.pathname).toBe('/overview');
    expect(alertSpy).toHaveBeenCalledWith('Admin access required.');
    expect(screen.getAllByText('Visible to overview').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /agent orchestration/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /routing policy/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /user management/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /audit logs/i })).not.toBeInTheDocument();
    expect(screen.queryByText('CONTROL')).not.toBeInTheDocument();
  });

  it('lets administrators open the Audit Logs route', async () => {
    seedStoredAuth(adminUser);
    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/auth/session') return jsonResponse({ authenticated: true, user: adminUser, expires_in: 28800 });
      if (url === '/api/overview/agents' || url === '/api/agents') return jsonResponse({ agents: [] });
      if (url === '/api/routing/global') return jsonResponse({ rules: [] });
      if (url === '/api/users') return jsonResponse({ users: [adminUser] });
      if (url.startsWith('/api/audit/a2a-delegates?')) return jsonResponse({ logs: [], total: 0, page: 1, page_size: 50 });
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /audit logs/i }));
    expect(window.location.pathname).toBe('/audit');
    expect(await screen.findByRole('heading', { name: /audit logs/i })).toBeInTheDocument();
  });

  it('lets administrators navigate to System Settings from the sidebar and header control', async () => {
    seedStoredAuth(adminUser);

    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: adminUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents' || url === '/api/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/routing/global') {
        return jsonResponse({ rules: [] });
      }
      if (url === '/api/users') {
        return jsonResponse({ users: [adminUser] });
      }
      if (url === '/health') {
        return jsonResponse({ status: 'ok', pid: 123 });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    fireEvent.click(await screen.findByRole('button', { name: /system settings/i }));
    expect(window.location.pathname).toBe('/settings');
    expect(await screen.findByRole('heading', { name: /system settings/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /overview/i }));
    fireEvent.click(screen.getByTitle(/configure platform settings/i));
    expect(window.location.pathname).toBe('/settings');
    expect(await screen.findByRole('heading', { name: /system settings/i })).toBeInTheDocument();
  });

  it('returns to login when the Settings restart request reports an expired session', async () => {
    seedStoredAuth(adminUser);

    global.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method || 'GET';
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: adminUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents' || url === '/api/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/routing/global') {
        return jsonResponse({ rules: [] });
      }
      if (url === '/api/users') {
        return jsonResponse({ users: [adminUser] });
      }
      if (url === '/health') {
        return jsonResponse({ status: 'ok', pid: 123 });
      }
      if (url === '/api/system/restart' && method === 'POST') {
        return jsonResponse({ detail: 'Token expired' }, 401);
      }
      throw new Error(`Unhandled request: ${method} ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    fireEvent.click(await screen.findByRole('button', { name: /system settings/i }));
    fireEvent.click(screen.getByRole('button', { name: /restart aegis/i }));
    fireEvent.click(screen.getByRole('button', { name: /continue/i }));
    fireEvent.change(screen.getByLabelText(/restart confirmation phrase/i), {
      target: { value: 'RESTART AEGIS' },
    });
    fireEvent.click(screen.getByRole('button', { name: /confirm restart/i }));

    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
    expect(window.localStorage.getItem('aegis_session_token')).toBeNull();
    expect(screen.queryByText(/restart request failed/i)).not.toBeInTheDocument();
  });

  it('hides every settings control from non-admin users and redirects direct settings access', async () => {
    seedStoredAuth(analystUser);
    window.history.replaceState({}, '', '/overview');
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    const requests: string[] = [];

    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      requests.push(url);
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: analystUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents') {
        return jsonResponse({ agents: [] });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    await screen.findByRole('button', { name: /overview/i });
    const themeButton = screen.getByRole('button', { name: /choose color theme/i });
    expect(screen.getByText('UTC:').parentElement?.nextElementSibling).toContainElement(themeButton);
    fireEvent.click(themeButton);

    const dialog = screen.getByRole('dialog', { name: /choose your operating environment/i });
    const radios = within(dialog).getAllByRole('radio');
    expect(radios).toHaveLength(3);
    expect(radios[2]).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(within(dialog).getByRole('radio', { name: /daylight signal/i }));
    await waitFor(() => {
      expect(document.documentElement).toHaveAttribute('data-aegis-theme', 'daylight-signal');
      expect(window.localStorage.getItem('aegis_theme')).toBe('daylight-signal');
    });
    fireEvent.keyDown(within(dialog).getByRole('radio', { name: /daylight signal/i }), { key: 'ArrowRight' });
    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-aegis-theme', 'aegis-night'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: /choose your operating environment/i })).not.toBeInTheDocument();
    expect(themeButton).toHaveFocus();

    requests.length = 0;
    window.history.replaceState({}, '', '/settings');
    fireEvent(window, new PopStateEvent('popstate'));
    await waitFor(() => {
      expect(window.location.pathname).toBe('/overview');
    });
    expect(alertSpy).toHaveBeenCalledWith('Admin access required.');
    expect(screen.queryByText(/system settings/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /system settings/i })).not.toBeInTheDocument();
    expect(screen.queryByTitle(/configure platform settings/i)).not.toBeInTheDocument();
    expect(requests).not.toContain('/health');
  });

  it('keeps session sockets alive when switching chat sessions and preserves parallel task streams', async () => {
    seedStoredAuth(analystUser);
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);

    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: analystUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/routing/global') {
        return jsonResponse({ rules: [] });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    fireEvent.click(await screen.findByRole('button', { name: /aegis chat/i }));
    setChatComposerText(await screen.findByRole('combobox', { name: 'Chat message' }) as HTMLDivElement, 'session-one');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => {
      expect(MockWebSocket.instances).toHaveLength(1);
      expect(MockWebSocket.instances[0].url).toContain('frontend-test-token');
    });
    const firstSocket = MockWebSocket.instances[0];
    await waitFor(() => {
      expect(firstSocket.sent.length).toBeGreaterThan(0);
    });
    firstSocket.emit({
      type: 'session.bound',
      session_id: 'sess-1',
      title: 'session-one',
      resumed: false,
    });
    await waitFor(() => {
      expect(firstSocket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true);
    });

    fireEvent.click(screen.getByRole('button', { name: /新建/i }));
    setChatComposerText(getChatComposer(), 'session-two');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => {
      expect(MockWebSocket.instances).toHaveLength(2);
    });
    const secondSocket = MockWebSocket.instances[1];
    secondSocket.emit({
      type: 'session.bound',
      session_id: 'sess-2',
      title: 'session-two',
      resumed: false,
    });
    await waitFor(() => {
      expect(secondSocket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true);
    });

    firstSocket.emit({
      type: 'message.completed',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      message_id: 'assistant-1',
      source: 'main',
      content: 'background response from session one',
      completed: true,
    });
    secondSocket.emit({
      type: 'message.completed',
      session_id: 'sess-2',
      turn_id: 'turn-2',
      message_id: 'assistant-2',
      source: 'main',
      content: 'foreground response from session two',
      completed: true,
    });

    expect(await screen.findByText('foreground response from session two')).toBeInTheDocument();
    fireEvent.click(screen.getByText('session-one'));
    expect(await screen.findByText('background response from session one')).toBeInTheDocument();
  });

  it('keeps background chat state updating across app navigation and surfaces attention in the sidebar', async () => {
    seedStoredAuth(analystUser);
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);

    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: analystUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/routing/global') {
        return jsonResponse({ rules: [] });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    fireEvent.click(await screen.findByRole('button', { name: /aegis chat/i }));
    setChatComposerText(await screen.findByRole('combobox', { name: 'Chat message' }) as HTMLDivElement, 'background-session');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => {
      expect(MockWebSocket.instances).toHaveLength(1);
    });
    const socket = MockWebSocket.instances[0];
    socket.emit({
      type: 'session.bound',
      session_id: 'sess-background',
      title: 'background-session',
      resumed: false,
    });
    await waitFor(() => {
      expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true);
    });

    fireEvent.click(screen.getByRole('button', { name: /overview/i }));

    socket.emit({
      type: 'approval.request',
      session_id: 'sess-background',
      approval_id: 'approval-1',
      command: 'sudo rm -rf /tmp/demo',
      description: 'dangerous command',
      choices: ['once', 'session', 'always', 'deny'],
      source: 'main',
    });
    socket.emit({
      type: 'message.completed',
      session_id: 'sess-background',
      turn_id: 'turn-1',
      message_id: 'assistant-background',
      source: 'main',
      content: 'background session kept streaming',
      completed: true,
    });

    expect(await screen.findByText('1')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /aegis chat/i }));
    expect(await screen.findByText('background session kept streaming')).toBeInTheDocument();
    expect(await screen.findByText(/approval required/i)).toBeInTheDocument();
  });

  it('keeps app shell and tab content selectable by default', async () => {
    seedStoredAuth(adminUser);

    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: adminUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/routing/global') {
        return jsonResponse({ rules: [] });
      }
      if (url === '/api/users') {
        return jsonResponse({ users: [adminUser] });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    await screen.findByRole('button', { name: /aegis chat/i });

    const mainViewport = document.getElementById('main-content-viewport');
    const appShell = mainViewport?.parentElement?.parentElement as HTMLElement | null;
    const sidebar = document.getElementById('sidebar-container');

    expect(appShell).toBeTruthy();
    expect(sidebar).toBeTruthy();
    expect(mainViewport?.firstElementChild).toBeTruthy();
    expect(appShell).not.toHaveClass('select-none');
    expect(sidebar).not.toHaveClass('select-none');
    expect(mainViewport?.firstElementChild).not.toHaveClass('select-none');

    fireEvent.click(screen.getByRole('button', { name: /agent orchestration/i }));
    expect(mainViewport?.firstElementChild).not.toHaveClass('select-none');

    fireEvent.click(screen.getByRole('button', { name: /routing policy/i }));
    expect(mainViewport?.firstElementChild).not.toHaveClass('select-none');

    fireEvent.click(screen.getByRole('button', { name: /aegis chat/i }));
    expect(mainViewport?.firstElementChild).not.toHaveClass('select-none');
  });

  it('shares the App Entry directory between Overview and App Entry and refreshes it explicitly', async () => {
    seedStoredAuth(analystUser);
    window.history.replaceState({}, '', '/chat');
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    let appEntryCalls = 0;

    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: analystUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/overview/stats') {
        return jsonResponse({
          window_start: '2026-07-15T00:00:00.000000Z',
          window_end: '2026-07-22T00:00:00.000000Z',
          executing_agent_count: 0,
          source_platform_count: 0,
          active_user_count: 0,
          delegation_total: 0,
          success_count: 0,
          success_rate: null,
          status_counts: { succ: 0, fail: 0, auth_denied: 0 },
          comparison: {
            previous_delegation_total: 0,
            delegation_volume_change_percent: null,
            previous_success_rate: null,
            success_rate_change_percentage_points: null,
          },
        });
      }
      if (url === '/api/overview/topology') {
        return jsonResponse({
          schema_version: '1.0.0',
          updated_at: '2026-07-22',
          center: { id: 'aegis', symbol: 'aegis-connection' },
          agents: [{
            id: 'ai-soc',
            layer: 'agent_ring',
            business_domain: 'AI-SOC',
            product_service_code: 'WORKAGENT-AI-SOC',
            name: 'Argus',
            display_name: 'AI-SOC · Argus',
            marketing_name: '[ Argus ]',
            symbol: 'argus-eyes',
            cultural_origin: '希腊神话·百眼巨人',
            business_fit: '全天候、零死角威胁监控与感知',
            role: '安全运营',
            layout: { ring_position: 0, angle_degrees: 270, radius: 'agent' },
            star_nodes: [],
            runtime: { status: 'active', source: 'a2a_registry' },
          }],
          edges: [{ source: 'aegis', target: 'ai-soc', mode: 'orchestrates' }],
        });
      }
      if (url === '/api/app-entries') {
        appEntryCalls += 1;
        return jsonResponse({
          organization_code: 'XINGHAI-SH',
          entries: [{
            subscription_id: 'subscription-1',
            subscription_no: 'SUB-001',
            subscription_status: 'active',
            effective_to: '2027-08-01T00:00:00Z',
            product_service_code: 'WORKAGENT-AI-SOC',
            product_service_name: 'Argus Work Agent',
            entry_url: 'https://workagent.example.com',
            entry_source: 'app',
          }],
        });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    await screen.findByRole('button', { name: /aegis chat/i });
    expect(appEntryCalls).toBe(0);

    fireEvent.click(screen.getByRole('button', { name: /overview/i }));
    await waitFor(() => expect(appEntryCalls).toBe(1));
    fireEvent.click(await screen.findByLabelText(/AI-SOC · Argus/));
    expect(openSpy).toHaveBeenCalledWith('https://workagent.example.com', '_blank', 'noopener,noreferrer');
    expect(appEntryCalls).toBe(1);

    fireEvent.click(screen.getByRole('button', { name: /app entry/i }));
    expect(await screen.findByRole('heading', { name: 'App Entry' })).toBeInTheDocument();
    expect(screen.getByText('Argus Work Agent')).toBeInTheDocument();
    expect(appEntryCalls).toBe(1);

    fireEvent.click(screen.getByRole('button', { name: /refresh entries/i }));
    await waitFor(() => expect(appEntryCalls).toBe(2));
  });

  it('refreshes Overview delegation stats every 60 seconds', async () => {
    vi.useFakeTimers();
    seedStoredAuth(analystUser);
    let statsCalls = 0;

    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: analystUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/overview/stats') {
        statsCalls += 1;
        return jsonResponse({
          window_start: '2026-07-15T00:00:00.000000Z',
          window_end: '2026-07-22T00:00:00.000000Z',
          executing_agent_count: 1,
          source_platform_count: 1,
          active_user_count: 1,
          delegation_total: statsCalls,
          success_count: statsCalls,
          success_rate: 1,
          status_counts: { succ: statsCalls, fail: 0, auth_denied: 0 },
          comparison: {
            previous_delegation_total: 0,
            delegation_volume_change_percent: null,
            previous_success_rate: null,
            success_rate_change_percentage_points: null,
          },
        });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(statsCalls).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(statsCalls).toBe(2);
  });

  it('does not refresh Overview delegation stats while another page is active', async () => {
    vi.useFakeTimers();
    seedStoredAuth(analystUser);
    window.history.replaceState({}, '', '/app-entry');
    let statsCalls = 0;

    global.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/auth/session') {
        return jsonResponse({ authenticated: true, user: analystUser, expires_in: 28800 });
      }
      if (url === '/api/overview/agents') {
        return jsonResponse({ agents: [] });
      }
      if (url === '/api/overview/stats') {
        statsCalls += 1;
        return jsonResponse({
          window_start: '2026-07-15T00:00:00.000000Z',
          window_end: '2026-07-22T00:00:00.000000Z',
          executing_agent_count: 0,
          source_platform_count: 0,
          active_user_count: 0,
          delegation_total: statsCalls,
          success_count: statsCalls,
          success_rate: 1,
          status_counts: { succ: statsCalls, fail: 0, auth_denied: 0 },
          comparison: {
            previous_delegation_total: 0,
            delegation_volume_change_percent: null,
            previous_success_rate: null,
            success_rate_change_percentage_points: null,
          },
        });
      }
      if (url === '/api/overview/topology') {
        return new Response('topology unavailable', { status: 503 });
      }
      if (url === '/api/app-entries') {
        return jsonResponse({ organization_code: 'XINGHAI-SH', entries: [] });
      }
      throw new Error(`Unhandled request: GET ${url}`);
    }) as typeof global.fetch;

    render(<App />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(statsCalls).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(statsCalls).toBe(1);
  });
});
