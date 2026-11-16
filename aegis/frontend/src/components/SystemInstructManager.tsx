import { useEffect, useState, type FormEvent } from 'react';
import { FileText, Pencil, Plus, Save, Trash2, X } from 'lucide-react';

import { ApiError, fetchJSON, getApiErrorMessage } from '../lib/api';

type SystemInstructStatus = 'enabled' | 'disabled';

interface SystemInstruct {
  id: string;
  name: string;
  describe: string;
  instruct: string;
  create_time: string;
  update_time: string;
  status: SystemInstructStatus;
}

interface SystemInstructDraft {
  name: string;
  describe: string;
  instruct: string;
  status: SystemInstructStatus;
}

interface SystemInstructManagerProps {
  onAuthExpired?: () => void;
}

const EMPTY_DRAFT: SystemInstructDraft = {
  name: '',
  describe: '',
  instruct: '',
  status: 'enabled',
};

function formatTimestamp(value: string): string {
  return value.replace('T', ' ').replace('Z', ' UTC');
}

function sortInstructions(instructions: SystemInstruct[]): SystemInstruct[] {
  return [...instructions].sort((left, right) =>
    right.update_time.localeCompare(left.update_time) || left.name.localeCompare(right.name),
  );
}

export default function SystemInstructManager({
  onAuthExpired,
}: SystemInstructManagerProps) {
  const [instructions, setInstructions] = useState<SystemInstruct[]>([]);
  const [draft, setDraft] = useState<SystemInstructDraft>(EMPTY_DRAFT);
  const [editing, setEditing] = useState<SystemInstruct | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function loadInstructions() {
    setLoading(true);
    setError('');
    try {
      const response = await fetchJSON<{ instructions: SystemInstruct[] }>(
        '/api/system-instructs',
      );
      setInstructions(sortInstructions(response.instructions));
    } catch (loadError) {
      if (loadError instanceof ApiError && loadError.status === 401) {
        onAuthExpired?.();
        return;
      }
      setError(getApiErrorMessage(loadError, 'Unable to load system instructions.'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadInstructions();
  }, []);

  function closeEditor() {
    setEditing(null);
    setDraft(EMPTY_DRAFT);
    setEditorOpen(false);
  }

  function openCreate() {
    setError('');
    setEditing(null);
    setDraft(EMPTY_DRAFT);
    setEditorOpen(true);
  }

  function openEdit(instruction: SystemInstruct) {
    setError('');
    setEditing(instruction);
    setDraft({
      name: instruction.name,
      describe: instruction.describe,
      instruct: instruction.instruct,
      status: instruction.status,
    });
    setEditorOpen(true);
  }

  function updateDraft<K extends keyof SystemInstructDraft>(
    field: K,
    value: SystemInstructDraft[K],
  ) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function saveInstruction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.name.trim() || !draft.instruct.trim()) {
      setError('Name and instruction are required.');
      return;
    }

    setSaving(true);
    setError('');
    try {
      const path = editing
        ? `/api/system-instructs/${encodeURIComponent(editing.id)}`
        : '/api/system-instructs';
      const saved = await fetchJSON<SystemInstruct>(path, {
        method: editing ? 'PUT' : 'POST',
        body: JSON.stringify(draft),
      });
      setInstructions((current) =>
        sortInstructions(
          editing
            ? current.map((item) => (item.id === saved.id ? saved : item))
            : [...current, saved],
        ),
      );
      closeEditor();
    } catch (saveError) {
      if (saveError instanceof ApiError && saveError.status === 401) {
        onAuthExpired?.();
        return;
      }
      setError(getApiErrorMessage(saveError, 'Unable to save system instruction.'));
    } finally {
      setSaving(false);
    }
  }

  async function deleteInstruction(instruction: SystemInstruct) {
    if (!window.confirm(`Delete system instruction “${instruction.name}”? This cannot be undone.`)) {
      return;
    }
    setError('');
    try {
      await fetchJSON(`/api/system-instructs/${encodeURIComponent(instruction.id)}`, {
        method: 'DELETE',
      });
      setInstructions((current) => current.filter((item) => item.id !== instruction.id));
      if (editing?.id === instruction.id) {
        closeEditor();
      }
    } catch (deleteError) {
      if (deleteError instanceof ApiError && deleteError.status === 401) {
        onAuthExpired?.();
        return;
      }
      setError(getApiErrorMessage(deleteError, 'Unable to delete system instruction.'));
    }
  }

  return (
    <section aria-labelledby="system-instruct-heading">
      <header className="aegis-page-content__header">
        <div>
          <h2 id="system-instruct-heading" className="aegis-page-content__title">
            System Instructions
          </h2>
          <p className="aegis-page-content__description">
            Create, activate, revise, and retire global instructions for Aegis.
          </p>
        </div>
        <button
          className="aegis-btn aegis-btn--primary inline-flex items-center gap-1.5 px-3 py-2 text-xs"
          onClick={openCreate}
          type="button"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          New instruction
        </button>
      </header>

      {error ? <div className="aegis-alert aegis-alert--danger mx-5 mt-4" role="alert">{error}</div> : null}

      {editorOpen ? (
        <form className="border-b border-slate-800 p-5" onSubmit={(event) => void saveInstruction(event)}>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_11rem]">
            <label className="block text-[10px] font-mono font-bold tracking-widest text-slate-500">
              NAME
              <input
                aria-label="System instruction name"
                className="mt-1.5 w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                maxLength={128}
                onChange={(event) => updateDraft('name', event.target.value)}
                placeholder="Incident response baseline"
                required
                value={draft.name}
              />
            </label>
            <label className="block text-[10px] font-mono font-bold tracking-widest text-slate-500">
              STATUS
              <select
                aria-label="System instruction status"
                className="mt-1.5 w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                onChange={(event) => updateDraft('status', event.target.value as SystemInstructStatus)}
                value={draft.status}
              >
                <option value="enabled">Enabled</option>
                <option value="disabled">Disabled</option>
              </select>
            </label>
          </div>
          <label className="mt-4 block text-[10px] font-mono font-bold tracking-widest text-slate-500">
            DESCRIPTION
            <textarea
              aria-label="System instruction description"
              className="mt-1.5 min-h-20 w-full resize-y rounded border border-slate-800 bg-[#020408] px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
              maxLength={512}
              onChange={(event) => updateDraft('describe', event.target.value)}
              placeholder="Short purpose or scope for this instruction"
              value={draft.describe}
            />
          </label>
          <label className="mt-4 block text-[10px] font-mono font-bold tracking-widest text-slate-500">
            INSTRUCTION
            <textarea
              aria-label="System instruction content"
              className="mt-1.5 min-h-44 w-full resize-y rounded border border-slate-800 bg-[#020408] px-3 py-2 font-mono text-xs leading-relaxed text-white outline-none focus:border-cyan-500"
              maxLength={50000}
              onChange={(event) => updateDraft('instruct', event.target.value)}
              placeholder="Write the system instruction applied by Aegis…"
              required
              value={draft.instruct}
            />
          </label>
          <div className="mt-4 flex justify-end gap-2">
            <button className="aegis-btn aegis-btn--secondary px-3 py-2 text-xs" onClick={closeEditor} type="button">
              <X className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />
              Cancel
            </button>
            <button className="aegis-btn aegis-btn--primary px-3 py-2 text-xs" disabled={saving} type="submit">
              <Save className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />
              {saving ? 'Saving…' : editing ? 'Save changes' : 'Create instruction'}
            </button>
          </div>
        </form>
      ) : null}

      <div className="aegis-page-content__body overflow-x-auto">
        {loading ? <div className="py-14 text-center font-mono text-xs text-slate-500">LOADING SYSTEM INSTRUCTIONS…</div> : null}
        {!loading && instructions.length === 0 ? (
          <div className="px-6 py-16 text-center">
            <FileText className="mx-auto h-7 w-7 text-slate-600" aria-hidden="true" />
            <p className="mt-3 text-sm font-semibold text-slate-300">No system instructions yet</p>
            <p className="mt-1 text-xs text-slate-500">Create an instruction to maintain a global operating baseline.</p>
          </div>
        ) : null}
        {!loading && instructions.length > 0 ? (
          <table className="w-full min-w-[880px] text-left">
            <thead className="border-y border-slate-800 bg-[#05080F] font-mono text-[10px] tracking-widest text-slate-500">
              <tr>
                <th className="px-5 py-3 font-bold">NAME</th>
                <th className="px-5 py-3 font-bold">DESCRIPTION</th>
                <th className="px-5 py-3 font-bold">INSTRUCTION</th>
                <th className="px-5 py-3 font-bold">STATUS</th>
                <th className="px-5 py-3 font-bold">UPDATED</th>
                <th className="px-5 py-3 text-right font-bold">ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {instructions.map((instruction) => (
                <tr className="aegis-table-row border-b border-slate-800/80 text-xs" key={instruction.id}>
                  <td className="px-5 py-4 font-medium text-slate-200">{instruction.name}</td>
                  <td className="max-w-xs px-5 py-4"><p className="line-clamp-2 text-[11px] leading-relaxed text-slate-400">{instruction.describe || '—'}</p></td>
                  <td className="max-w-xl px-5 py-4"><p className="line-clamp-2 whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-slate-400">{instruction.instruct}</p></td>
                  <td className="px-5 py-4"><span className={`aegis-status-badge ${instruction.status === 'enabled' ? 'aegis-status-badge--success' : 'aegis-status-badge--warning'}`}>{instruction.status}</span></td>
                  <td className="whitespace-nowrap px-5 py-4 font-mono text-[10px] text-slate-500">{formatTimestamp(instruction.update_time)}</td>
                  <td className="px-5 py-4"><div className="flex justify-end gap-1"><button aria-label={`Edit ${instruction.name}`} className="aegis-btn aegis-btn--secondary aegis-btn--icon p-2" onClick={() => openEdit(instruction)} type="button"><Pencil className="h-3.5 w-3.5" /></button><button aria-label={`Delete ${instruction.name}`} className="aegis-btn aegis-btn--danger aegis-btn--icon p-2" onClick={() => void deleteInstruction(instruction)} type="button"><Trash2 className="h-3.5 w-3.5" /></button></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    </section>
  );
}
