import type { ReactNode } from 'react';

export function StatCard({
  label,
  value,
  sub,
  accent = '#38E1FF',
  icon,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  accent?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="glass glass-hover p-4 relative overflow-hidden">
      <div
        className="absolute -right-6 -top-6 h-20 w-20 rounded-full blur-2xl opacity-30"
        style={{ background: accent }}
      />
      <div className="flex items-center justify-between">
        <div className="eyebrow">{label}</div>
        {icon && <div style={{ color: accent }}>{icon}</div>}
      </div>
      <div className="mt-2 text-2xl font-semibold font-display" style={{ color: '#EAF2FF' }}>
        {value}
      </div>
      {sub && <div className="mt-1 text-xs text-[color:var(--ink-lo)]">{sub}</div>}
    </div>
  );
}

export function ProgressBar({ ratio, color = '#38E1FF' }: { ratio: number; color?: string }) {
  const pct = Math.max(0, Math.min(1, ratio)) * 100;
  return (
    <div className="h-2 w-full rounded-full bg-white/5 overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${color}, ${color}cc)`, boxShadow: `0 0 12px -2px ${color}` }}
      />
    </div>
  );
}
