export function DomainFilter({ domains, selected, onSelect }: { domains: string[]; selected: string; onSelect: (v: string) => void }) {
  return (
    <div className="card">
      <div className="flex flex-wrap gap-2">
        <button className={`px-3 py-1.5 rounded-md text-sm border ${selected === 'ALL' ? 'border-cyan-400 text-cyan-300 bg-cyan-400/10' : 'border-slate-700 text-slate-400'}`} onClick={() => onSelect('ALL')}>ALL</button>
        {domains.map((domain) => (
          <button key={domain} className={`px-3 py-1.5 rounded-md text-sm border ${selected === domain ? 'border-cyan-400 text-cyan-300 bg-cyan-400/10' : 'border-slate-700 text-slate-400'}`} onClick={() => onSelect(domain)}>{domain}</button>
        ))}
      </div>
    </div>
  );
}
