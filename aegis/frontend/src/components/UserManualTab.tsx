import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertTriangle, BookOpen, ChevronRight, FileText, LoaderCircle, RefreshCw } from 'lucide-react';
import { ApiError, fetchJSON, getApiErrorMessage } from '../lib/api';
import type { UserManual, UserManualSummary } from '../types';

interface UserManualListResponse {
  manuals: UserManualSummary[];
  default_manual_id: string | null;
}

function MarkdownManual({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => <h1 className="border-b border-cyan-950/70 pb-4 font-mono text-2xl font-bold tracking-tight text-cyan-100">{children}</h1>,
        h2: ({ children }) => <h2 className="mt-9 border-l-2 border-cyan-500 pl-3 text-lg font-bold text-slate-100">{children}</h2>,
        h3: ({ children }) => <h3 className="mt-6 text-base font-semibold text-slate-200">{children}</h3>,
        p: ({ children }) => <p className="mt-4 max-w-4xl leading-7 text-slate-300">{children}</p>,
        ul: ({ children }) => <ul className="mt-3 list-disc space-y-2 pl-6 text-slate-300 marker:text-cyan-500">{children}</ul>,
        ol: ({ children }) => <ol className="mt-3 list-decimal space-y-2 pl-6 text-slate-300 marker:text-cyan-400">{children}</ol>,
        li: ({ children }) => <li className="pl-1 leading-6">{children}</li>,
        blockquote: ({ children }) => <blockquote className="my-5 border-l-2 border-amber-500/70 bg-amber-950/10 px-4 py-3 text-slate-300">{children}</blockquote>,
        code: ({ className, children }) => className
          ? <code className={`${className} block overflow-x-auto rounded border border-slate-800 bg-[#010309] p-4 font-mono text-xs leading-6 text-cyan-100`}>{children}</code>
          : <code className="rounded bg-cyan-950/30 px-1.5 py-0.5 font-mono text-[0.9em] text-cyan-200">{children}</code>,
        table: ({ children }) => <div className="my-5 overflow-x-auto rounded border border-slate-800"><table className="min-w-full text-left text-sm">{children}</table></div>,
        th: ({ children }) => <th className="border-b border-slate-700 bg-slate-900/80 px-3 py-2 font-semibold text-cyan-100">{children}</th>,
        td: ({ children }) => <td className="border-b border-slate-800 px-3 py-2 align-top leading-6 text-slate-300">{children}</td>,
        a: ({ href, children }) => {
          const external = Boolean(href && /^https?:\/\//i.test(href));
          return <a href={href} target={external ? '_blank' : undefined} rel={external ? 'noreferrer' : undefined} className="text-cyan-400 underline decoration-cyan-700 underline-offset-4 hover:text-cyan-200">{children}</a>;
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

export default function UserManualTab() {
  const [manuals, setManuals] = useState<UserManualSummary[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [manual, setManual] = useState<UserManual | null>(null);
  const [directoryLoading, setDirectoryLoading] = useState(true);
  const [manualLoading, setManualLoading] = useState(false);
  const [directoryError, setDirectoryError] = useState('');
  const [manualError, setManualError] = useState('');
  const requestId = useRef(0);

  async function loadManual(manualId: string) {
    const currentRequest = ++requestId.current;
    setSelectedId(manualId);
    setManual(null);
    setManualLoading(true);
    setManualError('');
    try {
      const response = await fetchJSON<UserManual>(`/api/user-manuals/${encodeURIComponent(manualId)}`);
      if (currentRequest === requestId.current) {
        setManual(response);
      }
    } catch (error) {
      if (currentRequest === requestId.current) {
        setManualError(error instanceof ApiError && error.status === 404
          ? 'This manual is no longer available. Refresh the directory and choose another manual.'
          : getApiErrorMessage(error, 'Unable to load this user manual.'));
      }
    } finally {
      if (currentRequest === requestId.current) {
        setManualLoading(false);
      }
    }
  }

  async function loadDirectory() {
    setDirectoryLoading(true);
    setDirectoryError('');
    try {
      const response = await fetchJSON<UserManualListResponse>('/api/user-manuals');
      setManuals(response.manuals);
      if (!response.manuals.length) {
        requestId.current += 1;
        setSelectedId('');
        setManual(null);
        setManualError('');
        return;
      }
      const defaultId = response.manuals.some((item) => item.id === response.default_manual_id)
        ? response.default_manual_id!
        : response.manuals[0].id;
      await loadManual(defaultId);
    } catch (error) {
      setDirectoryError(getApiErrorMessage(error, 'Unable to load the user manual directory.'));
      setManuals([]);
      setManual(null);
    } finally {
      setDirectoryLoading(false);
    }
  }

  useEffect(() => {
    void loadDirectory();
  }, []);

  return (
    <section aria-labelledby="user-manual-heading" className="flex h-full min-h-0 flex-col overflow-hidden">
      <header className="aegis-manual-header shrink-0 border-b border-slate-800 bg-[#03060C]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 font-mono text-[10px] font-bold tracking-[0.18em] text-cyan-400"><BookOpen className="h-4 w-4" /> USER PROFILE / REFERENCE LIBRARY</div>
            <h1 id="user-manual-heading" className="mt-2 text-lg font-bold tracking-tight text-white">User Manual</h1>
            <p className="mt-1 text-xs text-slate-500">Browse the Aegis operating manuals bundled with this deployment.</p>
          </div>
          <button type="button" onClick={() => void loadDirectory()} disabled={directoryLoading} className="inline-flex items-center gap-2 rounded border border-slate-700 bg-[#070B12] px-3 py-2 font-mono text-[10px] font-bold tracking-wide text-slate-300 transition hover:border-cyan-700 hover:text-cyan-300 disabled:cursor-not-allowed disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${directoryLoading ? 'animate-spin' : ''}`} /> Refresh directory
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <aside aria-label="User manual directory" className="flex max-h-56 shrink-0 flex-col border-b border-slate-800 bg-[#05080F] lg:max-h-none lg:w-72 lg:border-b-0 lg:border-r">
          <div className="border-b border-slate-800 px-4 py-3 font-mono text-[10px] font-bold tracking-[0.16em] text-slate-500">MANUAL DIRECTORY</div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {directoryLoading ? <div className="flex items-center gap-2 px-3 py-4 text-xs text-slate-500"><LoaderCircle className="h-4 w-4 animate-spin" /> Loading manuals…</div> : null}
            {!directoryLoading && directoryError ? <div className="p-3"><div className="flex gap-2 text-xs leading-5 text-rose-300"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{directoryError}</div><button type="button" onClick={() => void loadDirectory()} className="mt-3 text-xs text-cyan-400 hover:text-cyan-200">Try again</button></div> : null}
            {!directoryLoading && !directoryError && manuals.length === 0 ? <div className="p-3 text-xs leading-5 text-slate-500">No published manuals are available in this deployment.</div> : null}
            {!directoryLoading && !directoryError ? manuals.map((item) => {
              const active = item.id === selectedId;
              return <button key={item.id} type="button" aria-current={active ? 'page' : undefined} onClick={() => void loadManual(item.id)} className={`mb-1 flex w-full items-center gap-2 rounded border px-3 py-2.5 text-left transition ${active ? 'border-cyan-800/80 bg-cyan-950/25 text-cyan-100 shadow-[inset_2px_0_0_#06b6d4]' : 'border-transparent text-slate-400 hover:border-slate-800 hover:bg-slate-900/60 hover:text-slate-200'}`}><FileText className={`h-3.5 w-3.5 shrink-0 ${active ? 'text-cyan-400' : 'text-slate-600'}`} /><span className="flex-1 text-xs leading-5">{item.title}</span><ChevronRight className={`h-3.5 w-3.5 shrink-0 ${active ? 'text-cyan-400' : 'text-slate-700'}`} /></button>;
            }) : null}
          </div>
        </aside>

        <article aria-live="polite" className="aegis-manual-article min-h-0 flex-1 overflow-y-auto bg-[radial-gradient(circle_at_top_right,rgba(8,145,178,0.08),transparent_32%),#020408]">
          {manualLoading ? <div className="flex h-full min-h-52 items-center justify-center gap-3 font-mono text-xs text-slate-500"><LoaderCircle className="h-4 w-4 animate-spin text-cyan-500" /> Retrieving manual content…</div> : null}
          {!manualLoading && manualError ? <div className="mx-auto max-w-2xl rounded border border-rose-900/60 bg-rose-950/10 p-5"><div className="flex gap-3 text-sm leading-6 text-rose-200"><AlertTriangle className="mt-1 h-4 w-4 shrink-0" />{manualError}</div><button type="button" onClick={() => selectedId && void loadManual(selectedId)} className="mt-4 text-xs font-semibold text-cyan-400 hover:text-cyan-200">Retry manual</button></div> : null}
          {!manualLoading && !manualError && manual ? <div className="mx-auto max-w-5xl pb-12"><MarkdownManual content={manual.content} /></div> : null}
          {!manualLoading && !manualError && !manual && !directoryLoading && !directoryError && manuals.length > 0 ? <div className="flex h-full min-h-52 items-center justify-center text-sm text-slate-500">Select a manual from the directory to begin reading.</div> : null}
        </article>
      </div>
    </section>
  );
}
