import { useEffect, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { AlertTriangle, Check, ChevronDown, ChevronRight, FlaskConical, Plus, RefreshCw, Save, ShieldCheck, Trash2 } from 'lucide-react';

import { ApiError, fetchJSON, getApiErrorMessage } from '../lib/api';
import type {
  RbacRule,
  RbacRuleRole,
  RbacRuleUpdateResponse,
  RbacRulesResponse,
} from '../types';

interface RbacRulesManagerProps {
  onAuthExpired?: () => void;
}

interface ParameterDraft {
  id: string;
  name: string;
  pattern: string;
  testText: string;
  testStatus: 'idle' | 'testing' | 'matched' | 'unmatched' | 'error';
  testMessage: string;
}

interface ParameterRuleDraft {
  id: string;
  parameters: ParameterDraft[];
}

interface ToolDraft {
  id: string;
  name: string;
  rules: ParameterRuleDraft[];
}

interface RoleDraft {
  rule: RbacRule;
  tools: ToolDraft[];
}

const ROLE_OPTIONS: RbacRuleRole[] = ['admin', 'operator', 'user'];
const ROLE_LABELS: Record<RbacRuleRole, string> = {
  admin: 'Admin',
  operator: 'Operator',
  user: 'User',
};

const EMPTY_RULE: RbacRule = {
  summary: '',
  prompt_constraints: [],
  allow_tools: null,
  denied_tools: [],
  tools_paras: {},
};

function createId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function createParameter(name = '', pattern = ''): ParameterDraft {
  return {
    id: createId('parameter'),
    name,
    pattern,
    testText: '',
    testStatus: 'idle',
    testMessage: '',
  };
}

function createParameterRule(): ParameterRuleDraft {
  return {
    id: createId('rule'),
    parameters: [createParameter()],
  };
}

function normalizeToolRules(
  rules: Array<Record<string, string>> | Record<string, string>,
): Array<Record<string, string>> {
  return Array.isArray(rules) ? rules : [rules];
}

function toEditorState(rule: RbacRule): RoleDraft {
  return {
    rule: {
      summary: rule.summary,
      prompt_constraints: [...rule.prompt_constraints],
      allow_tools: rule.allow_tools ? [...rule.allow_tools] : null,
      denied_tools: [...rule.denied_tools],
      tools_paras: Object.fromEntries(
        Object.entries(rule.tools_paras).map(([tool, rules]) => [
          tool,
          normalizeToolRules(rules).map((parameters) => ({ ...parameters })),
        ]),
      ),
    },
    tools: Object.entries(rule.tools_paras).map(([name, rawRules]) => ({
      id: createId('tool'),
      name,
      rules: normalizeToolRules(rawRules).map((parameters) => ({
        id: createId('rule'),
        parameters: Object.entries(parameters).map(([parameter, pattern]) => createParameter(parameter, pattern)),
      })),
    })),
  };
}

function toPersistedRule(editor: RoleDraft): RbacRule {
  return {
    summary: editor.rule.summary,
    prompt_constraints: [...editor.rule.prompt_constraints],
    allow_tools: editor.rule.allow_tools ? [...editor.rule.allow_tools] : null,
    denied_tools: [...editor.rule.denied_tools],
    tools_paras: Object.fromEntries(
      editor.tools.map((tool) => [
        tool.name.trim(),
        tool.rules.map((rule) => Object.fromEntries(
          rule.parameters.map((parameter) => [parameter.name.trim(), parameter.pattern]),
        )),
      ]),
    ),
  };
}

function cloneRules(rules: Record<RbacRuleRole, RbacRule>): Record<RbacRuleRole, RbacRule> {
  return Object.fromEntries(
    ROLE_OPTIONS.map((role) => [role, toPersistedRule(toEditorState(rules[role] || EMPTY_RULE))]),
  ) as Record<RbacRuleRole, RbacRule>;
}

function formatApiError(error: unknown, fallback: string): string {
  const message = getApiErrorMessage(error, fallback);
  try {
    const payload = JSON.parse(message) as { detail?: unknown };
    return typeof payload.detail === 'string' ? payload.detail : message;
  } catch {
    return message;
  }
}

export default function RbacRulesManager({ onAuthExpired }: RbacRulesManagerProps) {
  const [activeRole, setActiveRole] = useState<RbacRuleRole>('admin');
  const [drafts, setDrafts] = useState<Partial<Record<RbacRuleRole, RoleDraft>>>({});
  const [savedRules, setSavedRules] = useState<Partial<Record<RbacRuleRole, RbacRule>>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [expandedToolIds, setExpandedToolIds] = useState<Set<string>>(() => new Set());
  const roleTabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const activeDraft = drafts[activeRole] || toEditorState(EMPTY_RULE);
  const dirtyRoles = useMemo(
    () => ROLE_OPTIONS.filter((role) => {
      const saved = savedRules[role];
      const draft = drafts[role];
      if (!saved || !draft) return false;
      return JSON.stringify(saved) !== JSON.stringify(toPersistedRule(draft));
    }),
    [drafts, savedRules],
  );
  const isDirty = dirtyRoles.includes(activeRole);

  async function loadRules() {
    if (dirtyRoles.length > 0 && !window.confirm('Discard unsaved RBAC rule changes and reload from the server?')) {
      return;
    }
    setLoading(true);
    setError('');
    setNotice('');
    try {
      const response = await fetchJSON<RbacRulesResponse>('/api/rbac-rules');
      const normalized = cloneRules(response.rules);
      setSavedRules(normalized);
      setDrafts(Object.fromEntries(ROLE_OPTIONS.map((role) => [role, toEditorState(normalized[role])])));
      setExpandedToolIds(new Set());
    } catch (loadError) {
      if (loadError instanceof ApiError && loadError.status === 401) {
        onAuthExpired?.();
        return;
      }
      setError(formatApiError(loadError, 'Unable to load RBAC rules.'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadRules();
  }, []);

  function updateActiveDraft(updater: (current: RoleDraft) => RoleDraft) {
    setDrafts((current) => ({
      ...current,
      [activeRole]: updater(current[activeRole] || toEditorState(EMPTY_RULE)),
    }));
    setNotice('');
  }

  function handleRoleTabKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, role: RbacRuleRole) {
    const currentIndex = ROLE_OPTIONS.indexOf(role);
    let nextIndex = currentIndex;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = (currentIndex + 1) % ROLE_OPTIONS.length;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = (currentIndex - 1 + ROLE_OPTIONS.length) % ROLE_OPTIONS.length;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = ROLE_OPTIONS.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    const nextRole = ROLE_OPTIONS[nextIndex];
    setActiveRole(nextRole);
    roleTabRefs.current[nextIndex]?.focus();
  }

  function updateRuleField<K extends keyof RbacRule>(field: K, value: RbacRule[K]) {
    updateActiveDraft((current) => ({ ...current, rule: { ...current.rule, [field]: value } }));
  }

  function updateStringList(field: 'prompt_constraints' | 'denied_tools' | 'allow_tools', index: number, value: string) {
    const current = activeDraft.rule[field] || [];
    updateRuleField(field, current.map((item, itemIndex) => itemIndex === index ? value : item) as RbacRule[typeof field]);
  }

  function removeStringListItem(field: 'prompt_constraints' | 'denied_tools' | 'allow_tools', index: number) {
    const current = activeDraft.rule[field] || [];
    updateRuleField(field, current.filter((_item, itemIndex) => itemIndex !== index) as RbacRule[typeof field]);
  }

  function addStringListItem(field: 'prompt_constraints' | 'denied_tools' | 'allow_tools') {
    const current = activeDraft.rule[field] || [];
    updateRuleField(field, [...current, ''] as RbacRule[typeof field]);
  }

  function updateTool(toolId: string, value: string) {
    updateActiveDraft((current) => ({
      ...current,
      tools: current.tools.map((tool) => tool.id === toolId ? { ...tool, name: value } : tool),
    }));
  }

  function removeTool(toolId: string) {
    updateActiveDraft((current) => ({ ...current, tools: current.tools.filter((tool) => tool.id !== toolId) }));
    setExpandedToolIds((current) => {
      if (!current.has(toolId)) return current;
      const next = new Set(current);
      next.delete(toolId);
      return next;
    });
  }

  function toggleTool(toolId: string) {
    setExpandedToolIds((current) => {
      const next = new Set(current);
      if (next.has(toolId)) {
        next.delete(toolId);
      } else {
        next.add(toolId);
      }
      return next;
    });
  }

  function addTool() {
    updateActiveDraft((current) => ({
      ...current,
      tools: [...current.tools, { id: createId('tool'), name: '', rules: [createParameterRule()] }],
    }));
  }

  function addRule(toolId: string) {
    updateActiveDraft((current) => ({
      ...current,
      tools: current.tools.map((tool) => tool.id !== toolId ? tool : {
        ...tool,
        rules: [...tool.rules, createParameterRule()],
      }),
    }));
  }

  function removeRule(toolId: string, ruleId: string) {
    updateActiveDraft((current) => ({
      ...current,
      tools: current.tools.map((tool) => tool.id !== toolId ? tool : {
        ...tool,
        rules: tool.rules.filter((rule) => rule.id !== ruleId),
      }),
    }));
  }

  function updateParameter(toolId: string, ruleId: string, parameterId: string, field: 'name' | 'pattern' | 'testText', value: string) {
    updateActiveDraft((current) => ({
      ...current,
      tools: current.tools.map((tool) => tool.id !== toolId ? tool : {
        ...tool,
        rules: tool.rules.map((rule) => rule.id !== ruleId ? rule : {
          ...rule,
          parameters: rule.parameters.map((parameter) => parameter.id !== parameterId ? parameter : {
            ...parameter,
            [field]: value,
            ...(field === 'pattern' || field === 'testText' ? { testStatus: 'idle', testMessage: '' } : {}),
          }),
        }),
      }),
    }));
  }

  function removeParameter(toolId: string, ruleId: string, parameterId: string) {
    updateActiveDraft((current) => ({
      ...current,
      tools: current.tools.map((tool) => tool.id !== toolId ? tool : {
        ...tool,
        rules: tool.rules.map((rule) => rule.id !== ruleId ? rule : {
          ...rule,
          parameters: rule.parameters.filter((parameter) => parameter.id !== parameterId),
        }),
      }),
    }));
  }

  function addParameter(toolId: string, ruleId: string) {
    updateActiveDraft((current) => ({
      ...current,
      tools: current.tools.map((tool) => tool.id !== toolId ? tool : {
        ...tool,
        rules: tool.rules.map((rule) => rule.id !== ruleId ? rule : {
          ...rule,
          parameters: [...rule.parameters, createParameter()],
        }),
      }),
    }));
  }

  function validateDraft(rule: RbacRule, tools: ToolDraft[]): string | null {
    const toolNames = new Set<string>();
    for (const tool of tools) {
      const toolName = tool.name.trim();
      if (!toolName) return 'Every parameter rule needs a tool name.';
      if (toolNames.has(toolName)) return `Tool “${toolName}” is defined more than once.`;
      toolNames.add(toolName);
      for (const parameterRule of tool.rules) {
        const parameterNames = new Set<string>();
        for (const parameter of parameterRule.parameters) {
          const parameterName = parameter.name.trim();
          if (!parameterName) return `Every rule under “${toolName}” needs a parameter name.`;
          if (parameterNames.has(parameterName)) return `Parameter “${parameterName}” is defined more than once in a rule under “${toolName}”.`;
          parameterNames.add(parameterName);
        }
      }
    }
    const lists: Array<[string, string[]]> = [
      ['prompt constraint', rule.prompt_constraints],
      ['denied tool', rule.denied_tools],
      ...(rule.allow_tools ? [['allowed tool', rule.allow_tools] as [string, string[]]] : []),
    ];
    for (const [label, values] of lists) {
      if (values.some((value) => !value.trim())) return `Every ${label} entry must contain text.`;
    }
    return null;
  }

  async function saveActiveRole() {
    const nextRule = toPersistedRule(activeDraft);
    const validationError = validateDraft(nextRule, activeDraft.tools);
    if (validationError) {
      setError(validationError);
      return;
    }
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const response = await fetchJSON<RbacRuleUpdateResponse>(`/api/rbac-rules/${activeRole}`, {
        method: 'PUT',
        body: JSON.stringify(nextRule),
      });
      const nextSaved = cloneRules({
        admin: savedRules.admin || EMPTY_RULE,
        operator: savedRules.operator || EMPTY_RULE,
        user: savedRules.user || EMPTY_RULE,
        [activeRole]: response.rule,
      });
      setSavedRules(nextSaved);
      setDrafts((current) => ({ ...current, [activeRole]: toEditorState(response.rule) }));
      setNotice(response.restart_required ? 'Saved. Restart Hermes/Aegis for the rule file to take effect.' : 'Saved.');
    } catch (saveError) {
      if (saveError instanceof ApiError && saveError.status === 401) {
        onAuthExpired?.();
        return;
      }
      setError(formatApiError(saveError, 'Unable to save RBAC rules.'));
    } finally {
      setSaving(false);
    }
  }

  async function testParameter(toolId: string, ruleId: string, parameterId: string) {
    const tool = activeDraft.tools.find((item) => item.id === toolId);
    const parameter = tool?.rules
      .find((rule) => rule.id === ruleId)
      ?.parameters.find((item) => item.id === parameterId);
    if (!parameter) return;
    updateActiveDraft((current) => ({
      ...current,
      tools: current.tools.map((item) => item.id !== toolId ? item : {
        ...item,
        rules: item.rules.map((rule) => rule.id !== ruleId ? rule : {
          ...rule,
          parameters: rule.parameters.map((entry) => entry.id !== parameterId ? entry : {
            ...entry,
            testStatus: 'testing',
            testMessage: '',
          }),
        }),
      }),
    }));
    try {
      const response = await fetchJSON<{ matched: boolean }>('/api/rbac-rules/test', {
        method: 'POST',
        body: JSON.stringify({ pattern: parameter.pattern, text: parameter.testText }),
      });
      updateParameterTestResult(toolId, ruleId, parameterId, response.matched ? 'matched' : 'unmatched', response.matched ? 'Pattern matched.' : 'Pattern did not match.');
    } catch (testError) {
      if (testError instanceof ApiError && testError.status === 401) {
        onAuthExpired?.();
        return;
      }
      updateParameterTestResult(toolId, ruleId, parameterId, 'error', formatApiError(testError, 'Invalid regular expression.'));
    }
  }

  function updateParameterTestResult(
    toolId: string,
    ruleId: string,
    parameterId: string,
    testStatus: ParameterDraft['testStatus'],
    testMessage: string,
  ) {
    setDrafts((current) => {
      const draft = current[activeRole] || toEditorState(EMPTY_RULE);
      return {
        ...current,
        [activeRole]: {
          ...draft,
          tools: draft.tools.map((tool) => tool.id !== toolId ? tool : {
            ...tool,
            rules: tool.rules.map((rule) => rule.id !== ruleId ? rule : {
              ...rule,
              parameters: rule.parameters.map((parameter) => parameter.id !== parameterId ? parameter : { ...parameter, testStatus, testMessage }),
            }),
          }),
        },
      };
    });
  }

  function handleListInput(
    event: ChangeEvent<HTMLInputElement>,
    field: 'prompt_constraints' | 'denied_tools' | 'allow_tools',
    index: number,
  ) {
    updateStringList(field, index, event.target.value);
  }

  return (
    <section aria-labelledby="rbac-rules-heading">
      <header className="aegis-page-content__header aegis-page-content__header--compact">
        <div>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-cyan-400" aria-hidden="true" />
            <h2 id="rbac-rules-heading" className="aegis-page-content__title">RBAC Rules</h2>
          </div>
          <p className="aegis-page-content__description">Edit role prompts, tool access, and parameter regular expressions.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void loadRules()} disabled={loading || saving} className="aegis-btn aegis-btn--secondary inline-flex items-center gap-1.5 px-3 py-2 text-xs">
            <RefreshCw className={loading ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} aria-hidden="true" /> Refresh
          </button>
          <button type="button" onClick={() => void saveActiveRole()} disabled={loading || saving || !isDirty} className="aegis-btn aegis-btn--primary inline-flex items-center gap-1.5 px-3 py-2 text-xs">
            <Save className="h-3.5 w-3.5" aria-hidden="true" /> {saving ? 'Saving…' : 'Save role'}
          </button>
        </div>
      </header>

      {error ? <div role="alert" className="aegis-alert aegis-alert--danger mx-5 mt-4">{error}</div> : null}
      {notice ? <div role="status" className="mx-5 mt-4 border border-emerald-800/60 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-300">{notice}</div> : null}
      {dirtyRoles.length > 0 ? <div role="status" className="mx-5 mt-4 border border-amber-800/60 bg-amber-950/20 px-4 py-3 text-xs text-amber-300">Unsaved changes: {dirtyRoles.map((role) => ROLE_LABELS[role]).join(', ')}. Save each changed role before refreshing.</div> : null}
      <div className="aegis-page-tabs aegis-page-tabs--compact mx-5 mt-3 overflow-x-auto" role="tablist" aria-label="RBAC roles">
        {ROLE_OPTIONS.map((role) => (
          <button
            key={role}
            ref={(element) => { roleTabRefs.current[ROLE_OPTIONS.indexOf(role)] = element; }}
            id={`rbac-${role}-tab`}
            type="button"
            role="tab"
            aria-selected={activeRole === role}
            aria-controls={`rbac-${role}-panel`}
            tabIndex={activeRole === role ? 0 : -1}
            onClick={() => setActiveRole(role)}
            onKeyDown={(event) => handleRoleTabKeyDown(event, role)}
            className="aegis-page-tab shrink-0 font-mono text-xs uppercase tracking-wider"
          >
            {ROLE_LABELS[role]}
          </button>
        ))}
      </div>

      <div id={`rbac-${activeRole}-panel`} role="tabpanel" aria-labelledby={`rbac-${activeRole}-tab`} className="aegis-page-content__body aegis-page-content__body--padded space-y-4">
        {loading ? <div className="py-12 text-center font-mono text-xs text-slate-500">LOADING RBAC RULES…</div> : (
          <>
            <section className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(15rem,0.42fr)]" aria-labelledby="rbac-summary-heading">
              <div className="lg:col-span-2">
                <h3 id="rbac-summary-heading" className="aegis-page-content__title text-base">Role brief</h3>
                <p className="aegis-page-content__description">This description is shown as the role summary.</p>
              </div>
              <textarea aria-label={`${ROLE_LABELS[activeRole]} summary`} value={activeDraft.rule.summary} onChange={(event) => updateRuleField('summary', event.target.value)} className="aegis-page-field min-h-24 w-full resize-y px-3 py-2 text-sm leading-relaxed" />
              <div className="aegis-page-metric self-start">
                <p className="aegis-page-metric__label">Active role</p>
                <p className="mt-1.5 font-mono text-lg font-bold uppercase text-cyan-300">{ROLE_LABELS[activeRole]}</p>
                <p className="mt-1.5 text-xs leading-relaxed text-slate-500">Changes are written to the rules file and require a Hermes/Aegis restart.</p>
              </div>
            </section>

            <StringListEditor
              title="Prompt constraints"
              description="Each entry is preserved as one prompt constraint and may contain multiple lines."
              items={activeDraft.rule.prompt_constraints}
              onAdd={() => addStringListItem('prompt_constraints')}
              onChange={(event, index) => handleListInput(event, 'prompt_constraints', index)}
              onRemove={(index) => removeStringListItem('prompt_constraints', index)}
              emptyMessage="No prompt constraints configured."
            />

            <section className="border-t border-[var(--aegis-border)] pt-4" aria-labelledby="rbac-tools-heading">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 id="rbac-tools-heading" className="aegis-page-content__title text-base">Tool access</h3>
                  <p className="aegis-page-content__description">Denied tools win over allow-list entries. Parameter rules are tested with backend Python regex semantics.</p>
                </div>
              </div>
              <div className="mt-3 grid gap-3 xl:grid-cols-2">
                <StringListEditor
                  title="Allow tools"
                  description="Enable the whitelist to restrict this role to the listed tools."
                  items={activeDraft.rule.allow_tools || []}
                  enabled={activeDraft.rule.allow_tools !== null}
                  onEnabledChange={(enabled) => updateRuleField('allow_tools', enabled ? [] : null)}
                  onAdd={() => addStringListItem('allow_tools')}
                  onChange={(event, index) => handleListInput(event, 'allow_tools', index)}
                  onRemove={(index) => removeStringListItem('allow_tools', index)}
                  emptyMessage="Whitelist disabled or empty."
                />
                <StringListEditor
                  title="Denied tools"
                  description="These tools are always blocked for the selected role."
                  items={activeDraft.rule.denied_tools}
                  onAdd={() => addStringListItem('denied_tools')}
                  onChange={(event, index) => handleListInput(event, 'denied_tools', index)}
                  onRemove={(index) => removeStringListItem('denied_tools', index)}
                  emptyMessage="No denied tools configured."
                />
              </div>
            </section>

            <section className="border-t border-[var(--aegis-border)] pt-4" aria-labelledby="rbac-parameters-heading">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 id="rbac-parameters-heading" className="aegis-page-content__title text-base">Tool parameter constraints</h3>
                  <p className="aegis-page-content__description">Each parameter is checked only when the tool call includes it — a configured parameter missing from the call is skipped, not treated as a failure. Rules use AND logic and values are checked with regular-expression search.</p>
                </div>
                <button type="button" onClick={addTool} className="aegis-btn aegis-btn--secondary inline-flex items-center gap-1.5 px-3 py-2 text-xs"><Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add tool</button>
              </div>
              <div className="mt-3 space-y-2">
                {activeDraft.tools.map((tool, toolIndex) => {
                  const isExpanded = expandedToolIds.has(tool.id);
                  const displayName = tool.name.trim() || `Tool ${toolIndex + 1}`;
                  const parameterPanelId = `rbac-tool-${tool.id}-parameters`;
                  return (
                  <article key={tool.id} className={`rounded-lg border border-[var(--aegis-border)] bg-[var(--aegis-bg)] ${isExpanded ? 'p-3' : 'p-0'}`} aria-label={`${displayName} parameter rules`}>
                    {isExpanded ? <div className="flex flex-wrap items-end gap-2">
                      <button type="button" onClick={() => toggleTool(tool.id)} aria-expanded="true" aria-controls={parameterPanelId} aria-label={`Collapse ${displayName} parameter rules`} className="aegis-btn aegis-btn--ghost aegis-btn--icon mb-1 h-8 w-8 shrink-0">
                        <ChevronDown className="h-4 w-4" aria-hidden="true" />
                      </button>
                      <label className="min-w-0 flex-1 text-[10px] font-mono font-bold uppercase tracking-widest text-slate-500">
                        Tool name
                        <input id={`rbac-tool-${tool.id}`} aria-label={`Parameter rule tool ${toolIndex + 1}`} value={tool.name} onChange={(event) => updateTool(tool.id, event.target.value)} className="aegis-page-field mt-1 w-full px-2.5 py-1.5 font-mono text-sm text-cyan-200" placeholder="terminal" />
                      </label>
                      <button type="button" onClick={() => removeTool(tool.id)} className="aegis-btn aegis-btn--danger inline-flex items-center gap-1.5 px-3 py-2 text-xs"><Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Remove tool</button>
                    </div> : <button type="button" onClick={() => toggleTool(tool.id)} aria-expanded="false" aria-controls={parameterPanelId} aria-label={`Expand ${displayName} parameter rules`} className="flex min-h-12 w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-cyan-950/10 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-cyan-400">
                      <ChevronRight className="h-4 w-4 shrink-0 text-cyan-400" aria-hidden="true" />
                      <span className="min-w-0 truncate font-mono text-sm font-semibold tracking-wide text-cyan-200">{displayName}</span>
                    </button>}
                    <div id={parameterPanelId} hidden={!isExpanded} className="mt-3 space-y-3 border-l border-cyan-900/60 pl-3">
                      {tool.rules.map((rule, ruleIndex) => (
                        <section key={rule.id} aria-labelledby={`rbac-rule-${rule.id}`} className="rounded-md border border-cyan-950/80 bg-cyan-950/10 p-2">
                          <div className="mb-2 flex items-center justify-between gap-2">
                            <h4 id={`rbac-rule-${rule.id}`} className="font-mono text-[10px] font-bold uppercase tracking-widest text-cyan-300">Rule {ruleIndex + 1} <span className="font-normal text-slate-500">(AND)</span></h4>
                            <button type="button" onClick={() => removeRule(tool.id, rule.id)} aria-label={`Remove rule ${toolIndex + 1}.${ruleIndex + 1}`} className="aegis-btn aegis-btn--ghost aegis-btn--icon h-8 w-8"><Trash2 className="h-3.5 w-3.5" /></button>
                          </div>
                          <div className="space-y-2">
                            {rule.parameters.map((parameter, parameterIndex) => (
                              <div key={parameter.id} className="grid gap-2 rounded-md border border-[var(--aegis-border)] p-2 lg:grid-cols-[minmax(8rem,0.45fr)_minmax(12rem,1fr)_minmax(10rem,0.7fr)_auto] lg:items-end">
                                <label className="text-[10px] font-mono font-bold uppercase tracking-widest text-slate-500">
                                  Parameter
                                  <input aria-label={`Parameter ${toolIndex + 1}.${ruleIndex + 1}.${parameterIndex + 1} name`} value={parameter.name} onChange={(event) => updateParameter(tool.id, rule.id, parameter.id, 'name', event.target.value)} className="aegis-page-field mt-1 w-full px-2 py-1.5 font-mono text-xs" placeholder="command" />
                                </label>
                                <label className="text-[10px] font-mono font-bold uppercase tracking-widest text-slate-500">
                                  Regex pattern
                                  <input aria-label={`Parameter ${toolIndex + 1}.${ruleIndex + 1}.${parameterIndex + 1} regex`} value={parameter.pattern} onChange={(event) => updateParameter(tool.id, rule.id, parameter.id, 'pattern', event.target.value)} className="aegis-page-field mt-1 w-full px-2 py-1.5 font-mono text-xs" placeholder="^ls(\\s|$)" spellCheck={false} />
                                </label>
                                <label className="text-[10px] font-mono font-bold uppercase tracking-widest text-slate-500">
                                  Test text
                                  <input aria-label={`Parameter ${toolIndex + 1}.${ruleIndex + 1}.${parameterIndex + 1} test text`} value={parameter.testText} onChange={(event) => updateParameter(tool.id, rule.id, parameter.id, 'testText', event.target.value)} className="aegis-page-field mt-1 w-full px-2 py-1.5 font-mono text-xs" placeholder="ls -la" />
                                </label>
                                <div className="flex items-center gap-2 lg:justify-end">
                                  <button type="button" onClick={() => void testParameter(tool.id, rule.id, parameter.id)} disabled={parameter.testStatus === 'testing'} className="aegis-btn aegis-btn--secondary inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs"><FlaskConical className="h-3.5 w-3.5" aria-hidden="true" /> Test</button>
                                  <button type="button" onClick={() => removeParameter(tool.id, rule.id, parameter.id)} aria-label={`Remove parameter ${toolIndex + 1}.${ruleIndex + 1}.${parameterIndex + 1}`} className="aegis-btn aegis-btn--ghost aegis-btn--icon h-8 w-8"><Trash2 className="h-3.5 w-3.5" /></button>
                                </div>
                                {parameter.testStatus !== 'idle' ? <p className={`lg:col-span-4 flex items-center gap-1.5 text-xs ${parameter.testStatus === 'matched' ? 'text-emerald-300' : parameter.testStatus === 'error' ? 'text-rose-300' : parameter.testStatus === 'testing' ? 'text-cyan-300' : 'text-amber-300'}`} role="status">
                                  {parameter.testStatus === 'matched' ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : parameter.testStatus === 'error' ? <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" /> : null}
                                  {parameter.testStatus === 'testing' ? 'Testing…' : parameter.testMessage}
                                </p> : null}
                              </div>
                            ))}
                          </div>
                          <button type="button" onClick={() => addParameter(tool.id, rule.id)} className="aegis-btn aegis-btn--ghost mt-2 inline-flex items-center gap-1.5 px-2 py-1.5 text-xs"><Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add parameter</button>
                        </section>
                      ))}
                      <button type="button" onClick={() => addRule(tool.id)} className="aegis-btn aegis-btn--secondary inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs"><Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add rule</button>
                    </div>
                  </article>
                  );
                })}
                {activeDraft.tools.length === 0 ? <div className="border border-dashed border-[var(--aegis-border)] px-4 py-6 text-center text-sm text-slate-500">No tool parameter constraints configured.</div> : null}
              </div>
            </section>
          </>
        )}
      </div>
    </section>
  );
}

interface StringListEditorProps {
  title: string;
  description: string;
  items: string[];
  onAdd: () => void;
  onChange: (event: ChangeEvent<HTMLInputElement>, index: number) => void;
  onRemove: (index: number) => void;
  emptyMessage: string;
  enabled?: boolean;
  onEnabledChange?: (enabled: boolean) => void;
}

function StringListEditor({
  title,
  description,
  items,
  onAdd,
  onChange,
  onRemove,
  emptyMessage,
  enabled,
  onEnabledChange,
}: StringListEditorProps) {
  const isToggleable = typeof enabled === 'boolean' && onEnabledChange;
  return (
    <section className="aegis-page-metric" aria-labelledby={`list-editor-${title.toLowerCase().replaceAll(' ', '-')}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 id={`list-editor-${title.toLowerCase().replaceAll(' ', '-')}`} className="aegis-page-content__title text-sm">{title}</h3>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">{description}</p>
        </div>
        {isToggleable ? <label className="flex shrink-0 items-center gap-2 text-xs text-slate-400">
          <input type="checkbox" aria-label={`${title} enabled`} checked={enabled} onChange={(event) => onEnabledChange(event.target.checked)} className="h-4 w-4 accent-cyan-400" /> Enabled
        </label> : null}
      </div>
      {(!isToggleable || enabled) ? <div className="mt-3 space-y-2">
        {items.map((item, index) => (
          <div key={`${title}-${index}`} className="flex items-start gap-2">
            <input type="text" aria-label={`${title} ${index + 1}`} value={item} onChange={(event) => onChange(event, index)} className="aegis-page-field min-w-0 flex-1 px-2.5 py-1.5 font-mono text-xs" />
            <button type="button" aria-label={`Remove ${title} ${index + 1}`} onClick={() => onRemove(index)} className="aegis-btn aegis-btn--ghost aegis-btn--icon mt-1 h-8 w-8 shrink-0"><Trash2 className="h-3.5 w-3.5" aria-hidden="true" /></button>
          </div>
        ))}
        <div className="flex items-center justify-between gap-3 pt-1">
          {items.length === 0 ? <span className="text-xs text-slate-600">{emptyMessage}</span> : <span />}
          <button type="button" onClick={onAdd} className="aegis-btn aegis-btn--ghost inline-flex items-center gap-1.5 px-2 py-1.5 text-xs"><Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add</button>
        </div>
      </div> : <p className="mt-3 text-xs text-slate-600">Whitelist disabled; every tool not denied is eligible for this role.</p>}
    </section>
  );
}
