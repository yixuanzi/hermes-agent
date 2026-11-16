import { FormEvent, useEffect, useState } from 'react';
import { Edit2, Plus, RefreshCw, Trash2, X } from 'lucide-react';

import { ApiError, alertApiError, fetchJSON, getApiErrorMessage } from '../lib/api';
import type { UserRole, UserRoleDraft, UserRoleName } from '../types';

interface UserRoleManagementTabProps {
  onAuthExpired?: () => void;
}

const ROLE_OPTIONS: UserRoleName[] = ['user', 'operator', 'admin'];
const EMPTY_DRAFT: UserRoleDraft = {
  platform: '',
  uid: '',
  uname: '',
  role: 'user',
};

function formatTimestamp(value: string): string {
  return value.replace('T', ' ').replace('Z', ' UTC');
}

export default function UserRoleManagementTab({ onAuthExpired }: UserRoleManagementTabProps) {
  const [roles, setRoles] = useState<UserRole[]>([]);
  const [draft, setDraft] = useState<UserRoleDraft>(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState('');

  async function loadRoles() {
    setLoading(true);
    setError('');
    try {
      const response = await fetchJSON<{ roles: UserRole[] }>('/api/user-roles');
      setRoles(response.roles);
    } catch (loadError) {
      if (loadError instanceof ApiError && loadError.status === 401) {
        onAuthExpired?.();
        return;
      }
      setError(getApiErrorMessage(loadError, 'Unable to load user roles.'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadRoles();
  }, []);

  function closeModal() {
    setModalOpen(false);
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
  }

  function openCreate() {
    setError('');
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
    setModalOpen(true);
  }

  function openEdit(role: UserRole) {
    setError('');
    setEditingId(role.id);
    setDraft({
      platform: role.platform,
      uid: role.uid,
      uname: role.uname,
      role: role.role,
    });
    setModalOpen(true);
  }

  function updateDraft(field: keyof UserRoleDraft, value: string) {
    setDraft((current) => ({
      ...current,
      [field]: field === 'role' ? value as UserRoleName : value,
    }));
  }

  async function saveRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.platform.trim() || !draft.uid.trim() || !draft.uname.trim()) {
      setError('Platform, UID, and username are required.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const path = editingId
        ? `/api/user-roles/${encodeURIComponent(editingId)}`
        : '/api/user-roles';
      const saved = await fetchJSON<UserRole>(path, {
        method: editingId ? 'PUT' : 'POST',
        body: JSON.stringify(draft),
      });
      setRoles((current) => {
        const next = editingId
          ? current.map((role) => role.id === saved.id ? saved : role)
          : [saved, ...current];
        return next.sort((left, right) => right.update_time.localeCompare(left.update_time));
      });
      closeModal();
    } catch (saveError) {
      if (saveError instanceof ApiError && saveError.status === 401) {
        onAuthExpired?.();
        return;
      }
      setError(getApiErrorMessage(saveError, 'Unable to save user role.'));
    } finally {
      setSaving(false);
    }
  }

  async function deleteRole(role: UserRole) {
    if (!window.confirm(`Delete role for ${role.uname} on ${role.platform}?`)) return;
    setDeletingId(role.id);
    setError('');
    try {
      await fetchJSON<{ deleted: boolean }>(`/api/user-roles/${encodeURIComponent(role.id)}`, { method: 'DELETE' });
      setRoles((current) => current.filter((item) => item.id !== role.id));
    } catch (deleteError) {
      if (deleteError instanceof ApiError && deleteError.status === 401) {
        onAuthExpired?.();
        return;
      }
      alertApiError(deleteError, 'Unable to delete user role.');
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <section id="role-management-panel" role="tabpanel" aria-labelledby="role-management-tab" className="aegis-page-content">
      <header className="aegis-page-content__header aegis-page-content__header--compact">
        <div>
          <h2 className="aegis-page-content__title">rbac guard 角色管理</h2>
          <p className="aegis-page-content__description">维护 RBAC Guard 的平台用户角色规则。</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => void loadRoles()} disabled={loading} className="aegis-btn aegis-btn--secondary inline-flex items-center gap-1.5 px-4 py-2 text-sm">
            <RefreshCw className={loading ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} /> Refresh
          </button>
          <button type="button" onClick={openCreate} className="aegis-btn aegis-btn--primary inline-flex items-center gap-1.5 px-4 py-2 text-sm">
            <Plus className="h-3.5 w-3.5" /> 新增角色
          </button>
        </div>
      </header>

      {error ? <div role="alert" className="border-b border-rose-900/30 bg-rose-950/20 px-6 py-3 text-sm text-rose-300">{error}</div> : null}
      <div className="aegis-page-content__body overflow-x-auto">
        {loading ? <div className="p-10 text-center font-mono text-xs text-slate-500">LOADING USER ROLES…</div> : null}
        {!loading && roles.length === 0 ? <div className="p-10 text-center text-sm text-slate-500">No user roles found.</div> : null}
        {!loading && roles.length > 0 ? (
          <table className="w-full min-w-[900px] border-collapse text-left">
            <thead className="border-b border-slate-800 bg-[#03060C] font-mono text-[9px] uppercase tracking-wider text-slate-500">
              <tr><th className="p-3">ID</th><th className="p-3">Platform</th><th className="p-3">UID</th><th className="p-3">Username</th><th className="p-3">Role</th><th className="p-3">Updated</th><th className="p-3 text-center">Actions</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {roles.map((role) => (
                <tr key={role.id} className="aegis-table-row text-sm text-slate-300">
                  <td className="max-w-[180px] p-3"><span className="block truncate font-mono text-[10px] text-slate-500" title={role.id}>{role.id}</span></td>
                  <td className="p-3 font-mono text-cyan-300">{role.platform}</td>
                  <td className="max-w-[180px] p-3"><span className="block truncate font-mono" title={role.uid}>{role.uid}</span></td>
                  <td className="p-3">{role.uname}</td>
                  <td className="p-3"><span className="aegis-status-badge aegis-status-badge--success">{role.role}</span></td>
                  <td className="whitespace-nowrap p-3 font-mono text-[10px] text-slate-500">{formatTimestamp(role.update_time)}</td>
                  <td className="p-3"><div className="flex justify-center gap-1.5"><button type="button" aria-label={`Edit role for ${role.uname}`} onClick={() => openEdit(role)} className="aegis-btn aegis-btn--secondary aegis-btn--icon h-8 w-8"><Edit2 className="h-3.5 w-3.5" /></button><button type="button" disabled={deletingId === role.id} aria-label={`Delete role for ${role.uname}`} onClick={() => void deleteRole(role)} className="aegis-btn aegis-btn--danger aegis-btn--icon h-8 w-8"><Trash2 className="h-3.5 w-3.5" /></button></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>

      {modalOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4 backdrop-blur-sm">
          <form onSubmit={(event) => void saveRole(event)} className="w-full max-w-md space-y-4 rounded-xl border border-slate-800 bg-[#05080F] p-5 shadow-2xl">
            <div className="flex items-center justify-between"><h3 className="font-bold text-white">{editingId ? '编辑角色' : '新增角色'}</h3><button type="button" aria-label="Close user role dialog" onClick={closeModal} className="aegis-btn aegis-btn--ghost aegis-btn--icon rounded p-1"><X className="h-4 w-4" /></button></div>
            <label className="block space-y-1 text-sm text-slate-300"><span>Platform</span><input aria-label="Role Platform" required value={draft.platform} onChange={(event) => updateDraft('platform', event.target.value)} className="w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-white" /></label>
            <label className="block space-y-1 text-sm text-slate-300"><span>UID</span><input aria-label="Role UID" required value={draft.uid} onChange={(event) => updateDraft('uid', event.target.value)} className="w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-white" /></label>
            <label className="block space-y-1 text-sm text-slate-300"><span>用户名</span><input aria-label="Role Username" required value={draft.uname} onChange={(event) => updateDraft('uname', event.target.value)} className="w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-white" /></label>
            <label className="block space-y-1 text-sm text-slate-300"><span>Role</span><select aria-label="Role" value={draft.role} onChange={(event) => updateDraft('role', event.target.value)} className="w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-white">{ROLE_OPTIONS.map((role) => <option key={role} value={role}>{role}</option>)}</select></label>
            <div className="flex justify-end gap-2"><button type="button" onClick={closeModal} className="aegis-btn aegis-btn--secondary rounded px-4 py-2 font-semibold">取消</button><button type="submit" disabled={saving} className="aegis-btn aegis-btn--primary rounded px-4 py-2 font-bold">{saving ? '保存中…' : '保存角色'}</button></div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
