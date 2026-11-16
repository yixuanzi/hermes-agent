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
  title = '尚无扫描记录',
  message = '当前环境还没有生成扫描快照，此页面依赖扫描结果。请先运行 Environment Scan 生成 mapped/scorecard/gap 报告。',
}: EmptyScanProps) {
  return (
    <div className="glass p-8 anim-fade-up">
      <div className="max-w-xl mx-auto text-center space-y-5">
        <div className="mx-auto h-14 w-14 rounded-2xl flex items-center justify-center glow-cyan anim-pulse" style={{ background: 'linear-gradient(150deg,#0C2436,#08131F)' }}>
          <AlertCircle className="h-7 w-7" style={{ color: '#FFC24B' }} />
        </div>
        <div>
          <h2 className="text-xl font-semibold font-display text-gradient">{title}</h2>
          <p className="mt-2 text-sm text-[color:var(--ink-mid)] leading-relaxed">{message}</p>
        </div>
        {onRunScan ? (
          <button className="btn-primary" disabled={!!scanPending} onClick={onRunScan}>
            <Radar className={`h-4 w-4 ${scanPending ? 'animate-spin' : ''}`} />
            {scanPending ? '扫描中…' : 'Run Environment Scan'}
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
