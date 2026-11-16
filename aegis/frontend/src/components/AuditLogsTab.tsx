import { FormEvent, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { ChevronLeft, ChevronRight, RefreshCw, RotateCcw, Search } from 'lucide-react';

import { ApiError, alertApiError, fetchJSON } from '../lib/api';
import { DelegateAuditPage } from '../types';
import TaskAuditLogsTab from './TaskAuditLogsTab';

interface AuditLogsTabProps {
  onAuthExpired?: () => void;
}

type Filters = Record<string, string>;
type BusyAction = 'refresh' | 'search' | 'reset' | 'page-prev' | 'page-next' | null;
type AuditView = 'delegate' | 'task';

const EMPTY_FILTERS: Filters = {
  id: '', platform: '', user_id: '', user_name: '', agent_name: '', goal: '',
  session_id: '', status: '', is_loop: '', is_delegate_output: '',
  timestamp_from: '', timestamp_to: '',
};

const TEXT_FILTERS = [
  ['id', 'Audit ID'], ['platform', 'Platform'], ['user_id', 'User ID'],
  ['user_name', 'User Name'], ['agent_name', 'Agent Name'], ['goal', 'Goal'],
  ['session_id', 'Session ID'],
] as const;

export default function AuditLogsTab({ onAuthExpired }: AuditLogsTabProps) {
  const [activeView, setActiveView] = useState<AuditView>('delegate');
  const auditTabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [draftFilters, setDraftFilters] = useState<Filters>(EMPTY_FILTERS);
  const [activeFilters, setActiveFilters] = useState<Filters>(EMPTY_FILTERS);
  const [data, setData] = useState<DelegateAuditPage>({ logs: [], total: 0, page: 1, page_size: 50 });
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [expandedId, setExpandedId] = useState('');
  const requestSequence = useRef(0);
  const busy = busyAction !== null;

  async function load(page: number, filters: Filters = activeFilters, action: Exclude<BusyAction, null> = 'refresh') {
    const requestId = ++requestSequence.current;
    setBusyAction(action);
    const params = new URLSearchParams({ page: String(page), page_size: '50' });
    Object.entries(filters).forEach(([key, value]) => {
      if (value.trim()) params.set(key, value.trim());
    });
    try {
      const response = await fetchJSON<DelegateAuditPage>(`/api/audit/a2a-delegates?${params.toString()}`);
      if (requestId === requestSequence.current) setData(response);
    } catch (error) {
      if (requestId !== requestSequence.current) return;
      if (error instanceof ApiError && error.status === 401) {
        onAuthExpired?.();
        return;
      }
      alertApiError(error, 'Failed to load audit logs.');
    } finally {
      if (requestId === requestSequence.current) setBusyAction(null);
    }
  }

  useEffect(() => { void load(1, EMPTY_FILTERS); }, []);

  function search(event: FormEvent) {
    event.preventDefault();
    const next = { ...draftFilters };
    setActiveFilters(next);
    void load(1, next, 'search');
  }

  function reset() {
    setDraftFilters(EMPTY_FILTERS);
    setActiveFilters(EMPTY_FILTERS);
    void load(1, EMPTY_FILTERS, 'reset');
  }

  const lastPage = Math.max(1, Math.ceil(data.total / data.page_size));

  function selectView(view: AuditView) {
    setActiveView(view);
  }

  function handleAuditTabKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, view: AuditView) {
    const views: AuditView[] = ['delegate', 'task'];
    const currentIndex = views.indexOf(view);
    let nextIndex = currentIndex;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % views.length;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + views.length) % views.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = views.length - 1;
    if (nextIndex === currentIndex) return;
    event.preventDefault();
    const nextView = views[nextIndex];
    selectView(nextView);
    auditTabRefs.current[nextIndex]?.focus();
  }

  return (
    <main className="aegis-admin-page" aria-labelledby="audit-logs-heading">
      <div className="aegis-admin-page__inner">
        <header className="aegis-page-intro">
          <div>
            <h1 id="audit-logs-heading" className="aegis-page-intro__title">Audit Logs</h1>
            <p className="aegis-page-intro__description">Search A2A delegate outcomes and main-agent task execution traces.</p>
          </div>
          <div className="aegis-page-intro__badge"><span className="aegis-page-intro__badge-label">Scope:</span> delegate + task ledgers</div>
        </header>

        <div className="aegis-page-tabs aegis-page-tabs--compact" role="tablist" aria-label="Audit log views">
          <button
            ref={(element) => { auditTabRefs.current[0] = element; }}
            id="delegate-audit-tab"
            type="button"
            role="tab"
            tabIndex={activeView === 'delegate' ? 0 : -1}
            aria-selected={activeView === 'delegate'}
            aria-controls="delegate-audit-panel"
            onClick={() => selectView('delegate')}
            onKeyDown={(event) => handleAuditTabKeyDown(event, 'delegate')}
            className="aegis-page-tab"
          >
            Delegate audit
          </button>
          <button
            ref={(element) => { auditTabRefs.current[1] = element; }}
            id="task-audit-tab"
            type="button"
            role="tab"
            tabIndex={activeView === 'task' ? 0 : -1}
            aria-selected={activeView === 'task'}
            aria-controls="task-audit-panel"
            onClick={() => selectView('task')}
            onKeyDown={(event) => handleAuditTabKeyDown(event, 'task')}
            className="aegis-page-tab"
          >
            Task audit
          </button>
        </div>

        {activeView === 'task' ? <TaskAuditLogsTab onAuthExpired={onAuthExpired} /> : <section id="delegate-audit-panel" className="aegis-page-content" role="tabpanel" aria-labelledby="delegate-audit-tab">
          <header className="aegis-page-content__header aegis-page-content__header--compact">
            <div>
              <h2 id="audit-evidence-heading" className="aegis-page-content__title">Delegate Evidence</h2>
              <p className="aegis-page-content__description">Filter recorded authorization decisions and execution details.</p>
            </div>
            <button type="button" aria-busy={busyAction === 'refresh'} onClick={() => void load(data.page, activeFilters, 'refresh')} className={`aegis-btn aegis-btn--secondary flex items-center gap-2 px-3 py-2 ${busyAction === 'refresh' ? 'aegis-btn--busy' : ''}`}>
              <RefreshCw className={`h-4 w-4 ${busyAction === 'refresh' ? 'animate-spin' : ''}`} /> Refresh
            </button>
          </header>

          <form onSubmit={search} className="aegis-page-filter-bar grid items-end gap-2 border-b border-slate-800 md:grid-cols-3 xl:grid-cols-6">
            {TEXT_FILTERS.map(([key, label]) => <input key={key} aria-label={`Audit ${label}`} value={draftFilters[key]} onChange={(event) => setDraftFilters((current) => ({ ...current, [key]: event.target.value }))} placeholder={label} className="aegis-page-field h-10 px-3 py-2 text-xs" />)}
            <select aria-label="Audit Status" value={draftFilters.status} onChange={(event) => setDraftFilters((current) => ({ ...current, status: event.target.value }))} className="aegis-page-field h-10 px-3 py-2 text-xs"><option value="">Any status</option><option value="succ">succ</option><option value="fail">fail</option><option value="auth_denied">auth_denied</option></select>
            <select aria-label="Audit Loop" value={draftFilters.is_loop} onChange={(event) => setDraftFilters((current) => ({ ...current, is_loop: event.target.value }))} className="aegis-page-field h-10 px-3 py-2 text-xs"><option value="">Any loop mode</option><option value="true">Loop</option><option value="false">Non-loop</option></select>
            <select aria-label="Audit Delegate Output" value={draftFilters.is_delegate_output} onChange={(event) => setDraftFilters((current) => ({ ...current, is_delegate_output: event.target.value }))} className="aegis-page-field h-10 px-3 py-2 text-xs"><option value="">Any output mode</option><option value="true">Output enabled</option><option value="false">Output disabled</option></select>
            <label className="flex flex-col justify-end text-[10px] text-slate-500">From UTC<input aria-label="Audit Timestamp From" type="datetime-local" value={draftFilters.timestamp_from.replace(':00Z', '')} onChange={(event) => setDraftFilters((current) => ({ ...current, timestamp_from: event.target.value ? `${event.target.value}:00Z` : '' }))} className="aegis-page-field mt-1 h-10 w-full px-3 py-2 text-xs" /></label>
            <label className="flex flex-col justify-end text-[10px] text-slate-500">To UTC<input aria-label="Audit Timestamp To" type="datetime-local" value={draftFilters.timestamp_to.replace(':00Z', '')} onChange={(event) => setDraftFilters((current) => ({ ...current, timestamp_to: event.target.value ? `${event.target.value}:00Z` : '' }))} className="aegis-page-field mt-1 h-10 w-full px-3 py-2 text-xs" /></label>
            <div className="flex items-end gap-2"><button type="submit" aria-busy={busyAction === 'search'} className={`aegis-btn aegis-btn--primary flex items-center gap-1 px-3 py-2 ${busyAction === 'search' ? 'aegis-btn--busy' : ''}`}><Search className="h-3.5 w-3.5" /> Search</button><button type="button" aria-busy={busyAction === 'reset'} onClick={reset} className={`aegis-btn aegis-btn--secondary flex items-center gap-1 px-3 py-2 ${busyAction === 'reset' ? 'aegis-btn--busy' : ''}`}><RotateCcw className="h-3.5 w-3.5" /> Reset</button></div>
          </form>

          <div className="aegis-page-content__body overflow-auto">
            <table className="w-full border-collapse text-left text-xs"><thead className="sticky top-0 bg-[#03060C] font-mono text-[9px] uppercase tracking-wider text-slate-500"><tr><th className="p-3">Timestamp</th><th className="p-3">Caller</th><th className="p-3">Agent</th><th className="p-3">Goal</th><th className="p-3">Session</th><th className="p-3">Options</th><th className="p-3">Status</th></tr></thead>
              <tbody className="divide-y divide-slate-800/60">{data.logs.length === 0 ? <tr><td colSpan={7} className="p-10 text-center text-slate-500">No audit logs match the active filters.</td></tr> : data.logs.map((log) => <tr key={log.id} className="aegis-table-row align-top"><td className="whitespace-nowrap p-3 font-mono text-slate-400"><div>{log.timestamp}</div><div className="mt-1 text-[9px] text-slate-600">{log.id}</div></td><td className="p-3"><div>{log.platform || '—'} / {log.user_name || '—'}</div><div className="font-mono text-[10px] text-slate-500">{log.user_id || '—'}</div></td><td className="p-3 font-mono text-cyan-400">{log.agent_name}</td><td className="max-w-md p-3"><button type="button" aria-expanded={expandedId === log.id} className={`aegis-btn px-1 py-0.5 text-left ${expandedId === log.id ? 'aegis-btn--secondary aegis-btn--selected whitespace-pre-wrap' : 'aegis-btn--ghost line-clamp-2'}`} onClick={() => setExpandedId((current) => current === log.id ? '' : log.id)}>{log.goal || '—'}</button></td><td className="p-3 font-mono text-[10px]">{log.session_id || '—'}</td><td className="p-3 font-mono text-[10px]">loop={String(log.is_loop)}<br />output={String(log.is_delegate_output)}</td><td className="p-3"><span className={`aegis-status-badge ${log.status === 'succ' ? 'aegis-status-badge--success' : 'aegis-status-badge--danger'}`}>{log.status}</span></td></tr>)}</tbody>
            </table>
          </div>

          <footer className="flex items-center justify-between border-t border-slate-800 px-4 py-3 text-xs text-slate-400"><span>{data.total} results · page {data.page} of {lastPage}</span><div className="flex gap-2"><button type="button" aria-label="Previous audit page" disabled={data.page <= 1 || busy} aria-busy={busyAction === 'page-prev'} onClick={() => void load(data.page - 1, activeFilters, 'page-prev')} className={`aegis-btn aegis-btn--secondary aegis-btn--icon p-2 ${busyAction === 'page-prev' ? 'aegis-btn--busy' : ''}`}><ChevronLeft className="h-4 w-4" /></button><button type="button" aria-label="Next audit page" disabled={data.page >= lastPage || busy} aria-busy={busyAction === 'page-next'} onClick={() => void load(data.page + 1, activeFilters, 'page-next')} className={`aegis-btn aegis-btn--secondary aegis-btn--icon p-2 ${busyAction === 'page-next' ? 'aegis-btn--busy' : ''}`}><ChevronRight className="h-4 w-4" /></button></div></footer>
        </section>
        }
      </div>
    </main>
  );
}
