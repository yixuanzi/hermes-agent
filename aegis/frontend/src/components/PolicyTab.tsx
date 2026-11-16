import React, { useMemo, useState } from 'react';
import { Edit2, Plus, RefreshCw, Search, Trash2, X } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { alertApiError } from '../lib/api';
import { RoutingRule, RoutingRuleDraft } from '../types';
import type { Agent } from '../types';
import AgentPolicyPanel from './AgentPolicyPanel';

interface PolicyTabProps {
  agents?: Agent[];
  rules: RoutingRule[];
  busy: boolean;
  onCreate: (draft: RoutingRuleDraft) => Promise<void>;
  onUpdate: (ruleId: string, draft: RoutingRuleDraft) => Promise<void>;
  onDelete: (ruleId: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  onAuthExpired?: () => void;
}

export default function PolicyTab({
  agents = [],
  rules,
  busy,
  onCreate,
  onDelete,
  onRefresh,
  onUpdate,
  onAuthExpired,
}: PolicyTabProps) {
  const [activePolicyView, setActivePolicyView] = useState<'global' | 'agent'>('global');
  const [searchTerm, setSearchTerm] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<RoutingRule | null>(null);
  const [formName, setFormName] = useState('');
  const [formConditions, setFormConditions] = useState('');
  const [formStatus, setFormStatus] = useState<'Enabled' | 'Disabled'>('Enabled');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const filteredRules = useMemo(() => {
    return rules.filter((rule) => {
      const matchSearch =
        rule.ruleName.toLowerCase().includes(searchTerm.toLowerCase()) ||
        rule.conditions.toLowerCase().includes(searchTerm.toLowerCase());
      return matchSearch;
    });
  }, [rules, searchTerm]);

  function handleOpenCreateModal() {
    setEditingRule(null);
    setFormName('');
    setFormConditions('');
    setFormStatus('Enabled');
    setError('');
    setIsModalOpen(true);
  }

  function handleOpenEditModal(rule: RoutingRule) {
    setEditingRule(rule);
    setFormName(rule.ruleName);
    setFormConditions(rule.conditions);
    setFormStatus(rule.status);
    setError('');
    setIsModalOpen(true);
  }

  async function handleDeleteRule(ruleId: string) {
    if (!window.confirm('Delete this routing policy? (确定要删除这条路由策略/规则吗？)')) {
      return;
    }

    try {
      await onDelete(ruleId);
      setError('');
    } catch (caughtError) {
      alertApiError(caughtError, '删除路由规则失败。');
    }
  }

  async function handleSaveRule(event: React.FormEvent) {
    event.preventDefault();
    if (!formName.trim() || !formConditions.trim()) {
      setError('请填写规则名称与具体的路由规则内容。');
      return;
    }

    setSubmitting(true);
    setError('');

    const draft: RoutingRuleDraft = {
      name: formName.trim(),
      policy: formConditions.trim(),
      status: formStatus,
    };

    try {
      if (editingRule) {
        await onUpdate(editingRule.id, draft);
      } else {
        await onCreate(draft);
      }
      setIsModalOpen(false);
    } catch (caughtError) {
      alertApiError(caughtError, '保存路由规则失败。');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="aegis-console-page text-xs scrollbar-thin">
      <div className="aegis-console-intro relative flex flex-col justify-between overflow-hidden rounded-xl border border-slate-800 bg-[#05080F] md:flex-row md:items-center">
        <div className="absolute right-0 top-0 h-32 w-32 rounded-full bg-cyan-500/5 blur-2xl" />
        <div className="max-w-2xl flex-1 space-y-1.5">
          <h3 className="flex items-center gap-1.5 text-sm font-bold uppercase tracking-widest italic text-white">
            Aegis Smart Policy Router (路由 Policy 中枢)
          </h3>
          <p className="leading-relaxed text-slate-400">
            管理 `a2a.json` 中的 <strong>Global Routing Rules</strong>，以及 `aegis.db`
            中控制用户能否委派目标 Agent 的 <strong>Agent Policy</strong>。
          </p>
        </div>
        <div className="shrink-0 rounded border border-slate-800 bg-[#03060C] px-3 py-2 text-[10px] font-bold text-cyan-400">
          Policy Mode: <strong className="text-white">Routing + Access</strong>
        </div>
      </div>

      <div className="aegis-console-tabs flex rounded-xl border border-slate-800 bg-[#03060C]">
        <button type="button" aria-pressed={activePolicyView === 'global'} onClick={() => setActivePolicyView('global')} className={`aegis-btn rounded-lg px-4 py-2 font-bold ${activePolicyView === 'global' ? 'aegis-btn--primary aegis-btn--selected' : 'aegis-btn--ghost'}`}>Global Routing Rules</button>
        <button type="button" aria-pressed={activePolicyView === 'agent'} onClick={() => setActivePolicyView('agent')} className={`aegis-btn rounded-lg px-4 py-2 font-bold ${activePolicyView === 'agent' ? 'aegis-btn--primary aegis-btn--selected' : 'aegis-btn--ghost'}`}>Agent Policy</button>
      </div>

      {activePolicyView === 'global' ? <div className="flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#05080F]">
        <div className="aegis-console-section-header flex flex-col justify-between border-b border-slate-800 bg-[#03060C] md:flex-row md:items-center">
          <div>
            <h4 className="font-bold text-cyan-400">Global Routing Rules</h4>
            <p className="mt-1 text-[10px] text-slate-500">
              Priority-ordered rules route matching requests to the configured agent.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-600" />
              <input
                type="text"
                value={searchTerm}
                onChange={(event) => setSearchTerm(event.target.value)}
                placeholder="Search global rule, condition..."
                className="w-52 rounded border border-slate-800 bg-[#020408] py-1.5 pl-8 pr-3 font-mono text-xs text-white placeholder-slate-700 focus:border-cyan-500 focus:outline-none"
              />
            </div>

            <button
              onClick={() => void onRefresh()}
              className="flex shrink-0 items-center gap-1 rounded border border-slate-800 bg-[#020408] px-3 py-1.5 text-xs font-semibold text-slate-300 transition-all hover:bg-slate-900"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${busy ? 'animate-spin' : ''}`} /> Refresh
            </button>

            <button
              onClick={handleOpenCreateModal}
              className="flex shrink-0 items-center gap-1 rounded border-0 bg-cyan-500 px-3 py-1.5 text-xs font-bold text-white shadow-[0_0_10px_rgba(6,182,212,0.35)] transition-all hover:bg-cyan-600"
            >
              <Plus className="h-3.5 w-3.5" /> 新建路由规则
            </button>
          </div>
        </div>

        {error ? <div className="border-b border-rose-900/30 bg-rose-950/20 px-4 py-3 text-xs text-rose-300">{error}</div> : null}

        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-slate-800 bg-[#03060C] font-mono text-[9px] uppercase tracking-wider text-slate-500">
                <th className="p-3">Rule Name & Config</th>
                <th className="p-3">Routing Scope</th>
                <th className="p-3">Global Rule Content</th>
                <th className="p-3">Active State</th>
                <th className="p-3">Last Change</th>
                <th className="p-3 text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {filteredRules.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-8 text-center font-mono text-xs text-slate-500">
                    No rules matching standard set.
                  </td>
                </tr>
              ) : (
                filteredRules.map((rule) => (
                  <tr key={rule.id} className="aegis-table-row">
                    <td className="min-w-[160px] p-3">
                      <div>
                        <div className="text-xs font-bold text-slate-200">{rule.ruleName}</div>
                        <div className="text-[10px] font-mono text-slate-500">Rule ID: {rule.id}</div>
                      </div>
                    </td>
                    <td className="whitespace-nowrap p-3">
                      <span className="rounded border border-slate-800 bg-[#080C14] px-1.5 py-0.5 font-mono text-[10px] font-bold text-slate-400">
                        All Work Agents
                      </span>
                    </td>
                    <td className="max-w-sm p-3">
                      <div className="line-clamp-2 text-xs leading-relaxed text-cyan-400 font-mono">{rule.conditions}</div>
                    </td>
                    <td className="whitespace-nowrap p-3">
                      {rule.status === 'Enabled' ? (
                        <span className="aegis-status-badge aegis-status-badge--success">
                          ACTIVE (启用)
                        </span>
                      ) : (
                        <span className="aegis-status-badge aegis-status-badge--muted">
                          DISABLED (停用)
                        </span>
                      )}
                    </td>
                    <td className="whitespace-nowrap p-3 font-mono text-slate-500">{rule.updateTime}</td>
                    <td className="whitespace-nowrap p-3 text-center">
                      <div className="flex justify-center gap-2">
                        <button
                          onClick={() => handleOpenEditModal(rule)}
                          className="rounded border border-slate-800 bg-[#080C14] p-1 text-cyan-400 transition-all hover:bg-slate-850 hover:text-cyan-300"
                          title="Edit global routing rule"
                        >
                          <Edit2 className="h-3 w-3" />
                        </button>
                        <button
                          onClick={() => void handleDeleteRule(rule.id)}
                          className="rounded border border-rose-900/40 bg-rose-950/10 p-1 text-rose-400 transition-all hover:bg-rose-950/20 hover:text-rose-300"
                          title="Delete global routing rule"
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
      </div> : <AgentPolicyPanel agents={agents} onAuthExpired={onAuthExpired} />}

      <AnimatePresence>
        {activePolicyView === 'global' && isModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4 backdrop-blur-sm">
            <motion.div
              initial={{ scale: 0.96, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.96, opacity: 0 }}
              className="flex w-full max-w-md flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#05080F] shadow-2xl"
            >
              <div className="flex items-center justify-between border-b border-slate-800/80 bg-[#03060C] p-4">
                <h4 className="text-xs font-bold uppercase tracking-wider text-white">
                  {editingRule ? '编辑全局路由规则' : '创建全局路由规则'}
                </h4>
                <button
                  onClick={() => setIsModalOpen(false)}
                  className="rounded p-1 text-slate-500 transition-all hover:bg-slate-800 hover:text-white"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <form onSubmit={(event) => void handleSaveRule(event)} className="space-y-4 p-4">
                <div className="space-y-3.5">
                  <div className="space-y-1">
                    <label className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">条目规则名称 (Rule Name) *</label>
                    <input
                      type="text"
                      required
                      value={formName}
                      onChange={(event) => setFormName(event.target.value)}
                      placeholder="e.g. 钓鱼邮件重设优先级"
                      className="w-full rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 font-mono text-xs text-white placeholder-slate-700 focus:border-cyan-500 focus:outline-none"
                    />
                  </div>

                  <div className="space-y-1">
                    <label className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">作用范围 (Routing Scope)</label>
                    <div className="w-full rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 font-mono text-xs text-slate-300">
                      All Agents / Global Fallback
                    </div>
                  </div>

                  <div className="space-y-1">
                    <label className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">全局规则内容 (Rule Content) *</label>
                    <textarea
                      rows={4}
                      required
                      value={formConditions}
                      onChange={(event) => setFormConditions(event.target.value)}
                      placeholder="请输入统一分流、过滤、兜底处置的全局策略内容..."
                      className="w-full rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 font-mono text-xs text-white placeholder-slate-700 focus:border-cyan-500 focus:outline-none"
                    />
                  </div>

                  <div className="space-y-1">
                    <label className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-500">规则状态 (Status)</label>
                    <select
                      value={formStatus}
                      onChange={(event) => setFormStatus(event.target.value as 'Enabled' | 'Disabled')}
                      className="w-full rounded border border-slate-800 bg-[#020408] px-2.5 py-1.5 font-mono text-xs text-cyan-400 focus:border-cyan-500 focus:outline-none"
                    >
                      <option value="Enabled">Enabled (启用)</option>
                      <option value="Disabled">Disabled (停用)</option>
                    </select>
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
                    {submitting ? 'Saving...' : '保存规则'}
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
