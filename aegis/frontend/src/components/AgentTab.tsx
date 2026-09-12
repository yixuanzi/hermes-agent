import React, { useMemo, useState } from 'react';
import { Activity, AlertCircle, Edit2, Plus, RefreshCw, Search, Trash2, Users, X } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { alertApiError } from '../lib/api';
import { Agent, AgentDraft, AgentHeaderEntry } from '../types';

interface AgentTabProps {
  agents: Agent[];
  busy: boolean;
  onCreate: (draft: AgentDraft) => Promise<void>;
  onUpdate: (agentId: string, draft: AgentDraft) => Promise<void>;
  onDelete: (agentId: string) => Promise<void>;
  onRefresh: () => Promise<void>;
}

export default function AgentTab({
  agents,
  busy,
  onCreate,
  onDelete,
  onRefresh,
  onUpdate,
}: AgentTabProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('All');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const [formAgentId, setFormAgentId] = useState('');
  const [formStatus, setFormStatus] = useState<'Active' | 'Idle' | 'Offline'>('Active');
  const [formDesc, setFormDesc] = useState('');
  const [formA2aAddr, setFormA2aAddr] = useState('');
  const [formHeaders, setFormHeaders] = useState<AgentHeaderEntry[]>([{ key: 'Authorization', value: '' }]);
  const [formCapabilities, setFormCapabilities] = useState<string[]>([]);

  const total = agents.length;
  const active = agents.filter((agent) => agent.status === 'Active').length;
  const idle = agents.filter((agent) => agent.status === 'Idle').length;
  const offline = agents.filter((agent) => agent.status === 'Offline').length;

  const filtered = useMemo(() => {
    return agents.filter((agent) => {
      const matchSearch =
        agent.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        agent.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (agent.skillDescription || '').toLowerCase().includes(searchTerm.toLowerCase());
      const matchStatus = statusFilter === 'All' || agent.status === statusFilter;
      return matchSearch && matchStatus;
    });
  }, [agents, searchTerm, statusFilter]);

  function resetForm() {
    setFormAgentId('');
    setFormStatus('Active');
    setFormDesc('');
    setFormA2aAddr('');
    setFormHeaders([{ key: 'Authorization', value: '' }]);
    setFormCapabilities([]);
    setError('');
  }

  function handleOpenCreateModal() {
    setEditingAgent(null);
    resetForm();
    setIsModalOpen(true);
  }

  function handleOpenEditModal(agent: Agent) {
    setEditingAgent(agent);
    setFormAgentId(agent.id);
    setFormStatus(agent.status);
    setFormDesc(agent.description);
    setFormA2aAddr(agent.a2aAddr || '');
    setFormHeaders(
      agent.headers && agent.headers.length > 0
        ? agent.headers.map((header) => ({ ...header }))
        : [{ key: 'Authorization', value: '' }],
    );
    setFormCapabilities([...(agent.extCapabilities || [])]);
    setError('');
    setIsModalOpen(true);
  }

  async function handleDeleteAgent(agentId: string) {
    if (!window.confirm('Delete this agent? (确定删除该工作智能体吗？此操作不可逆)')) {
      return;
    }

    try {
      await onDelete(agentId);
      setError('');
    } catch (caughtError) {
      alertApiError(caughtError, '删除智能体失败。');
    }
  }

  function handleAddHeader() {
    setFormHeaders((current) => [...current, { key: '', value: '' }]);
  }

  function handleChangeHeaderKey(index: number, value: string) {
    setFormHeaders((current) => current.map((header, currentIndex) => (
      currentIndex === index ? { ...header, key: value } : header
    )));
  }

  function handleChangeHeaderValue(index: number, value: string) {
    setFormHeaders((current) => current.map((header, currentIndex) => (
      currentIndex === index ? { ...header, value } : header
    )));
  }

  function handleRemoveHeader(index: number) {
    setFormHeaders((current) => current.filter((_, currentIndex) => currentIndex !== index));
  }

  function handleAddCapability() {
    setFormCapabilities((current) => [...current, '']);
  }

  function handleChangeCapability(index: number, value: string) {
    setFormCapabilities((current) => current.map((capability, currentIndex) => (
      currentIndex === index ? value : capability
    )));
  }

  function handleRemoveCapability(index: number) {
    setFormCapabilities((current) => current.filter((_, currentIndex) => currentIndex !== index));
  }

  async function handleSaveAgent(event: React.FormEvent) {
    event.preventDefault();
    if (!formAgentId.trim() || !formDesc.trim() || !formA2aAddr.trim()) {
      setError('请填写 Agent ID、A2A 地址与功能描述。');
      return;
    }

    setSubmitting(true);
    setError('');

    const draft: AgentDraft = {
      agentId: formAgentId.trim(),
      url: formA2aAddr.trim(),
      description: formDesc.trim(),
      status: formStatus,
      headers: formHeaders,
      extCapabilities: formCapabilities,
    };

    try {
      if (editingAgent) {
        await onUpdate(editingAgent.id, draft);
      } else {
        await onCreate(draft);
      }
      setIsModalOpen(false);
    } catch (caughtError) {
      alertApiError(caughtError, '保存智能体失败。');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="aegis-console-page text-xs scrollbar-thin">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-[#05080F] p-4">
          <div>
            <div className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">Total Registered</div>
            <div className="mt-0.5 font-mono text-2xl font-black text-white">{total}</div>
          </div>
          <Users className="h-6 w-6 text-slate-500 opacity-60" />
        </div>
        <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-[#05080F] p-4">
          <div>
            <div className="aegis-status-text--success text-[9px] font-mono font-bold uppercase tracking-wider">Active &amp; Working</div>
            <div className="mt-0.5 font-mono text-2xl font-black text-white">{active}</div>
          </div>
          <Activity className="aegis-status-text--success h-6 w-6 animate-pulse opacity-60" />
        </div>
        <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-[#05080F] p-4">
          <div>
            <div className="aegis-status-text--warning text-[9px] font-mono font-bold uppercase tracking-wider">Idle Status</div>
            <div className="mt-0.5 font-mono text-2xl font-black text-white">{idle}</div>
          </div>
          <Activity className="aegis-status-text--warning h-6 w-6 opacity-60" />
        </div>
        <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-[#05080F] p-4">
          <div>
            <div className="aegis-status-text--danger text-[9px] font-mono font-bold uppercase tracking-wider">Offline Status</div>
            <div className="mt-0.5 font-mono text-2xl font-black text-white">{offline}</div>
          </div>
          <AlertCircle className="aegis-status-text--danger h-6 w-6 opacity-60" />
        </div>
      </div>

      <div className="flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#05080F]">
        <div className="aegis-console-section-header flex flex-col justify-between border-b border-slate-800 bg-[#03060C] md:flex-row md:items-center">
          <div>
            <h3 className="flex items-center gap-1.5 text-sm font-bold uppercase tracking-widest italic text-white">
              Agent Orchestration (工作智能体管理)
            </h3>
            <p className="text-[10px] text-slate-500">直接管理 `a2a.json` 中的 Agent 定义、状态、A2A 地址和全局可见技能描述。</p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" />
              <input
                type="text"
                value={searchTerm}
                onChange={(event) => setSearchTerm(event.target.value)}
                placeholder="Search id, description..."
                className="w-60 rounded border border-slate-800 bg-[#020408] py-1.5 pl-8 pr-3 font-mono text-xs text-white placeholder-slate-700 focus:border-cyan-500 focus:outline-none"
              />
            </div>

            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              className="rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 font-mono text-xs text-cyan-400 focus:border-cyan-500 focus:outline-none"
            >
              <option value="All">All Status</option>
              <option value="Active">Active</option>
              <option value="Idle">Idle</option>
              <option value="Offline">Offline</option>
            </select>

            <button
              onClick={() => void onRefresh()}
              className="flex shrink-0 items-center gap-1 rounded border border-slate-800 bg-[#020408] px-3 py-1.5 text-xs font-semibold text-slate-300 transition-all hover:bg-slate-900"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${busy ? 'animate-spin' : ''}`} /> Refresh
            </button>

            <button
              onClick={handleOpenCreateModal}
              className="flex shrink-0 items-center gap-1 rounded border-0 bg-cyan-500 px-3 py-1.5 text-xs font-bold text-white shadow-[0_0_10px_rgba(6,182,212,0.3)] transition-all hover:bg-cyan-600"
            >
              <Plus className="h-3.5 w-3.5" /> 注册智能体
            </button>
          </div>
        </div>

        {error ? <div className="border-b border-rose-900/30 bg-rose-950/20 px-4 py-3 text-xs text-rose-300">{error}</div> : null}

        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-slate-800 bg-[#03060C] font-mono text-[9px] uppercase tracking-wider text-slate-500">
                <th className="p-3">Agent ID</th>
                <th className="p-3">Status</th>
                <th className="p-3">A2A Address</th>
                <th className="p-3">Description</th>
                <th className="p-3">Capabilities</th>
                <th className="p-3 text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-8 text-center font-mono text-xs text-slate-500">
                    No matching work agents found.
                  </td>
                </tr>
              ) : (
                filtered.map((agent) => (
                  <tr key={agent.id} className="aegis-table-row">
                    <td className="min-w-[200px] whitespace-nowrap p-3">
                      <div className="flex items-center gap-2">
                        <span className={`aegis-status-indicator ${
                          agent.status === 'Active'
                            ? 'aegis-status-indicator--success'
                            : agent.status === 'Idle'
                              ? 'aegis-status-indicator--warning'
                              : 'aegis-status-indicator--danger'
                        }`} />
                        <div>
                          <div className="text-xs font-bold text-slate-200">{agent.name}</div>
                          <div className="text-[10px] font-mono text-slate-500">Stored key: {agent.id}</div>
                        </div>
                      </div>
                    </td>
                    <td className="whitespace-nowrap p-3">
                      <span className={`aegis-status-badge ${
                        agent.status === 'Active'
                          ? 'aegis-status-badge--success'
                          : agent.status === 'Idle'
                            ? 'aegis-status-badge--warning'
                            : 'aegis-status-badge--danger'
                      }`}>
                        {agent.status}
                      </span>
                    </td>
                    <td className="whitespace-nowrap p-3 font-mono text-xs text-cyan-400">{agent.a2aAddr}</td>
                    <td className="max-w-xs p-3">
                      <div className="line-clamp-2 leading-relaxed text-slate-400">{agent.description}</div>
                    </td>
                    <td className="max-w-sm p-3">
                      <div className="line-clamp-3 whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-cyan-400">
                        {agent.skillDescription || '--'}
                      </div>
                    </td>
                    <td className="whitespace-nowrap p-3 text-center">
                      <div className="flex justify-center gap-2">
                        <button
                          onClick={() => handleOpenEditModal(agent)}
                          className="rounded border border-slate-800 bg-[#080C14] p-1 text-cyan-400 transition-all hover:bg-slate-850 hover:text-cyan-300"
                          title="Edit Agent details"
                        >
                          <Edit2 className="h-3 w-3" />
                        </button>
                        <button
                          onClick={() => void handleDeleteAgent(agent.id)}
                          className="rounded border border-rose-900/40 bg-rose-950/10 p-1 text-rose-400 transition-all hover:bg-rose-950/20 hover:text-rose-300"
                          title="Delete Agent profile"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <AnimatePresence>
        {isModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4 backdrop-blur-sm">
            <motion.div
              initial={{ scale: 0.96, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.96, opacity: 0 }}
              className="flex max-h-[calc(100vh-2rem)] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#05080F] shadow-2xl"
            >
              <div className="flex items-center justify-between border-b border-slate-800/80 bg-[#03060C] p-4">
                <h4 className="text-xs font-bold uppercase tracking-wider text-white">
                  {editingAgent ? '编辑智能体配置' : '注册新网络安全智能体'}
                </h4>
                <button
                  onClick={() => setIsModalOpen(false)}
                  className="rounded p-1 text-slate-500 transition-all hover:bg-slate-800 hover:text-white"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <form onSubmit={(event) => void handleSaveAgent(event)} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 scrollbar-thin">
                <div className="grid grid-cols-2 gap-3.5">
                  <div className="col-span-2 space-y-1">
                    <label className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">Agent ID *</label>
                    <input
                      type="text"
                      required
                      disabled={Boolean(editingAgent)}
                      value={formAgentId}
                      onChange={(event) => setFormAgentId(event.target.value)}
                      placeholder="e.g. threat-intel"
                      className="w-full rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 font-mono text-xs text-white placeholder-slate-700 focus:border-cyan-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
                    />
                  </div>

                  <div className="col-span-2 space-y-1">
                    <label className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">A2A 地址 (A2A Addr) *</label>
                    <input
                      type="text"
                      required
                      value={formA2aAddr}
                      onChange={(event) => setFormA2aAddr(event.target.value)}
                      placeholder="e.g. http://127.0.0.1:9086/a2a"
                      className="w-full rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 font-mono text-xs text-white placeholder-slate-700 focus:border-cyan-500 focus:outline-none"
                    />
                  </div>

                  <div className="col-span-2 space-y-1">
                    <div className="flex items-center justify-between gap-3">
                      <label className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">验证头字段</label>
                      <button
                        type="button"
                        onClick={handleAddHeader}
                        aria-label="添加验证头"
                        className="flex items-center gap-1 rounded border border-cyan-900/60 bg-cyan-950/20 px-2 py-1 text-[10px] font-mono font-semibold text-cyan-300 transition-colors hover:border-cyan-500 hover:bg-cyan-950/50 hover:text-cyan-100"
                      >
                        <Plus className="h-3 w-3" /> 添加验证头
                      </button>
                    </div>
                    <div className="space-y-2">
                      {formHeaders.map((header, index) => (
                        <div key={index} className="flex items-stretch gap-2">
                          <input
                            type="text"
                            value={header.key}
                            onChange={(event) => handleChangeHeaderKey(index, event.target.value)}
                            aria-label={`验证头字段名 ${index + 1}`}
                            placeholder="Authorization"
                            className="w-2/5 rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 font-mono text-xs text-white placeholder-slate-700 focus:border-cyan-500 focus:outline-none"
                          />
                          <input
                            type="text"
                            value={header.value}
                            onChange={(event) => handleChangeHeaderValue(index, event.target.value)}
                            aria-label={`验证头字段值 ${index + 1}`}
                            placeholder="Bearer token"
                            className="flex-1 rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 font-mono text-xs text-white placeholder-slate-700 focus:border-cyan-500 focus:outline-none"
                          />
                          <button
                            type="button"
                            onClick={() => handleRemoveHeader(index)}
                            aria-label={`删除验证头 ${index + 1}`}
                            className="shrink-0 rounded p-1.5 text-slate-600 transition-colors hover:bg-rose-950/40 hover:text-rose-300"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="col-span-2 space-y-1">
                    <label className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">通信心跳状态</label>
                    <select
                      value={formStatus}
                      onChange={(event) => setFormStatus(event.target.value as 'Active' | 'Idle' | 'Offline')}
                      className="w-full rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 font-mono text-xs text-cyan-400 focus:border-cyan-500 focus:outline-none"
                    >
                      <option value="Active">Active (心跳活跃)</option>
                      <option value="Idle">Idle (空闲待命)</option>
                      <option value="Offline">Offline (临时离线)</option>
                    </select>
                  </div>

                  <div className="col-span-2 space-y-1">
                    <label className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">功能描述 (Description) *</label>
                    <input
                      type="text"
                      required
                      value={formDesc}
                      onChange={(event) => setFormDesc(event.target.value)}
                      placeholder="e.g. 审计服务器特权指令偏差、越权和容器越权"
                      className="w-full rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 text-xs text-white placeholder-slate-700 focus:border-cyan-500 focus:outline-none"
                    />
                  </div>

                  <div className="col-span-2 space-y-1">
                    <div className="flex items-center justify-between gap-3">
                      <label className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">
                        技能描述 / 能力列表
                      </label>
                      <button
                        type="button"
                        onClick={handleAddCapability}
                        aria-label="添加能力描述"
                        className="flex items-center gap-1 rounded border border-cyan-900/60 bg-cyan-950/20 px-2 py-1 text-[10px] font-mono font-semibold text-cyan-300 transition-colors hover:border-cyan-500 hover:bg-cyan-950/50 hover:text-cyan-100"
                      >
                        <Plus className="h-3 w-3" /> 添加能力
                      </button>
                    </div>
                    <p className="text-[10px] leading-relaxed text-slate-600">一项能力对应一个输入框，可填写多行描述。</p>
                    {formCapabilities.length === 0 ? (
                      <div className="rounded border border-dashed border-slate-800 bg-[#020408]/60 px-3 py-2 font-mono text-[10px] text-slate-600">
                        尚未添加能力描述
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {formCapabilities.map((capability, index) => (
                          <div key={index} className="flex items-stretch gap-2 rounded border border-slate-800 bg-[#020408] p-1.5 focus-within:border-cyan-700">
                            <textarea
                              rows={1}
                              value={capability}
                              onChange={(event) => handleChangeCapability(index, event.target.value)}
                              aria-label={`能力描述 ${index + 1}`}
                              placeholder="描述该 Agent 可执行的单项能力"
                              className="flex-1 resize-y bg-transparent px-1 py-0.5 font-mono text-xs leading-relaxed text-white placeholder-slate-700 focus:outline-none"
                            />
                            <button
                              type="button"
                              onClick={() => handleRemoveCapability(index)}
                              aria-label={`删除能力描述 ${index + 1}`}
                              className="self-start rounded p-1.5 text-slate-600 transition-colors hover:bg-rose-950/40 hover:text-rose-300"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex justify-end gap-2 border-t border-slate-800 pt-4 text-xs">
                  <button
                    type="button"
                    onClick={() => setIsModalOpen(false)}
                    className="cursor-pointer rounded border border-slate-800 bg-[#020408] px-4 py-2 font-semibold text-slate-400 transition-all hover:bg-slate-800/50 hover:text-white"
                  >
                    取消
                  </button>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="cursor-pointer rounded border-0 bg-cyan-500 px-5 py-2 font-semibold text-white transition-all hover:bg-cyan-600 hover:shadow-[0_0_10px_rgba(6,182,212,0.4)] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {submitting ? 'Saving...' : '保存 (Save)'}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
