import { FormEvent, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, RefreshCw, RotateCcw, Search } from 'lucide-react';

import { ApiError, alertApiError, fetchJSON } from '../lib/api';
import { TaskAuditPage } from '../types';

interface TaskAuditLogsTabProps {
  onAuthExpired?: () => void;
}

type Filters = Record<string, string>;
type BusyAction = 'refresh' | 'search' | 'reset' | 'page-prev' | 'page-next' | null;

const EMPTY_FILTERS: Filters = {
  uid: '',
  uname: '',
  session_id: '',
  prompt: '',
  create_time_from: '',
  create_time_to: '',
};

const TEXT_FILTERS = [
  ['uid', 'UID'],
  ['uname', 'User Name'],
  ['session_id', 'Session ID'],
  ['prompt', 'Prompt'],
] as const;

function dateTimeLocalValue(value: string): string {
  return value.replace(/:00Z$/, '');
}

function utcFilterValue(value: string): string {
  return value ? `${value}:00Z` : '';
}

export default function TaskAuditLogsTab({ onAuthExpired }: TaskAuditLogsTabProps) {
  const [draftFilters, setDraftFilters] = useState<Filters>(EMPTY_FILTERS);
  const [activeFilters, setActiveFilters] = useState<Filters>(EMPTY_FILTERS);
  const [data, setData] = useState<TaskAuditPage>({ logs: [], total: 0, page: 1, page_size: 50 });
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [expandedId, setExpandedId] = useState('');
  const requestSequence = useRef(0);
  const busy = busyAction !== null;

  async function load(
    page: number,
    filters: Filters = activeFilters,
    action: Exclude<BusyAction, null> = 'refresh',
  ) {
    const requestId = ++requestSequence.current;
    setBusyAction(action);
    const params = new URLSearchParams({ page: String(page), page_size: '50' });
    Object.entries(filters).forEach(([key, value]) => {
      if (value.trim()) params.set(key, value.trim());
    });
    try {
      const response = await fetchJSON<TaskAuditPage>(`/api/audit/tasks?${params.toString()}`);
      if (requestId === requestSequence.current) setData(response);
    } catch (error) {
      if (requestId !== requestSequence.current) return;
      if (error instanceof ApiError && error.status === 401) {
        onAuthExpired?.();
        return;
      }
      alertApiError(error, 'Failed to load task audit logs.');
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

  function updateFilter(key: string, value: string) {
    setDraftFilters((current) => ({ ...current, [key]: value }));
  }

  const lastPage = Math.max(1, Math.ceil(data.total / data.page_size));

  return (
    <section className="aegis-page-content" role="tabpanel" aria-labelledby="task-audit-tab" id="task-audit-panel">
      <header className="aegis-page-content__header aegis-page-content__header--compact">
        <div>
          <h2 id="task-audit-heading" className="aegis-page-content__title">Task Evidence</h2>
          <p className="aegis-page-content__description">Search every main-agent task prompt and its creation time.</p>
        </div>
        <button
          type="button"
          aria-busy={busyAction === 'refresh'}
          onClick={() => void load(data.page, activeFilters, 'refresh')}
          className={`aegis-btn aegis-btn--secondary flex items-center gap-2 px-3 py-2 ${busyAction === 'refresh' ? 'aegis-btn--busy' : ''}`}
        >
          <RefreshCw className={`h-4 w-4 ${busyAction === 'refresh' ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </header>

      <form onSubmit={search} className="aegis-page-filter-bar grid items-end gap-2 border-b border-slate-800 md:grid-cols-3 xl:grid-cols-7">
        {TEXT_FILTERS.map(([key, label]) => (
          <input
            key={key}
            aria-label={`Task ${label}`}
            value={draftFilters[key]}
            onChange={(event) => updateFilter(key, event.target.value)}
            placeholder={label}
            className="aegis-page-field h-10 min-w-0 px-3 py-2 text-xs"
          />
        ))}
        {([
          ['create_time_from', 'Task Created From UTC'],
          ['create_time_to', 'Task Created To UTC'],
        ] as const).map(([key, label]) => (
          <label key={key} className="flex min-w-0 flex-col justify-end text-[10px] text-slate-500">
            {label.replace('Task ', '')}
            <input
              aria-label={label}
              type="datetime-local"
              value={dateTimeLocalValue(draftFilters[key])}
              onChange={(event) => updateFilter(key, utcFilterValue(event.target.value))}
              className="aegis-page-field mt-1 h-10 w-full px-3 py-2 text-xs"
            />
          </label>
        ))}
        <div className="flex min-w-0 max-w-full flex-wrap items-end gap-2">
          <button
            type="submit"
            aria-label="Search"
            title="Search"
            aria-busy={busyAction === 'search'}
            className={`aegis-btn aegis-btn--primary aegis-btn--icon h-10 w-10 shrink-0 p-2 ${busyAction === 'search' ? 'aegis-btn--busy' : ''}`}
          >
            <Search className="h-4 w-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Reset"
            title="Reset"
            aria-busy={busyAction === 'reset'}
            onClick={reset}
            className={`aegis-btn aegis-btn--secondary aegis-btn--icon h-10 w-10 shrink-0 p-2 ${busyAction === 'reset' ? 'aegis-btn--busy' : ''}`}
          >
            <RotateCcw className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </form>

      <div className="aegis-page-content__body overflow-auto">
        <table className="w-full min-w-[1120px] border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-[#03060C] font-mono text-[9px] uppercase tracking-wider text-slate-500">
            <tr>
              <th className="p-3">ID</th>
              <th className="p-3">UID</th>
              <th className="p-3">User Name</th>
              <th className="p-3">Session</th>
              <th className="p-3">Prompt</th>
              <th className="p-3">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {data.logs.length === 0 ? (
              <tr><td colSpan={6} className="p-10 text-center text-slate-500">No task audit logs match the active filters.</td></tr>
            ) : data.logs.map((log) => (
              <tr key={log.id} className="aegis-table-row align-top">
                <td className="whitespace-nowrap p-3 font-mono text-[10px] text-slate-500">{log.id}</td>
                <td className="p-3 font-mono text-cyan-400">{log.uid || '—'}</td>
                <td className="p-3">{log.uname || '—'}</td>
                <td className="p-3 font-mono text-[10px]">{log.session_id || '—'}</td>
                <td className="max-w-md p-3">
                  <button
                    type="button"
                    aria-expanded={expandedId === log.id}
                    className={`aegis-btn px-1 py-0.5 text-left ${expandedId === log.id ? 'aegis-btn--secondary aegis-btn--selected whitespace-pre-wrap' : 'aegis-btn--ghost line-clamp-2'}`}
                    onClick={() => setExpandedId((current) => current === log.id ? '' : log.id)}
                  >
                    {log.prompt || '—'}
                  </button>
                </td>
                <td className="whitespace-nowrap p-3 font-mono text-[10px] text-slate-500">{log.create_time}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <footer className="flex items-center justify-between border-t border-slate-800 px-4 py-3 text-xs text-slate-400">
        <span>{data.total} results · page {data.page} of {lastPage}</span>
        <div className="flex gap-2">
          <button type="button" aria-label="Previous task audit page" disabled={data.page <= 1 || busy} aria-busy={busyAction === 'page-prev'} onClick={() => void load(data.page - 1, activeFilters, 'page-prev')} className={`aegis-btn aegis-btn--secondary aegis-btn--icon p-2 ${busyAction === 'page-prev' ? 'aegis-btn--busy' : ''}`}>
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button type="button" aria-label="Next task audit page" disabled={data.page >= lastPage || busy} aria-busy={busyAction === 'page-next'} onClick={() => void load(data.page + 1, activeFilters, 'page-next')} className={`aegis-btn aegis-btn--secondary aegis-btn--icon p-2 ${busyAction === 'page-next' ? 'aegis-btn--busy' : ''}`}>
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </footer>
    </section>
  );
}
