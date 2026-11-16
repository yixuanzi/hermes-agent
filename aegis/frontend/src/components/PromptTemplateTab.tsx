import { useEffect, useState } from 'react';
import { Eye, FilePenLine, Pencil, Plus, Save, Trash2, X } from 'lucide-react';
import { fetchJSON, getApiErrorMessage } from '../lib/api';
import type { PromptTemplate, PromptTemplateDraft } from '../types';

const EMPTY_DRAFT: PromptTemplateDraft = { tag: '', desc: '', prompt: '' };
type PromptTemplateListResponse = { templates: PromptTemplate[] };
type DialogMode = 'create' | 'edit' | 'view' | null;

function formatTimestamp(value: string): string {
  return value.replace('T', ' ').replace('Z', ' UTC');
}

function sortTemplates(templates: PromptTemplate[]): PromptTemplate[] {
  return [...templates].sort((left, right) =>
    left.tag.localeCompare(right.tag) || right.update_time.localeCompare(left.update_time),
  );
}

export default function PromptTemplateTab() {
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [draft, setDraft] = useState<PromptTemplateDraft>(EMPTY_DRAFT);
  const [selected, setSelected] = useState<PromptTemplate | null>(null);
  const [dialogMode, setDialogMode] = useState<DialogMode>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function loadTemplates() {
    setLoading(true);
    setError('');
    try {
      const response = await fetchJSON<PromptTemplateListResponse>('/api/prompt-templates');
      setTemplates(sortTemplates(response.templates));
    } catch (loadError) {
      setError(getApiErrorMessage(loadError, 'Unable to load prompt templates.'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadTemplates(); }, []);

  function closeDialog() {
    setDialogMode(null);
    setSelected(null);
    setDraft(EMPTY_DRAFT);
  }

  function openCreate() {
    setError('');
    setSelected(null);
    setDraft(EMPTY_DRAFT);
    setDialogMode('create');
  }

  function openEdit(template: PromptTemplate) {
    setError('');
    setSelected(template);
    setDraft({ tag: template.tag, desc: template.desc, prompt: template.prompt });
    setDialogMode('edit');
  }

  function openView(template: PromptTemplate) {
    setSelected(template);
    setDialogMode('view');
  }

  function updateDraft(field: keyof PromptTemplateDraft, value: string) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function saveTemplate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.tag.trim() || !draft.prompt.trim()) {
      setError('Tag and prompt are required.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const editing = dialogMode === 'edit' && selected;
      const path = editing ? `/api/prompt-templates/${encodeURIComponent(selected.id)}` : '/api/prompt-templates';
      const saved = await fetchJSON<PromptTemplate>(path, { method: editing ? 'PUT' : 'POST', body: JSON.stringify(draft) });
      setTemplates((current) => sortTemplates(editing ? current.map((item) => item.id === saved.id ? saved : item) : [...current, saved]));
      closeDialog();
    } catch (saveError) {
      setError(getApiErrorMessage(saveError, 'Unable to save prompt template.'));
    } finally {
      setSaving(false);
    }
  }

  async function deleteTemplate(template: PromptTemplate) {
    if (!window.confirm(`Delete prompt template “${template.tag}”? This cannot be undone.`)) return;
    setError('');
    try {
      await fetchJSON(`/api/prompt-templates/${encodeURIComponent(template.id)}`, { method: 'DELETE' });
      setTemplates((current) => current.filter((item) => item.id !== template.id));
      if (selected?.id === template.id) closeDialog();
    } catch (deleteError) {
      setError(getApiErrorMessage(deleteError, 'Unable to delete prompt template.'));
    }
  }

  const isEditor = dialogMode === 'create' || dialogMode === 'edit';
  return (
    <main className="aegis-admin-page" aria-labelledby="prompt-template-heading">
      <div className="aegis-admin-page__inner">
        <header className="aegis-page-intro">
          <div>
            <h1 id="prompt-template-heading" className="aegis-page-intro__title">Prompt Templates</h1>
            <p className="aegis-page-intro__description">Manage reusable prompts visible only to your signed-in account.</p>
          </div>
          <div className="aegis-page-intro__badge"><span className="aegis-page-intro__badge-label">Library:</span> personal templates</div>
        </header>

        <section className="aegis-page-content" aria-labelledby="prompt-library-heading">
          <header className="aegis-page-content__header">
            <div>
              <h2 id="prompt-library-heading" className="aegis-page-content__title">Personal Prompt Library</h2>
              <p className="aegis-page-content__description">Create, review, and maintain reusable chat instructions.</p>
            </div>
            <button type="button" onClick={openCreate} className="aegis-btn aegis-btn--primary inline-flex items-center gap-1.5 px-3 py-2 text-xs"><Plus className="h-3.5 w-3.5" /> New template</button>
          </header>
          {error ? <div role="alert" className="mx-5 mt-4 rounded border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-xs text-rose-200">{error}</div> : null}
          <div className="aegis-page-content__body overflow-x-auto">
            {loading ? <div className="py-14 text-center font-mono text-xs text-slate-500">LOADING PERSONAL PROMPT LIBRARY…</div> : null}
            {!loading && templates.length === 0 ? <div className="px-6 py-16 text-center"><FilePenLine className="mx-auto h-7 w-7 text-slate-600" /><p className="mt-3 text-sm font-semibold text-slate-300">No prompt templates yet</p><p className="mt-1 text-xs text-slate-500">Create one to insert it quickly into an Aegis chat draft.</p></div> : null}
            {!loading && templates.length > 0 ? <table className="w-full min-w-[720px] text-left"><thead className="border-y border-slate-800 bg-[#05080F] font-mono text-[10px] tracking-widest text-slate-500"><tr><th className="px-5 py-3 font-bold">TAG</th><th className="px-5 py-3 font-bold">DESCRIPTION</th><th className="px-5 py-3 font-bold">UPDATED</th><th className="px-5 py-3 text-right font-bold">ACTIONS</th></tr></thead><tbody>{templates.map((template) => <tr key={template.id} className="aegis-table-row border-b border-slate-800/80 text-xs"><td className="px-5 py-4"><span className="rounded border border-cyan-900/50 bg-cyan-950/20 px-2 py-1 font-mono text-[10px] font-bold text-cyan-300">{template.tag}</span></td><td className="max-w-md px-5 py-4 text-slate-300"><p className="truncate">{template.desc || '—'}</p><p className="mt-1 truncate font-mono text-[10px] text-slate-600">{template.prompt}</p></td><td className="whitespace-nowrap px-5 py-4 font-mono text-[10px] text-slate-500">{formatTimestamp(template.update_time)}</td><td className="px-5 py-4"><div className="flex justify-end gap-1"><button type="button" aria-label={`View ${template.tag}`} onClick={() => openView(template)} className="aegis-btn aegis-btn--secondary aegis-btn--icon p-2"><Eye className="h-3.5 w-3.5" /></button><button type="button" aria-label={`Edit ${template.tag}`} onClick={() => openEdit(template)} className="aegis-btn aegis-btn--secondary aegis-btn--icon p-2"><Pencil className="h-3.5 w-3.5" /></button><button type="button" aria-label={`Delete ${template.tag}`} onClick={() => void deleteTemplate(template)} className="aegis-btn aegis-btn--danger aegis-btn--icon p-2"><Trash2 className="h-3.5 w-3.5" /></button></div></td></tr>)}</tbody></table> : null}
          </div>
        </section>
      </div>

      {dialogMode ? <div role="presentation" className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"><div role="dialog" aria-modal="true" aria-labelledby="template-dialog-title" className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-slate-700 bg-[#05080F] shadow-2xl"><header className="flex items-start justify-between gap-4 border-b border-slate-800 px-5 py-4"><div><p className="font-mono text-[10px] font-bold tracking-[0.16em] text-cyan-400">{dialogMode === 'view' ? 'TEMPLATE DETAILS' : dialogMode === 'edit' ? 'UPDATE TEMPLATE' : 'NEW TEMPLATE'}</p><h2 id="template-dialog-title" className="mt-1 text-base font-semibold text-white">{dialogMode === 'view' ? selected?.tag : dialogMode === 'edit' ? 'Update prompt template' : 'Create prompt template'}</h2></div><button type="button" aria-label="Close template dialog" onClick={closeDialog} className="rounded p-2 text-slate-500 hover:bg-slate-800 hover:text-white"><X className="h-4 w-4" /></button></header>{isEditor ? <form onSubmit={saveTemplate} className="p-5"><label className="block text-[10px] font-mono font-bold tracking-widest text-slate-500">TAG<input aria-label="Template tag" value={draft.tag} onChange={(event) => updateDraft('tag', event.target.value)} maxLength={64} required className="mt-1.5 w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-sm text-white outline-none focus:border-cyan-500" placeholder="Incident response" /></label><label className="mt-4 block text-[10px] font-mono font-bold tracking-widest text-slate-500">DESCRIPTION<textarea aria-label="Template description" value={draft.desc} onChange={(event) => updateDraft('desc', event.target.value)} maxLength={512} rows={2} className="mt-1.5 w-full resize-none rounded border border-slate-800 bg-[#020408] px-3 py-2 text-sm text-white outline-none focus:border-cyan-500" placeholder="Optional summary" /></label><label className="mt-4 block text-[10px] font-mono font-bold tracking-widest text-slate-500">PROMPT<textarea aria-label="Template prompt" value={draft.prompt} onChange={(event) => updateDraft('prompt', event.target.value)} maxLength={20000} rows={12} required className="mt-1.5 w-full resize-y rounded border border-slate-800 bg-[#020408] px-3 py-2 font-mono text-xs leading-relaxed text-white outline-none focus:border-cyan-500" placeholder="Write the prompt to add to chat…" /></label><div className="mt-5 flex justify-end gap-2"><button type="button" onClick={closeDialog} className="rounded border border-slate-700 px-3 py-2 text-xs font-bold text-slate-300 hover:bg-slate-800">Cancel</button><button disabled={saving} type="submit" className="inline-flex items-center gap-1.5 rounded bg-cyan-500 px-3 py-2 text-xs font-bold text-white hover:bg-cyan-600 disabled:bg-slate-800"><Save className="h-3.5 w-3.5" />{saving ? 'Saving…' : 'Save template'}</button></div></form> : <div className="space-y-5 p-5"><div><p className="font-mono text-[10px] font-bold tracking-widest text-slate-500">DESCRIPTION</p><p className="mt-2 text-sm text-slate-200">{selected?.desc || '—'}</p></div><div><p className="font-mono text-[10px] font-bold tracking-widest text-slate-500">PROMPT</p><pre className="mt-2 whitespace-pre-wrap break-words rounded border border-slate-800 bg-[#020408] p-4 font-mono text-xs leading-relaxed text-slate-300">{selected?.prompt}</pre></div><div className="grid grid-cols-2 gap-4 font-mono text-[10px] text-slate-500"><p>CREATED<br /><span className="text-slate-300">{selected ? formatTimestamp(selected.create_time) : ''}</span></p><p>UPDATED<br /><span className="text-slate-300">{selected ? formatTimestamp(selected.update_time) : ''}</span></p></div></div>}</div></div> : null}
    </main>
  );
}
