import { useEffect, useState } from 'react';
import { KeyRound, Pencil, Plus, Save, Trash2, X } from 'lucide-react';
import { fetchJSON, getApiErrorMessage } from '../lib/api';

type UserEnvEntry = { key: string; masked_value: string };
type UserEnvListResponse = { variables: UserEnvEntry[] };
type UserEnvSetResponse = { updated: boolean; key: string; variables: UserEnvEntry[] };
type DialogMode = 'create' | 'edit' | null;

const EMPTY_DRAFT = { key: '', value: '' };

export default function UserEnvTab() {
  const [variables, setVariables] = useState<UserEnvEntry[]>([]);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [dialogMode, setDialogMode] = useState<DialogMode>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function loadVariables() {
    setLoading(true);
    setError('');
    try {
      const response = await fetchJSON<UserEnvListResponse>('/api/my/user-env');
      setVariables(response.variables);
    } catch (loadError) {
      setError(getApiErrorMessage(loadError, 'Unable to load user env variables.'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadVariables(); }, []);

  function closeDialog() {
    setDialogMode(null);
    setEditingKey(null);
    setDraft(EMPTY_DRAFT);
  }

  function openCreate() {
    setError('');
    setEditingKey(null);
    setDraft(EMPTY_DRAFT);
    setDialogMode('create');
  }

  function openEdit(entry: UserEnvEntry) {
    setError('');
    setEditingKey(entry.key);
    setDraft({ key: entry.key, value: '' });
    setDialogMode('edit');
  }

  async function saveVariable(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const key = draft.key.trim();
    if (!key) {
      setError('Key is required.');
      return;
    }
    if (dialogMode === 'edit' && !draft.value) {
      setError('Value is required (leave the dialog and use delete instead of setting an empty value).');
      return;
    }
    if (!draft.value) {
      setError('Value is required.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const saved = await fetchJSON<UserEnvSetResponse>('/api/my/user-env', {
        method: 'PUT',
        body: JSON.stringify({ key, value: draft.value }),
      });
      setVariables(saved.variables);
      closeDialog();
    } catch (saveError) {
      setError(getApiErrorMessage(saveError, 'Unable to save user env variable.'));
    } finally {
      setSaving(false);
    }
  }

  async function deleteVariable(entry: UserEnvEntry) {
    if (!window.confirm(`Delete user env variable “${entry.key}”? This cannot be undone.`)) return;
    setError('');
    try {
      await fetchJSON(`/api/my/user-env/${encodeURIComponent(entry.key)}`, { method: 'DELETE' });
      setVariables((current) => current.filter((item) => item.key !== entry.key));
    } catch (deleteError) {
      setError(getApiErrorMessage(deleteError, 'Unable to delete user env variable.'));
    }
  }

  return (
    <main className="aegis-admin-page" aria-labelledby="user-env-heading">
      <div className="aegis-admin-page__inner">
        <header className="aegis-page-intro">
          <div>
            <h1 id="user-env-heading" className="aegis-page-intro__title">User Environment Variables</h1>
            <p className="aegis-page-intro__description">Manage your own aegis-platform env variables. Values are stored masked-view only (first/last 4 chars shown).</p>
          </div>
          <div className="aegis-page-intro__badge"><span className="aegis-page-intro__badge-label">Scope:</span> your account only</div>
        </header>

        <section className="aegis-page-content" aria-labelledby="user-env-list-heading">
          <header className="aegis-page-content__header">
            <div>
              <h2 id="user-env-list-heading" className="aegis-page-content__title">My Variables</h2>
              <p className="aegis-page-content__description">Variables are injected into your aegis chat sessions as environment variables.</p>
            </div>
            <button type="button" onClick={openCreate} className="aegis-btn aegis-btn--primary inline-flex items-center gap-1.5 px-3 py-2 text-xs"><Plus className="h-3.5 w-3.5" /> New variable</button>
          </header>
          {error ? <div role="alert" className="mx-5 mt-4 rounded border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-xs text-rose-200">{error}</div> : null}
          <div className="aegis-page-content__body overflow-x-auto">
            {loading ? <div className="py-14 text-center font-mono text-xs text-slate-500">LOADING USER ENV VARIABLES…</div> : null}
            {!loading && variables.length === 0 ? <div className="px-6 py-16 text-center"><KeyRound className="mx-auto h-7 w-7 text-slate-600" /><p className="mt-3 text-sm font-semibold text-slate-300">No user env variables yet</p><p className="mt-1 text-xs text-slate-500">Create one to make it available to your aegis sessions as an environment variable.</p></div> : null}
            {!loading && variables.length > 0 ? <table className="w-full min-w-[640px] text-left"><thead className="border-y border-slate-800 bg-[#05080F] font-mono text-[10px] tracking-widest text-slate-500"><tr><th className="px-5 py-3 font-bold">KEY</th><th className="px-5 py-3 font-bold">VALUE (MASKED)</th><th className="px-5 py-3 text-right font-bold">ACTIONS</th></tr></thead><tbody>{variables.map((entry) => <tr key={entry.key} className="aegis-table-row border-b border-slate-800/80 text-xs"><td className="px-5 py-4"><span className="rounded border border-cyan-900/50 bg-cyan-950/20 px-2 py-1 font-mono text-[10px] font-bold text-cyan-300">{entry.key}</span></td><td className="px-5 py-4 font-mono text-[11px] text-slate-400">{entry.masked_value}</td><td className="px-5 py-4"><div className="flex justify-end gap-1"><button type="button" aria-label={`Edit ${entry.key}`} onClick={() => openEdit(entry)} className="aegis-btn aegis-btn--secondary aegis-btn--icon p-2"><Pencil className="h-3.5 w-3.5" /></button><button type="button" aria-label={`Delete ${entry.key}`} onClick={() => void deleteVariable(entry)} className="aegis-btn aegis-btn--danger aegis-btn--icon p-2"><Trash2 className="h-3.5 w-3.5" /></button></div></td></tr>)}</tbody></table> : null}
          </div>
        </section>
      </div>

      {dialogMode ? <div role="presentation" className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"><div role="dialog" aria-modal="true" aria-labelledby="user-env-dialog-title" className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-slate-700 bg-[#05080F] shadow-2xl"><header className="flex items-start justify-between gap-4 border-b border-slate-800 px-5 py-4"><div><p className="font-mono text-[10px] font-bold tracking-[0.16em] text-cyan-400">{dialogMode === 'edit' ? 'UPDATE VARIABLE' : 'NEW VARIABLE'}</p><h2 id="user-env-dialog-title" className="mt-1 text-base font-semibold text-white">{dialogMode === 'edit' ? 'Update user env variable' : 'Create user env variable'}</h2></div><button type="button" aria-label="Close user env dialog" onClick={closeDialog} className="rounded p-2 text-slate-500 hover:bg-slate-800 hover:text-white"><X className="h-4 w-4" /></button></header><form onSubmit={saveVariable} className="p-5"><label className="block text-[10px] font-mono font-bold tracking-widest text-slate-500">KEY<input aria-label="Variable key" value={dialogMode === 'edit' ? editingKey ?? '' : draft.key} onChange={(event) => setDraft((current) => ({ ...current, key: event.target.value }))} maxLength={256} required readOnly={dialogMode === 'edit'} className="mt-1.5 w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-sm text-white outline-none focus:border-cyan-500 disabled:text-slate-500" placeholder="MY_API_KEY" /></label><label className="mt-4 block text-[10px] font-mono font-bold tracking-widest text-slate-500">VALUE{dialogMode === 'edit' ? <span className="ml-2 normal-case tracking-normal text-slate-600">(current value is not shown; enter the new value)</span> : null}<input aria-label="Variable value" type="password" value={draft.value} onChange={(event) => setDraft((current) => ({ ...current, value: event.target.value }))} maxLength={32000} required className="mt-1.5 w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 font-mono text-sm text-white outline-none focus:border-cyan-500" placeholder="Enter the secret value" /></label><p className="mt-3 text-[10px] text-slate-600">Values are stored in plaintext on the server and displayed only masked (first/last 4 characters). The stored value is injected into your aegis chat sessions.</p><div className="mt-5 flex justify-end gap-2"><button type="button" onClick={closeDialog} className="rounded border border-slate-700 px-3 py-2 text-xs font-bold text-slate-300 hover:bg-slate-800">Cancel</button><button disabled={saving} type="submit" className="inline-flex items-center gap-1.5 rounded bg-cyan-500 px-3 py-2 text-xs font-bold text-white hover:bg-cyan-600 disabled:bg-slate-800"><Save className="h-3.5 w-3.5" />{saving ? 'Saving…' : 'Save variable'}</button></div></form></div></div> : null}
    </main>
  );
}
