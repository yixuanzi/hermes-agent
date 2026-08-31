import { useMemo, useState, type ReactNode } from 'react';
import { Activity, AlertTriangle, Cpu, Play, Search, Server, ShieldCheck } from 'lucide-react';
import StarmappingTopology from './StarmappingTopology';
import { Agent, AppEntry, OverviewStats, StarmappingTopology as StarmappingTopologyData } from '../types';

interface OverviewTabProps {
  agents: Agent[];
  currentUtcTime: string;
  setTab: (tab: string) => void;
  stats: OverviewStats | null;
  statsError: string;
  topology: StarmappingTopologyData | null;
  topologyError: string;
  appEntries: AppEntry[];
  appEntriesLoading: boolean;
  appEntriesError: string;
  appEntriesLoaded: boolean;
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatVolumeChange(value: number | null): string {
  if (value === null) return 'No prior baseline';
  return `${value >= 0 ? '↑' : '↓'} ${Math.abs(value).toFixed(1)}% vs previous 7 days`;
}

function formatRateChange(value: number | null): string {
  if (value === null) return 'No prior baseline';
  return `${value >= 0 ? '+' : ''}${value.toFixed(1)} pp vs previous 7 days`;
}

export default function OverviewTab({
  agents,
  currentUtcTime,
  setTab,
  stats,
  statsError,
  topology,
  topologyError,
  appEntries,
  appEntriesLoading,
  appEntriesError,
  appEntriesLoaded,
}: OverviewTabProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedStatus, setSelectedStatus] = useState('All');
  const filteredAgents = useMemo(
    () => agents.filter((agent) => {
      const query = searchTerm.toLowerCase();
      return (
        (agent.name.toLowerCase().includes(query) || agent.description.toLowerCase().includes(query))
        && (selectedStatus === 'All' || agent.status === selectedStatus)
      );
    }),
    [agents, searchTerm, selectedStatus],
  );
  const successRate = stats?.success_rate ?? null;
  const successRatePercent = successRate === null ? 0 : successRate * 100;
  const metricsUnavailable = !stats && Boolean(statsError);

  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-3 scrollbar-thin xl:overflow-hidden">
      <div className="shrink-0 flex flex-col items-start justify-between gap-3 rounded-xl border border-slate-800 bg-[#05080F] p-3 shadow-lg md:flex-row md:items-center">
        <div>
      <div className="flex items-center gap-2"><span className="aegis-status-indicator aegis-status-indicator--success animate-pulse" /><h2 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-white italic">Aegis Center Overview <span className="rounded border border-cyan-800/40 bg-cyan-950/40 px-2 py-0.5 font-mono text-[10px] font-normal text-cyan-400">LIVE</span></h2></div>
          <p className="mt-1 font-mono text-[10px] text-slate-500">System Time: {currentUtcTime} (UTC) | Coordinator State: Root Active</p>
        </div>
        <div className="flex items-center gap-3"><button onClick={() => setTab('chat')} className="flex items-center gap-1.5 rounded bg-cyan-500 px-3 py-1.5 text-xs font-medium text-white shadow-[0_0_12px_rgba(6,182,212,0.3)] transition-colors hover:bg-cyan-600"><Play className="h-3 w-3" /> 启动智能对话</button><div className="flex items-center gap-1.5 rounded border border-slate-800 bg-[#080C14] px-2 py-1 text-[11px]"><Server className="h-3 w-3 text-cyan-400" /><span className="font-mono text-slate-400">Channel: A2A RPC</span></div></div>
      </div>

      <div className="grid shrink-0 grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard icon={<Activity className="h-4 w-4" />} label="Executing Task Agents" value={stats?.executing_agent_count ?? '—'} note={metricsUnavailable ? 'Metrics unavailable' : 'Distinct agents · last 7 days'} tone="cyan" />
        <MetricCard icon={<Cpu className="h-4 w-4" />} label="Source Platforms" value={stats?.source_platform_count ?? '—'} note={metricsUnavailable ? 'Metrics unavailable' : 'Distinct sources · last 7 days'} tone="violet" />
        <MetricCard icon={<AlertTriangle className="h-4 w-4" />} label="Active Users" value={stats?.active_user_count ?? '—'} note={metricsUnavailable ? 'Metrics unavailable' : 'Distinct requesters · last 7 days'} tone="rose" />
        <MetricCard icon={<ShieldCheck className="h-4 w-4" />} label="Delegations Started" value={stats?.delegation_total ?? '—'} note={stats ? formatVolumeChange(stats.comparison.delegation_volume_change_percent) : metricsUnavailable ? 'Metrics unavailable' : 'Loading metrics...'} tone="emerald" />
      </div>

      <div className="grid grid-cols-1 gap-3 xl:min-h-0 xl:flex-1 xl:grid-cols-3">
        <section className="flex h-[clamp(26rem,54dvh,36rem)] min-h-0 flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#05080F] xl:col-span-2 xl:h-full">
          <div className="z-10 flex flex-col justify-between gap-2 border-b border-slate-800 bg-[#03060C] p-3 sm:flex-row sm:items-center"><div><h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-white italic">Aegis Orchestration Topology <span className="rounded border border-cyan-800 px-1.5 py-0.5 font-mono text-[10px] text-cyan-400">STARMAPPING</span></h3><p className="mt-1 text-[10px] text-slate-500">Hover or focus a node to inspect and illuminate its orchestration path.</p></div><span className="font-mono text-[9px] tracking-wider text-slate-500">API · {topology ? `${topology.agents.length} AGENTS / ${topology.edges.length} EDGES` : 'CONNECTING'}</span></div>
          <StarmappingTopology
            topology={topology}
            error={topologyError}
            appEntries={appEntries}
            appEntriesLoading={appEntriesLoading}
            appEntriesError={appEntriesError}
            appEntriesLoaded={appEntriesLoaded}
          />
        </section>

        <aside className="flex h-[clamp(26rem,54dvh,36rem)] min-h-0 flex-col gap-3 xl:h-full">
          <div className="relative flex items-center justify-between overflow-hidden rounded-xl border border-slate-800 bg-[#05080F] p-3"><div className="absolute right-0 top-0 h-24 w-24 rounded-full bg-cyan-500/5 blur-2xl" /><div className="mr-2"><div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Aegis Delegation Health (7D)</div><h4 className="mt-1 text-sm font-bold text-white italic">{successRate === null ? 'No delegations' : 'Delegation success rate'}</h4><p className="mt-1 text-[11px] leading-normal text-slate-400">{stats ? successRate === null ? 'No delegate audits were recorded in this window.' : `${stats.success_count} succeeded of ${stats.delegation_total} delegations.` : metricsUnavailable ? 'Overview metrics are currently unavailable.' : 'Loading delegation metrics...'}</p>{stats && successRate !== null ? <p className="mt-1 font-mono text-[10px] text-cyan-400">{formatRateChange(stats.comparison.success_rate_change_percentage_points)}</p> : null}{stats ? <div className="mt-2 flex flex-wrap gap-1.5"><span className="aegis-status-badge aegis-status-badge--success">succ {stats.status_counts.succ}</span><span className="aegis-status-badge aegis-status-badge--danger">fail {stats.status_counts.fail}</span><span className="aegis-status-badge aegis-status-badge--warning">denied {stats.status_counts.auth_denied}</span></div> : null}</div><div className="relative flex shrink-0 items-center justify-center"><svg width="64" height="64" viewBox="0 0 36 36" className="-rotate-90 transform"><circle cx="18" cy="18" r="15.91" fill="none" stroke="#1e293b" strokeWidth="2.5" /><circle cx="18" cy="18" r="15.91" fill="none" stroke="url(#postureGradient)" strokeWidth="2.5" strokeDasharray={`${successRatePercent} 100`} /><defs><linearGradient id="postureGradient" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stopColor="#06b6d4" /><stop offset="100%" stopColor="#10b981" /></linearGradient></defs></svg><span className="absolute font-mono text-sm font-extrabold text-white">{successRate === null ? '—' : formatPercent(successRate)}</span></div></div>

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#05080F]"><div className="space-y-2 border-b border-slate-800 bg-[#03060C] p-3"><div className="flex items-center justify-between"><h4 className="text-[11px] font-bold uppercase tracking-widest text-white italic">Agent Index</h4><span className="rounded border border-slate-800 bg-[#080C14] px-1.5 py-0.5 font-mono text-[9px] font-bold text-cyan-400">Total: {agents.length}</span></div><div className="flex gap-1.5"><div className="relative flex-1"><Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" /><input value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} placeholder="Search agents..." className="w-full rounded border border-slate-800 bg-[#020408] py-1 pl-8 pr-2 font-mono text-xs text-white placeholder-slate-600 focus:border-cyan-500 focus:outline-none" /></div><select value={selectedStatus} onChange={(event) => setSelectedStatus(event.target.value)} className="rounded border border-slate-800 bg-[#020408] px-1.5 py-1 font-mono text-[11px] text-cyan-400 focus:border-cyan-500 focus:outline-none"><option value="All">All Status</option><option value="Active">Active</option><option value="Idle">Idle</option><option value="Offline">Offline</option></select></div></div><div className="min-h-0 flex-1 divide-y divide-slate-800/60 overflow-y-auto scrollbar-thin">{filteredAgents.length === 0 ? <div className="p-6 text-center font-mono text-xs text-slate-500">No agents found</div> : filteredAgents.map((agent) => <div key={agent.id} className="flex items-center justify-between border-l-2 border-transparent p-2.5 text-[11px] transition-all hover:border-cyan-500 hover:bg-[#03060C]"><div className="min-w-0 flex-1 pr-2"><div className="flex items-center gap-1.5"><span className={`aegis-status-indicator ${agent.status === 'Active' ? 'aegis-status-indicator--success' : agent.status === 'Idle' ? 'aegis-status-indicator--warning' : 'aegis-status-indicator--danger'}`} /><span className="truncate font-bold text-slate-200">{agent.name}</span><span className="rounded bg-slate-800/40 px-1 font-mono text-[8px] uppercase text-slate-500">{agent.type === 'agent' ? 'Co-Agent' : 'VIP'}</span></div><p className="mt-0.5 truncate text-[10px] text-slate-500">{agent.description}</p></div><div className="shrink-0 text-right font-mono text-[9px] text-slate-500"><div className="font-semibold text-cyan-400">Tasks: {agent.tasksCount}</div><div>{agent.lastUpdated}</div></div></div>)}</div><div className="border-t border-slate-800 bg-[#03060C] p-2 text-center font-mono text-[9px] text-slate-500">UNIFIED_COORDINATOR_ROUTING: ACTIVE</div></div>
        </aside>
      </div>
    </div>
  );
}

function MetricCard({ icon, label, value, note, tone }: { icon: ReactNode; label: string; value: number | string; note: string; tone: 'cyan' | 'violet' | 'rose' | 'emerald' }) {
  const tones = { cyan: 'text-cyan-400 bg-cyan-500/5', violet: 'text-purple-400 bg-purple-500/5', rose: 'text-rose-400 bg-rose-500/5', emerald: 'text-emerald-400 bg-emerald-500/5' };
  return <div className="group relative overflow-hidden rounded-xl border border-slate-800 bg-[#05080F] p-4 transition-colors hover:border-slate-700"><div className={`absolute right-3 top-3 flex h-9 w-9 items-center justify-center rounded-lg ${tones[tone]}`}>{icon}</div><div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">{label}</div><div className="mt-2 font-mono text-2xl font-black tracking-tight text-white">{value}</div><p className="mt-3 font-mono text-[10px] text-slate-500">{note}</p></div>;
}
