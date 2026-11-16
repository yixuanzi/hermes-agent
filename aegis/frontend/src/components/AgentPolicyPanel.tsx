import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Edit2, Plus, RefreshCw, Search, Trash2, X } from 'lucide-react';

import { ApiError, alertApiError, fetchJSON } from '../lib/api';
import { Agent, AgentPolicy } from '../types';

interface AgentPolicyPanelProps {
  agents?: Agent[];
  onAuthExpired?: () => void;
}

type BusyAction = 'refresh' | 'save' | `delete:${number}` | null;

const EMPTY_POLICY: AgentPolicy = {
  rank_id: 1,
  platform: '*',
  user_id: '*',
  agent_name: '*',
  status: 'allow',
};

export default function AgentPolicyPanel({
  agents = [],
  onAuthExpired,
}: AgentPolicyPanelProps) {
  const [policies, setPolicies] = useState<AgentPolicy[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingRank, setEditingRank] = useState<number | null>(null);
  const [draft, setDraft] = useState<AgentPolicy>(EMPTY_POLICY);
  const requestSequence = useRef(0);
  const busy = busyAction !== null;

  const filteredPolicies = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    if (!query) return policies;
    return policies.filter((policy) =>
      [policy.rank_id, policy.platform, policy.user_id, policy.agent_name, policy.status]
        .some((value) => String(value).toLowerCase().includes(query)),
    );
  }, [policies, searchTerm]);

  async function loadPolicies() {
    const requestId = ++requestSequence.current;
    setBusyAction('refresh');
    try {
      const response = await fetchJSON<{ policies: AgentPolicy[] }>('/api/routing/agent');
      if (requestId === requestSequence.current) {
        setPolicies([...response.policies].sort((left, right) => left.rank_id - right.rank_id));
      }
    } catch (error) {
      if (requestId !== requestSequence.current) return;
      if (error instanceof ApiError && error.status === 401) {
        onAuthExpired?.();
        return;
      }
      alertApiError(error, 'Failed to load Agent Policy rules.');
    } finally {
      if (requestId === requestSequence.current) setBusyAction(null);
    }
  }

  useEffect(() => {
    void loadPolicies();
  }, []);

  function openCreate() {
    const nextRank = policies.reduce((highest, policy) => Math.max(highest, policy.rank_id), 0) + 1;
    setEditingRank(null);
    setDraft({ ...EMPTY_POLICY, rank_id: nextRank });
    setModalOpen(true);
  }

  function openEdit(policy: AgentPolicy) {
    setEditingRank(policy.rank_id);
    setDraft({ ...policy });
    setModalOpen(true);
  }

  async function savePolicy(event: FormEvent) {
    event.preventDefault();
    requestSequence.current += 1;
    setBusyAction('save');
    try {
      const path = editingRank === null
        ? '/api/routing/agent'
        : `/api/routing/agent/${encodeURIComponent(editingRank)}`;
      const saved = await fetchJSON<AgentPolicy>(path, {
        method: editingRank === null ? 'POST' : 'PUT',
        body: JSON.stringify(draft),
      });
      setPolicies((current) => {
        const remaining = editingRank === null
          ? current
          : current.filter((policy) => policy.rank_id !== editingRank);
        return [...remaining, saved].sort((left, right) => left.rank_id - right.rank_id);
      });
      setModalOpen(false);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        onAuthExpired?.();
        return;
      }
      alertApiError(error, 'Failed to save Agent Policy rule.');
    } finally {
      setBusyAction(null);
    }
  }

  async function deletePolicy(rankId: number) {
    if (!window.confirm(`Delete Agent Policy rank ${rankId}?`)) return;
    requestSequence.current += 1;
    setBusyAction(`delete:${rankId}`);
    try {
      await fetchJSON<{ deleted: boolean }>(
        `/api/routing/agent/${encodeURIComponent(rankId)}`,
        { method: 'DELETE' },
      );
      setPolicies((current) => current.filter((policy) => policy.rank_id !== rankId));
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        onAuthExpired?.();
        return;
      }
      alertApiError(error, 'Failed to delete Agent Policy rule.');
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <div className="flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#05080F]">
      <div className="flex flex-col justify-between gap-3 border-b border-slate-800 bg-[#03060C] p-4 md:flex-row md:items-center">
        <div>
          <h4 className="font-bold text-cyan-400">Agent Policy</h4>
          <p className="mt-1 text-[10px] text-slate-500">Lowest matching rank decides whether a caller may delegate.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <label className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" />
            <input
              aria-label="Search Agent Policy"
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Search rank, platform, user, agent..."
              className="w-60 rounded border border-slate-800 bg-[#020408] py-1.5 pl-8 pr-3 text-xs text-white"
            />
          </label>
          <button type="button" disabled={busy} aria-busy={busyAction === 'refresh'} onClick={() => void loadPolicies()} className={`aegis-btn aegis-btn--secondary flex items-center gap-1 rounded px-3 py-1.5 ${busyAction === 'refresh' ? 'aegis-btn--busy' : ''}`}>
            <RefreshCw className={`h-3.5 w-3.5 ${busyAction === 'refresh' ? 'animate-spin' : ''}`} /> Refresh
          </button>
          <button type="button" disabled={busy} onClick={openCreate} className="aegis-btn aegis-btn--primary flex items-center gap-1 rounded px-3 py-1.5 font-bold">
            <Plus className="h-3.5 w-3.5" /> New Policy
          </button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead className="border-b border-slate-800 bg-[#03060C] font-mono text-[9px] uppercase tracking-wider text-slate-500">
            <tr><th className="p-3">Rank</th><th className="p-3">Platform</th><th className="p-3">User ID</th><th className="p-3">Agent</th><th className="p-3">Decision</th><th className="p-3 text-center">Actions</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-800/50">
            {filteredPolicies.length === 0 ? (
              <tr><td colSpan={6} className="p-8 text-center text-slate-500">No Agent Policy rules found.</td></tr>
            ) : filteredPolicies.map((policy) => (
              <tr key={policy.rank_id} className="aegis-table-row">
                <td className="p-3 font-mono text-cyan-400">{policy.rank_id}</td>
                <td className="p-3 font-mono">{policy.platform}</td>
                <td className="p-3 font-mono">{policy.user_id}</td>
                <td className="p-3 font-mono">{policy.agent_name}</td>
                <td className="p-3"><span className={`aegis-status-badge ${policy.status === 'allow' ? 'aegis-status-badge--success' : 'aegis-status-badge--danger'}`}>{policy.status.toUpperCase()}</span></td>
                <td className="p-3"><div className="flex justify-center gap-2">
                  <button type="button" disabled={busy} aria-label={`Edit Agent Policy ${policy.rank_id}`} onClick={() => openEdit(policy)} className="aegis-btn aegis-btn--secondary aegis-btn--icon rounded p-1 text-cyan-400"><Edit2 className="h-3 w-3" /></button>
                  <button type="button" disabled={busy} aria-busy={busyAction === `delete:${policy.rank_id}`} aria-label={`Delete Agent Policy ${policy.rank_id}`} onClick={() => void deletePolicy(policy.rank_id)} className={`aegis-btn aegis-btn--danger aegis-btn--icon rounded p-1 ${busyAction === `delete:${policy.rank_id}` ? 'aegis-btn--busy' : ''}`}><Trash2 className="h-3 w-3" /></button>
                </div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modalOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4 backdrop-blur-sm">
          <form onSubmit={(event) => void savePolicy(event)} className="w-full max-w-md space-y-4 rounded-xl border border-slate-800 bg-[#05080F] p-5 shadow-2xl">
            <div className="flex items-center justify-between"><h4 className="font-bold text-white">{editingRank === null ? 'New Agent Policy' : 'Edit Agent Policy'}</h4><button type="button" aria-label="Close Agent Policy dialog" onClick={() => setModalOpen(false)} className="aegis-btn aegis-btn--ghost aegis-btn--icon rounded p-1"><X className="h-4 w-4" /></button></div>
            <label className="block space-y-1"><span>Rank *</span><input aria-label="Agent Policy Rank" required min={1} type="number" value={draft.rank_id} onChange={(event) => setDraft((current) => ({ ...current, rank_id: Number(event.target.value) }))} className="w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-white" /></label>
            <label className="block space-y-1"><span>Platform</span><input aria-label="Agent Policy Platform" value={draft.platform} onChange={(event) => setDraft((current) => ({ ...current, platform: event.target.value }))} className="w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-white" /></label>
            <label className="block space-y-1"><span>User ID</span><input aria-label="Agent Policy User ID" value={draft.user_id} onChange={(event) => setDraft((current) => ({ ...current, user_id: event.target.value }))} className="w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-white" /></label>
            <label className="block space-y-1"><span>Agent</span><input aria-label="Agent Policy Agent" list="agent-policy-options" value={draft.agent_name} onChange={(event) => setDraft((current) => ({ ...current, agent_name: event.target.value }))} className="w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-white" /><datalist id="agent-policy-options"><option value="*" />{agents.map((agent) => <option key={agent.id} value={agent.id} />)}</datalist></label>
            <label className="block space-y-1"><span>Decision</span><select aria-label="Agent Policy Decision" value={draft.status} onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value as AgentPolicy['status'] }))} className="w-full rounded border border-slate-800 bg-[#020408] px-3 py-2 text-white"><option value="allow">Allow</option><option value="deny">Deny</option></select></label>
            <div className="flex justify-end gap-2"><button type="button" onClick={() => setModalOpen(false)} className="aegis-btn aegis-btn--secondary rounded px-4 py-2 font-semibold">Cancel</button><button type="submit" disabled={busy} aria-busy={busyAction === 'save'} className={`aegis-btn aegis-btn--primary rounded px-4 py-2 font-bold ${busyAction === 'save' ? 'aegis-btn--busy' : ''}`}>Save Policy</button></div>
          </form>
        </div>
      ) : null}
    </div>
  );
}
