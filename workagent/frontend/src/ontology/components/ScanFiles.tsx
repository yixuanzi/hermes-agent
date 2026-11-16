export function ScanFiles({ files }: { files?: string[] }) {
  return (
    <div className="card">
      <div className="text-xs uppercase tracking-wider text-slate-500 mb-2">Generated Files</div>
      <div className="space-y-2 text-xs text-slate-300 break-all">
        {(files || []).map((f) => <div key={f} className="rounded border border-slate-800 p-2">{f}</div>)}
      </div>
    </div>
  );
}
