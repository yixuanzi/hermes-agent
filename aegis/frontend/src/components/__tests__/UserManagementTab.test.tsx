import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import UserManagementTab from '../UserManagementTab';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const user = {
  uid: 'user-1',
  username: 'alice',
  email: 'alice@example.com',
  status: 'enabled' as const,
  create_time: '2026-01-01T00:00:00Z',
  last_login: null,
  role: 'user' as const,
  is_admin: false,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('UserManagementTab', () => {
  it('creates users with a selected role and updates existing user roles', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const onUpdateRole = vi.fn().mockResolvedValue(undefined);

    render(
      <UserManagementTab
        busy={false}
        users={[user]}
        onCreate={onCreate}
        onDelete={vi.fn()}
        onRefresh={vi.fn()}
        onResetPassword={vi.fn()}
        onUpdateStatus={vi.fn()}
        onUpdateRole={onUpdateRole}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '新增用户' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Create Role' }), { target: { value: 'operator' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Create User' }).closest('form') as HTMLFormElement);
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ role: 'operator' })));

    fireEvent.change(screen.getByRole('combobox', { name: 'Role for alice' }), { target: { value: 'admin' } });
    await waitFor(() => expect(onUpdateRole).toHaveBeenCalledWith('user-1', 'admin'));
  });

  it('provides accessible user and role tabs with role CRUD interaction', async () => {
    const requests: Array<{ url: string; method: string; body?: unknown }> = [];
    let roles: Array<Record<string, string>> = [{
      id: 'role-1', platform: 'aegis', uid: 'u-1', uname: 'Role User', role: 'user', update_time: '2026-01-01T00:00:00Z',
    }];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method || 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      requests.push({ url, method, body });
      if (url === '/api/user-roles' && method === 'GET') return jsonResponse({ roles });
      if (url === '/api/user-roles' && method === 'POST') {
        const created = { id: 'role-2', ...body, update_time: '2026-01-02T00:00:00Z' };
        roles = [...roles, created];
        return jsonResponse(created, 201);
      }
      if (url === '/api/user-roles/role-1' && method === 'PUT') {
        const updated = { ...roles[0], ...body, update_time: '2026-01-03T00:00:00Z' };
        roles = [updated, ...roles.slice(1)];
        return jsonResponse(updated);
      }
      if (url === '/api/user-roles/role-2' && method === 'DELETE') {
        roles = roles.filter((role) => role.id !== 'role-2');
        return jsonResponse({ deleted: true, id: 'role-2' });
      }
      throw new Error(`Unhandled request: ${method} ${url}`);
    }));
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <UserManagementTab
        busy={false}
        users={[user]}
        onCreate={vi.fn()}
        onDelete={vi.fn()}
        onRefresh={vi.fn()}
        onResetPassword={vi.fn()}
        onUpdateStatus={vi.fn()}
        onUpdateRole={vi.fn()}
      />,
    );

    const tablist = screen.getByRole('tablist');
    const userTab = within(tablist).getByRole('tab', { name: '用户管理' });
    const roleTab = within(tablist).getByRole('tab', { name: 'rbac guard 角色管理' });
    expect(userTab).toHaveAttribute('aria-selected', 'true');
    expect(userTab).toHaveAttribute('aria-controls', 'user-management-panel');
    expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'user-management-panel');

    fireEvent.click(roleTab);
    expect(roleTab).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByText('Role User')).toBeInTheDocument();
    expect(requests).toContainEqual({ url: '/api/user-roles', method: 'GET', body: undefined });

    fireEvent.click(screen.getByRole('button', { name: /新增角色/i }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Role Platform' }), { target: { value: 'slack' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Role UID' }), { target: { value: 'u-2' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Role Username' }), { target: { value: 'Second User' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Role' }), { target: { value: 'operator' } });
    fireEvent.click(screen.getByRole('button', { name: /保存角色/i }));
    await waitFor(() => expect(requests).toContainEqual({
      url: '/api/user-roles',
      method: 'POST',
      body: { platform: 'slack', uid: 'u-2', uname: 'Second User', role: 'operator' },
    }));
    expect(await screen.findByText('Second User')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Edit role for Role User' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Role Username' }), { target: { value: 'Updated User' } });
    fireEvent.click(screen.getByRole('button', { name: /保存角色/i }));
    await waitFor(() => expect(requests).toContainEqual({
      url: '/api/user-roles/role-1',
      method: 'PUT',
      body: { platform: 'aegis', uid: 'u-1', uname: 'Updated User', role: 'user' },
    }));

    fireEvent.click(screen.getByRole('button', { name: 'Delete role for Second User' }));
    await waitFor(() => expect(screen.queryByText('Second User')).not.toBeInTheDocument());
    expect(requests).toContainEqual({ url: '/api/user-roles/role-2', method: 'DELETE', body: undefined });
  });
});
