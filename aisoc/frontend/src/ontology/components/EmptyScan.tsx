import { Radar, AlertCircle } from 'lucide-react';

interface EmptyScanProps {
  onRunScan?: () => void;
  scanPending?: boolean;
  scanError?: string | null;
  title?: string;
  message?: string;
}

export function EmptyScan({
  onRunScan,
  scanPending,
  scanError,
  title = 'No scan records yet',
  message = 'This environment has not generated a scan snapshot yet, and this page depends on scan results. Please run an Environment Scan first to generate the mapped/scorecard/gap reports.',
}: EmptyScanProps) {
  return (
    <div className="glass p-8 anim-fade-up">
      <div className="max-w-xl mx-auto text-center space-y-5">
        <div className="mx-auto h-14 w-14 rounded-2xl flex items-center justify-center glow-cyan anim-pulse" style={{ background: 'color-mix(in srgb, var(--accent-cyan) 12%, var(--bg-elevated))' }}>
          <AlertCircle className="h-7 w-7" style={{ color: '#fbbf24' }} />
        </div>
        <div>
          <h2 className="text-xl font-semibold font-display text-gradient">{title}</h2>
          <p className="mt-2 text-sm text-[color:var(--ink-mid)] leading-relaxed">{message}</p>
        </div>
        {onRunScan ? (
          <button className="btn-primary" disabled={!!scanPending} onClick={onRunScan}>
            <Radar className={`h-4 w-4 ${scanPending ? 'animate-spin' : ''}`} />
            {scanPending ? 'Scanning…' : 'Run Environment Scan'}
          </button>
        ) : null}
        {scanError ? (
          <div className="text-xs text-[color:var(--st-missing)] font-mono">{scanError}</div>
        ) : null}
      </div>
    </div>
  );
}

export function isNoScan404(error: unknown): boolean {
  const anyErr = error as { response?: { status?: number } } | undefined;
  return anyErr?.response?.status === 404;
}
