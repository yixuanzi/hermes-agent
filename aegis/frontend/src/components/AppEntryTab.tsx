import { AppWindow, ArrowUpRight, CircleAlert, ExternalLink, LoaderCircle, RefreshCw, ShieldCheck } from 'lucide-react';

import type { AppEntry, AppEntryList } from '../types';

interface AppEntryTabProps {
  directory: AppEntryList | null;
  loading: boolean;
  error: string;
  loaded: boolean;
  onRefresh: () => Promise<void>;
}

function formatExpiry(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(date);
}

function statusLabel(status: AppEntry['subscription_status']): string {
  return status === 'expiring' ? 'Expiring soon' : 'Active';
}

function AppEntryCard({ entry }: { entry: AppEntry }) {
  const entryUrl = entry.entry_url?.trim() || null;
  const hasEntry = Boolean(entryUrl);
  const cardClassName = `group relative flex min-h-56 flex-col overflow-hidden rounded-2xl border p-5 text-left transition-all duration-200 ${
    hasEntry
      ? 'border-slate-800 bg-[#05080F] hover:-translate-y-1 hover:border-cyan-700/80 hover:bg-[#080C14] hover:shadow-[0_16px_40px_rgba(6,182,212,0.12)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400'
      : 'cursor-not-allowed border-slate-800/70 bg-[#03060C] opacity-75'
  }`;

  const content = (
    <>
      <div className="absolute -right-8 -top-8 h-24 w-24 rounded-full bg-cyan-500/10 opacity-60 blur-2xl transition-opacity duration-200 group-hover:opacity-100" />
      <div className="relative flex items-start justify-between gap-3">
        <div className={`flex h-11 w-11 items-center justify-center rounded-xl border ${hasEntry ? 'border-cyan-800/70 bg-cyan-950/35 text-cyan-300' : 'border-slate-700 bg-slate-900/50 text-slate-500'}`}>
          <AppWindow className="h-5 w-5" />
        </div>
        {hasEntry ? <ArrowUpRight className="h-4 w-4 text-slate-600 transition-colors group-hover:text-cyan-300" /> : <CircleAlert className="h-4 w-4 text-amber-400/80" />}
      </div>

      <div className="relative mt-5 flex-1">
        <div className="font-mono text-[10px] font-bold tracking-[0.18em] text-cyan-400/80">{entry.product_service_code}</div>
        <h2 className="mt-2 line-clamp-2 text-base font-bold leading-6 text-white">{entry.product_service_name}</h2>
        <p className="mt-2 font-mono text-[10px] text-slate-600">SUBSCRIPTION · {entry.subscription_no}</p>
      </div>

      <div className="relative mt-5 flex items-center justify-between gap-2 border-t border-slate-800/80 pt-3">
        <span className={`aegis-status-badge ${entry.subscription_status === 'expiring' ? 'aegis-status-badge--warning' : 'aegis-status-badge--success'}`}>
          {statusLabel(entry.subscription_status)}
        </span>
        <span className="font-mono text-[10px] text-slate-500">Until {formatExpiry(entry.effective_to)}</span>
      </div>
      <div className={`relative mt-3 flex items-center gap-1.5 text-[11px] font-semibold ${hasEntry ? 'text-cyan-300' : 'text-amber-300/80'}`}>
        {hasEntry ? <><ExternalLink className="h-3.5 w-3.5" /> Open application</> : 'Entry not configured'}
      </div>
    </>
  );

  if (!hasEntry) {
    return <article aria-disabled="true" data-testid="app-entry-card-disabled" className={cardClassName}>{content}</article>;
  }

  return (
    <a
      aria-label={`Open ${entry.product_service_name}`}
      className={cardClassName}
      data-testid="app-entry-card"
      href={entryUrl!}
      rel="noopener noreferrer"
      target="_blank"
    >
      {content}
    </a>
  );
}

export default function AppEntryTab({ directory, loading, error, loaded, onRefresh }: AppEntryTabProps) {
  return (
    <section aria-labelledby="app-entry-heading" className="flex h-full min-h-0 flex-col overflow-hidden">
      <header className="shrink-0 border-b border-slate-800 bg-[#03060C] p-4 lg:p-5">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
          <div>
            <div className="flex items-center gap-2 font-mono text-[10px] font-bold tracking-[0.18em] text-cyan-400">
              <AppWindow className="h-4 w-4" /> ORGANIZATION / APPLICATION DIRECTORY
            </div>
            <h1 id="app-entry-heading" className="mt-2 text-xl font-bold tracking-tight text-white">App Entry</h1>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">Access the applications and services available to this organization from one operational launch surface.</p>
          </div>
          <button
            type="button"
            aria-busy={loading}
            disabled={loading}
            onClick={() => void onRefresh()}
            className="aegis-btn aegis-btn--secondary inline-flex shrink-0 items-center gap-2 self-start px-3 py-2 text-xs"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh entries
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto bg-[radial-gradient(circle_at_top_right,rgba(8,145,178,0.1),transparent_34%),var(--aegis-bg)] p-4 lg:p-6">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
          <div className="flex flex-col gap-3 rounded-2xl border border-cyan-900/40 bg-cyan-950/10 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-cyan-400" />
              <div>
                <p className="text-xs font-semibold text-cyan-100">Organization-scoped access</p>
                <p className="mt-1 text-[11px] leading-5 text-slate-500">Only active and expiring subscriptions returned by the Portal are listed here.</p>
              </div>
            </div>
            <span className="font-mono text-[10px] font-bold tracking-wider text-cyan-300">ORG · {directory?.organization_code || 'CONNECTING'}</span>
          </div>

          {loading ? (
            <div className="flex min-h-64 items-center justify-center gap-3 rounded-2xl border border-slate-800 bg-[#05080F] font-mono text-xs text-slate-500">
              <LoaderCircle className="h-4 w-4 animate-spin text-cyan-400" /> Loading application directory…
            </div>
          ) : null}

          {!loading && error ? (
            <div className="rounded-2xl border border-rose-900/60 bg-rose-950/15 p-5" role="alert">
              <div className="flex items-start gap-3 text-sm leading-6 text-rose-200">
                <CircleAlert className="mt-1 h-4 w-4 shrink-0" />
                <span>{error}</span>
              </div>
              <button type="button" onClick={() => void onRefresh()} className="mt-4 text-xs font-semibold text-cyan-400 hover:text-cyan-200">Retry directory</button>
            </div>
          ) : null}

          {!loading && !error && loaded && directory?.entries.length === 0 ? (
            <div className="flex min-h-64 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-700 bg-[#05080F] px-6 text-center">
              <AppWindow className="h-8 w-8 text-slate-600" />
              <h2 className="mt-4 text-sm font-semibold text-slate-300">No subscribed applications</h2>
              <p className="mt-2 max-w-md text-xs leading-5 text-slate-500">No active or expiring subscriptions were found for this organization.</p>
            </div>
          ) : null}

          {!loading && !error && loaded && directory && directory.entries.length > 0 ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {directory.entries.map((entry) => <AppEntryCard key={entry.subscription_id} entry={entry} />)}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
