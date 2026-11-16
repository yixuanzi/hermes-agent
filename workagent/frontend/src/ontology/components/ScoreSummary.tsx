export function ScoreSummary({ overview }: { overview?: any }) {
  const items = [
    { label: 'Latest Scan', value: overview?.latest_scan_id || 'N/A' },
    { label: 'Completeness', value: overview?.score != null ? `${overview.score}/100` : 'N/A' },
    { label: 'Satisfied', value: overview?.status_counts?.satisfied ?? 0 },
    { label: 'Partial', value: overview?.status_counts?.partial ?? 0 },
    { label: 'Missing', value: overview?.status_counts?.missing ?? 0 },
  ];
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-4">
      {items.map((item) => (
        <div key={item.label} className="card">
          <div className="text-xs uppercase tracking-wider text-slate-500 mb-2">{item.label}</div>
          <div className="text-xl font-semibold break-all">{item.value}</div>
        </div>
      ))}
    </div>
  );
}
