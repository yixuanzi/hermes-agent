import { Fragment, FormEvent, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { KeyRound, Power, PowerOff, Trash2 } from 'lucide-react';
import { alertApiError } from '../lib/api';
import { AuthenticatedUser, UserDraft, UserRoleName } from '../types';
import UserRoleManagementTab from './UserRoleManagementTab';

interface UserManagementTabProps {
  busy: boolean;
  users: AuthenticatedUser[];
  onCreate: (draft: UserDraft) => Promise<void>;
  onDelete: (uid: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  onResetPassword: (uid: string, password: string) => Promise<void>;
  onUpdateStatus: (uid: string, status: 'enabled' | 'disabled') => Promise<void>;
  onUpdateRole: (uid: string, role: UserRoleName) => Promise<void>;
  onAuthExpired?: () => void;
}

const EMPTY_DRAFT: UserDraft = {
  username: '',
  password: '',
  email: '',
  status: 'enabled',
  role: 'user',
};

const ROLE_OPTIONS: UserRoleName[] = ['admin', 'operator', 'user'];

function getUserRole(user: AuthenticatedUser): UserRoleName {
  return user.role ?? (user.is_admin ? 'admin' : 'user');
}

export default function UserManagementTab({
  busy,
  users,
  onCreate,
  onDelete,
  onRefresh,
  onResetPassword,
  onUpdateStatus,
  onUpdateRole,
  onAuthExpired,
}: UserManagementTabProps) {
  const [draft, setDraft] = useState<UserDraft>(EMPTY_DRAFT);
  const [error, setError] = useState('');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [resetPasswordUid, setResetPasswordUid] = useState('');
  const [resetPasswordValue, setResetPasswordValue] = useState('');
  const [updatingRoleUid, setUpdatingRoleUid] = useState('');
  const [activeView, setActiveView] = useState<'users' | 'roles'>('users');
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const currentIndex = event.currentTarget.dataset.index ? Number(event.currentTarget.dataset.index) : 0;
    const nextIndex = event.key === 'ArrowRight' ? (currentIndex + 1) % 2
      : event.key === 'ArrowLeft' ? (currentIndex + 1) % 2
        : event.key === 'Home' ? 0
          : event.key === 'End' ? 1
            : -1;
    if (nextIndex < 0) return;
    event.preventDefault();
    const nextView = nextIndex === 0 ? 'users' : 'roles';
    setActiveView(nextView);
    tabRefs.current[nextIndex]?.focus();
  }

  const sortedUsers = useMemo(
    () => [...users].sort((left, right) => left.username.localeCompare(right.username)),
    [users],
  );

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    try {
      await onCreate(draft);
      setDraft(EMPTY_DRAFT);
      setShowCreateForm(false);
      setError('');
    } catch (nextError) {
      alertApiError(nextError, 'Failed to create user.');
    }
  }

  async function handleToggleStatus(user: AuthenticatedUser) {
    const nextStatus = user.status === 'enabled' ? 'disabled' : 'enabled';
    const actionLabel = nextStatus === 'disabled' ? 'Disable' : 'Enable';
    if (!window.confirm(`${actionLabel} ${user.username}? (确定要${nextStatus}该用户吗？)`)) {
      return;
    }

    try {
      await onUpdateStatus(user.uid, nextStatus);
      setError('');
    } catch (nextError) {
      alertApiError(nextError, 'Failed to update user status.');
    }
  }

  async function handleDelete(uid: string) {
    const user = users.find((entry) => entry.uid === uid);
    if (!user) {
      return;
    }
    if (!window.confirm(`Delete ${user.username}? (确定删除该用户吗？此操作不可逆)`)) {
      return;
    }

    try {
      await onDelete(uid);
      setError('');
    } catch (nextError) {
      alertApiError(nextError, 'Failed to delete user.');
    }
  }

  async function handleResetPassword(uid: string) {
    if (!resetPasswordValue.trim()) {
      setError('请输入重置后的密码。');
      return;
    }
    try {
      await onResetPassword(uid, resetPasswordValue);
      setResetPasswordUid('');
      setResetPasswordValue('');
      setError('');
    } catch (nextError) {
      alertApiError(nextError, 'Failed to reset password.');
    }
  }

  async function handleRoleChange(user: AuthenticatedUser, role: UserRoleName) {
    if (user.username === 'admin' || role === getUserRole(user)) {
      return;
    }
    setUpdatingRoleUid(user.uid);
    setError('');
    try {
      await onUpdateRole(user.uid, role);
    } catch (nextError) {
      alertApiError(nextError, 'Failed to update user role.');
      setError('Failed to update user role.');
    } finally {
      setUpdatingRoleUid('');
    }
  }

  return (
    <main className="aegis-admin-page" aria-labelledby="user-management-heading">
      <div className="aegis-admin-page__inner">
        <header className="aegis-page-intro">
          <div>
            <h1 id="user-management-heading" className="aegis-page-intro__title">User Management</h1>
            <p className="aegis-page-intro__description">
              管理注册账号、启停状态、管理员新增用户与密码重置。
            </p>
          </div>
          <div className="aegis-page-intro__badge">
            <span className="aegis-page-intro__badge-label">Directory:</span> {users.length} accounts
          </div>
        </header>

        <div className="aegis-page-tabs aegis-page-tabs--compact" role="tablist" aria-label="用户页面视图">
          <button ref={(element) => { tabRefs.current[0] = element; }} type="button" id="user-management-tab" role="tab" tabIndex={activeView === 'users' ? 0 : -1} aria-selected={activeView === 'users'} aria-controls="user-management-panel" data-index="0" onClick={() => setActiveView('users')} onKeyDown={handleTabKeyDown} className="aegis-page-tab">用户管理</button>
          <button ref={(element) => { tabRefs.current[1] = element; }} type="button" id="role-management-tab" role="tab" tabIndex={activeView === 'roles' ? 0 : -1} aria-selected={activeView === 'roles'} aria-controls="role-management-panel" data-index="1" onClick={() => setActiveView('roles')} onKeyDown={handleTabKeyDown} className="aegis-page-tab">rbac guard 角色管理</button>
        </div>

        {activeView === 'roles' ? <UserRoleManagementTab onAuthExpired={onAuthExpired} /> : <section id="user-management-panel" role="tabpanel" aria-labelledby="user-management-tab" className="aegis-page-content">
          <header className="aegis-page-content__header aegis-page-content__header--compact">
            <div>
              <h2 id="user-directory-heading" className="aegis-page-content__title">User Directory</h2>
              <p className="aegis-page-content__description">Review account access, reset credentials, and maintain account status.</p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <button type="button" onClick={() => void onRefresh()} className="aegis-btn aegis-btn--secondary px-4 py-2 text-sm">
                Refresh
              </button>
              <button type="button" onClick={() => setShowCreateForm((current) => !current)} className="aegis-btn aegis-btn--primary px-4 py-2 text-sm">
                新增用户
              </button>
            </div>
          </header>

          {showCreateForm ? (
            <form className="aegis-page-filter-bar grid gap-3 border-b border-slate-800 md:grid-cols-6" onSubmit={handleCreate}>
          <input
            aria-label="Create Username"
            value={draft.username}
            onChange={(event) => setDraft((current) => ({ ...current, username: event.target.value }))}
            placeholder="username"
            className="aegis-page-field px-3 py-2 text-sm"
          />
          <input
            aria-label="Create Password"
            type="password"
            value={draft.password}
            onChange={(event) => setDraft((current) => ({ ...current, password: event.target.value }))}
            placeholder="password"
            className="aegis-page-field px-3 py-2 text-sm"
          />
          <input
            aria-label="Create Email"
            type="email"
            value={draft.email}
            onChange={(event) => setDraft((current) => ({ ...current, email: event.target.value }))}
            placeholder="email@example.com"
            className="aegis-page-field px-3 py-2 text-sm"
          />
          <select
            aria-label="Create Status"
            value={draft.status}
            onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value as 'enabled' | 'disabled' }))}
            className="aegis-page-field px-3 py-2 text-sm"
          >
            <option value="enabled">enabled</option>
            <option value="disabled">disabled</option>
          </select>
          <select
            aria-label="Create Role"
            value={draft.role}
            onChange={(event) => setDraft((current) => ({ ...current, role: event.target.value as UserRoleName }))}
            className="aegis-page-field px-3 py-2 text-sm"
          >
            {ROLE_OPTIONS.map((role) => <option key={role} value={role}>{role}</option>)}
          </select>
          <button
            type="submit"
            disabled={busy}
            className="aegis-btn aegis-btn--primary px-4 py-2 text-sm"
          >
            Create User
          </button>
            </form>
          ) : null}

          {error ? (
            <div className="border-b border-rose-900/30 bg-rose-950/20 px-6 py-3 text-sm text-rose-300">
              {error}
            </div>
          ) : null}

          <div className="aegis-page-content__body overflow-x-auto">
            <table className="w-full min-w-[780px] table-fixed border-collapse border-b border-slate-800 text-left">
              <colgroup>
                <col className="w-[18%]" />
                <col className="w-[23%]" />
                <col className="w-[12%]" />
                <col className="w-[12%]" />
                <col className="w-[18%]" />
                <col className="w-[17%]" />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-800 bg-[#03060C] font-mono text-[9px] uppercase tracking-wider text-slate-500">
                  <th className="p-3">Username &amp; ID</th>
                  <th className="p-3">Email</th>
                  <th className="p-3">Role</th>
                  <th className="p-3">Status</th>
                  <th className="p-3">Last Login</th>
                  <th className="p-3 text-center">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {sortedUsers.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-8 text-center font-mono text-xs text-slate-500">
                      No accounts in the current directory.
                    </td>
                  </tr>
                ) : sortedUsers.map((user) => (
                  <Fragment key={user.uid}>
                    <tr className="aegis-table-row align-middle text-sm text-slate-300">
                      <td className="p-3">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className={`aegis-status-indicator ${user.status === 'enabled' ? 'aegis-status-indicator--success' : 'aegis-status-indicator--warning'}`} />
                          <div className="min-w-0">
                            <div className="truncate font-semibold text-white" title={user.username}>{user.username}</div>
                            <div className="mt-1 truncate font-mono text-[10px] text-slate-500" title={user.uid}>User ID: {user.uid}</div>
                          </div>
                        </div>
                      </td>
                      <td className="p-3 text-slate-400">
                        <div className="truncate" title={user.email}>{user.email}</div>
                      </td>
                      <td className="p-3">
                        <select
                          aria-label={`Role for ${user.username}`}
                          value={getUserRole(user)}
                          disabled={user.username === 'admin' || updatingRoleUid === user.uid}
                          onChange={(event) => void handleRoleChange(user, event.target.value as UserRoleName)}
                          className="aegis-page-field w-full min-w-0 px-2 py-1.5 text-xs uppercase disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {ROLE_OPTIONS.map((role) => <option key={role} value={role}>{role}</option>)}
                        </select>
                      </td>
                      <td className="p-3">
                        <span className={`aegis-status-badge ${
                          user.status === 'enabled'
                            ? 'aegis-status-badge--success'
                            : 'aegis-status-badge--warning'
                        }`}>
                          {user.status}
                        </span>
                      </td>
                      <td className="p-3 font-mono text-[11px] text-slate-500">
                        <div className="truncate" title={user.last_login || 'Never'}>{user.last_login || 'Never'}</div>
                      </td>
                      <td className="p-3">
                        <div className="flex justify-center gap-1.5">
                          <button
                            type="button"
                            aria-label={user.status === 'enabled' ? `Disable ${user.username}` : `Enable ${user.username}`}
                            title={user.status === 'enabled' ? `Disable ${user.username}` : `Enable ${user.username}`}
                            onClick={() => void handleToggleStatus(user)}
                            className="aegis-btn aegis-btn--secondary aegis-btn--icon h-8 w-8"
                          >
                            {user.status === 'enabled' ? <PowerOff className="h-3.5 w-3.5" /> : <Power className="h-3.5 w-3.5" />}
                          </button>
                          <button
                            type="button"
                            aria-label={`Open password reset for ${user.username}`}
                            aria-pressed={resetPasswordUid === user.uid}
                            title={`Open password reset for ${user.username}`}
                            onClick={() => {
                              setResetPasswordUid(user.uid);
                              setResetPasswordValue('');
                            }}
                            className={`aegis-btn aegis-btn--secondary aegis-btn--icon h-8 w-8 ${resetPasswordUid === user.uid ? 'aegis-btn--selected' : ''}`}
                          >
                            <KeyRound className="h-3.5 w-3.5" />
                          </button>
                          {user.username !== 'admin' ? (
                            <button
                              type="button"
                              aria-label={`Delete ${user.username}`}
                              title={`Delete ${user.username}`}
                              onClick={() => void handleDelete(user.uid)}
                              className="aegis-btn aegis-btn--danger aegis-btn--icon h-8 w-8"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                    {resetPasswordUid === user.uid ? (
                      <tr className="bg-[#03060C]">
                        <td colSpan={6} className="px-4 py-3">
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="font-mono text-[10px] font-bold uppercase tracking-wider text-slate-500">
                              Reset password · {user.username}
                            </div>
                            <div className="flex flex-1 flex-wrap justify-end gap-2">
                              <input
                                aria-label={`New password for ${user.username}`}
                                type="password"
                                value={resetPasswordValue}
                                onChange={(event) => setResetPasswordValue(event.target.value)}
                                placeholder="new password"
                                className="aegis-page-field min-w-52 px-3 py-2 text-sm"
                              />
                              <button
                                type="button"
                                onClick={() => void handleResetPassword(user.uid)}
                                className="aegis-btn aegis-btn--primary px-3 py-2 text-xs"
                              >
                                Save Password
                              </button>
                            </div>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </section>}
      </div>
    </main>
  );
}
